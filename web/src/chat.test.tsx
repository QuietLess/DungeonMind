// Regression (found in the browser walk-through): the first question of a new chat vanished,
// because creating the thread changed the URL and the page reloaded the (still empty) thread.
import { render, screen } from "@testing-library/react";
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
