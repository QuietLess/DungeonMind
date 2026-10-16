// Regression (found in the browser walk-through): the first question of a new chat vanished,
// because creating the thread changed the URL and the page reloaded the (still empty) thread.
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CampaignContext } from "./hooks";
import { ChatPage } from "./pages/ChatPage";
import type { Campaign, Message } from "./types";

const api = vi.hoisted(() => ({
  threads: vi.fn(),
  createThread: vi.fn(),
  thread: vi.fn(),
  ask: vi.fn(),
  entry: vi.fn(),
}));
vi.mock("./api", async (orig) => ({ ...(await orig<typeof import("./api")>()), api }));

const campaign: Campaign = {
  id: "c1", name: "Ashvale", dm_id: "dm", role: "player", created_at: "2026-10-16T00:00:00Z",
};
const reply: Message = {
  id: 2, role: "assistant", text: "Elara is a spy [N1].", created_at: "2026-10-16T00:00:01Z",
  citations: [{ id: "N1", kind: "note", label: "Lore › NPC › Elara", entry_id: 7 }],
  confidence: "high", insufficient_evidence: false, stop_reason: "completed",
};

function renderChat() {
  return render(
    <CampaignContext.Provider value={{ campaign, user: { id: "u1", username: "mia" } }}>
      <MemoryRouter initialEntries={["/c/c1/chat"]}>
        <Routes>
          <Route path="/c/:cid/chat" element={<ChatPage />} />
          <Route path="/c/:cid/chat/:tid" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    </CampaignContext.Provider>,
  );
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn(() => Promise.resolve()) as never;
  api.threads.mockResolvedValue([]);
  api.createThread.mockResolvedValue({ id: 5, title: "Who is Elara?", created_at: "" });
  // Real network timing: the thread reload lands while the answer is still being written.
  const later = <T,>(value: T, ms: number) =>
    new Promise<T>((resolve) => setTimeout(() => resolve(value), ms));
  api.thread.mockImplementation(() =>
    later({ thread: { id: 5, title: "", created_at: "" }, messages: [] }, 30),
  );
  api.ask.mockImplementation(() => later(reply, 80));
});

describe("ChatPage", () => {
  it("keeps the first question of a new chat and shows the cited answer", async () => {
    renderChat();
    await userEvent.type(screen.getByLabelText("Your question"), "Who is Elara?{Enter}");
    expect(await screen.findByRole("button", { name: /N1: Lore › NPC › Elara/ })).toBeInTheDocument();
    expect(screen.getByText("Who is Elara?", { selector: ".message-user p" })).toBeInTheDocument();
    expect(api.ask).toHaveBeenCalledWith("c1", 5, "Who is Elara?");
  });

  it("announces the answer to screen readers", async () => {
    renderChat();
    await userEvent.type(screen.getByLabelText("Your question"), "Who is Elara?{Enter}");
    await screen.findByRole("button", { name: /N1/ });
    const live = document.querySelector("[aria-live='polite']");
    expect(live?.textContent).toContain("Elara is a spy");
  });
});

// --- M7 frontend review fixes (C1–C5) --------------------------------------------------------

const older: Message = {
  id: 90, role: "user", text: "An older question", created_at: "", citations: [],
  confidence: null, insufficient_evidence: null, stop_reason: null,
};

function threadsWithOlder() {
  api.threads.mockResolvedValue([
    { id: 5, title: "Who is Elara?", created_at: "" },
    { id: 9, title: "Older chat", created_at: "" },
  ]);
  api.thread.mockImplementation(async (_cid: string, tid: number) => ({
    thread: { id: tid, title: "", created_at: "" },
    messages: tid === 9 ? [older] : [
      { ...older, id: 1, text: "Who is Elara?" },
      reply,
    ],
  }));
}

describe("ChatPage thread switching", () => {
  it("C1: after starting a chat, switching away and back shows each thread's own messages", async () => {
    threadsWithOlder();
    renderChat();
    await userEvent.type(screen.getByLabelText("Your question"), "Who is Elara?{Enter}");
    await screen.findByRole("button", { name: /N1/ });
    await userEvent.click(await screen.findByRole("button", { name: "Older chat" }));
    expect(await screen.findByText("An older question")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /N1/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Who is Elara?" }));
    expect(await screen.findByRole("button", { name: /N1/ })).toBeInTheDocument();
    expect(screen.queryByText("An older question")).toBeNull();
  });

  it("C2: an answer that arrives after switching threads isn't shown in the other thread", async () => {
    threadsWithOlder();
    let finish!: (m: Message) => void;
    api.ask.mockImplementation(() => new Promise<Message>((resolve) => { finish = resolve; }));
    renderChat();
    await userEvent.type(screen.getByLabelText("Your question"), "Who is Elara?{Enter}");
    await userEvent.click(await screen.findByRole("button", { name: "Older chat" }));
    await screen.findByText("An older question");
    finish(reply);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("button", { name: /N1/ })).toBeNull();
  });

  it("C3: a compact chat picker works where the side list is hidden (phones)", async () => {
    threadsWithOlder();
    renderChat();
    const picker = await screen.findByRole("combobox", { name: /chat/i });
    await screen.findByRole("option", { name: "Older chat" });
    await userEvent.selectOptions(picker, "9");
    expect(await screen.findByText("An older question")).toBeInTheDocument();
  });

  it("C4: Enter while an input method is composing doesn't send", () => {
    renderChat();
    const box = screen.getByLabelText("Your question");
    fireEvent.change(box, { target: { value: "日本" } });
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    expect(api.createThread).not.toHaveBeenCalled();
  });

  it("C5: the conversation is a log region", () => {
    renderChat();
    expect(screen.getByRole("log", { name: /conversation/i })).toBeInTheDocument();
  });
});
