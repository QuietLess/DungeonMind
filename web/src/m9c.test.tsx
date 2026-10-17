/// <reference types="vite/client" />
// M9c UB1–UB5: lore filters, debounced text filter, Markdown notes, journal order, chat tidy-up.
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CampaignContext } from "./hooks";
import { NoteText } from "./notes";
import { BoardPage } from "./pages/BoardPage";
import { ChatPage } from "./pages/ChatPage";
import type { Campaign, Entry } from "./types";

const api = vi.hoisted(() => ({
  entries: vi.fn(),
  entry: vi.fn(),
  updateEntry: vi.fn(),
  threads: vi.fn(),
  thread: vi.fn(),
  createThread: vi.fn(),
  ask: vi.fn(),
  renameThread: vi.fn(),
  deleteThread: vi.fn(),
}));
vi.mock("./api", async (orig) => ({ ...(await orig<typeof import("./api")>()), api }));

const dmCampaign: Campaign = { id: "c1", name: "Ashvale", dm_id: "u1", role: "dm", created_at: "" };
const playerCampaign: Campaign = { ...dmCampaign, role: "player" };
const user = { id: "u1", username: "dana" };

let nextId = 1;
function entry(over: Partial<Entry>): Entry {
  const id = nextId++;
  return {
    id, board: "lore", character_id: null, author_id: "u1", kind: "npc", title: `Entry ${id}`,
    body: "text", visibility: "private", session: null,
    created_at: `2026-10-${String(id).padStart(2, "0")}T00:00:00Z`,
    updated_at: `2026-10-${String(id).padStart(2, "0")}T00:00:00Z`,
    ...over,
  };
}

function renderAt(ui: React.ReactNode, campaign: Campaign, path: string, route: string) {
  return render(
    <CampaignContext.Provider value={{ campaign, user }}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={route} element={ui} />
          <Route path="/c/:cid/chat/:tid" element={ui} />
        </Routes>
      </MemoryRouter>
    </CampaignContext.Provider>,
  );
}

const titles = () =>
  screen.queryAllByRole("article").map((a) => a.querySelector(".entry-title")?.textContent);

beforeEach(() => {
  vi.resetAllMocks();
  nextId = 1;
  Element.prototype.scrollIntoView = vi.fn() as never;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("UB1 lore filters", () => {
  const lore = () => [
    entry({ title: "Baron Vex", kind: "npc", visibility: "private" }),
    entry({ title: "Elara", kind: "npc", visibility: "party" }),
    entry({ title: "Ashvale", kind: "location", visibility: "party" }),
  ];

  it("kind chips show counts and filter the list", async () => {
    api.entries.mockResolvedValue(lore());
    renderAt(<BoardPage board="lore" />, dmCampaign, "/c/c1/lore", "/c/:cid/lore");
    await screen.findByText("Baron Vex");
    const kinds = screen.getByRole("group", { name: /kind/i });
    const npc = within(kinds).getByRole("button", { name: /NPC\s*2/ });
    expect(within(kinds).getByRole("button", { name: /Location\s*1/ })).toBeInTheDocument();
    expect(within(kinds).queryByRole("button", { name: /Faction/ })).toBeNull();
    await userEvent.click(npc);
    expect(npc).toHaveAttribute("aria-pressed", "true");
    expect(titles()).toEqual(["Baron Vex", "Elara"]);
    await userEvent.click(within(kinds).getByRole("button", { name: /All/ }));
    expect(titles()).toHaveLength(3);
  });

  it("the DM can show only secret or only revealed entries, combined with kind", async () => {
    api.entries.mockResolvedValue(lore());
    renderAt(<BoardPage board="lore" />, dmCampaign, "/c/c1/lore", "/c/:cid/lore");
    await screen.findByText("Baron Vex");
    const shown = screen.getByRole("radiogroup", { name: /show/i }); // L3: a radio group
    await userEvent.click(within(shown).getByRole("radio", { name: "Secret" }));
    expect(titles()).toEqual(["Baron Vex"]);
    await userEvent.click(within(shown).getByRole("radio", { name: "Revealed" }));
    expect(titles()).toEqual(["Elara", "Ashvale"]);
    const kinds = screen.getByRole("group", { name: /kind/i });
    await userEvent.click(within(kinds).getByRole("button", { name: /Location/ }));
    expect(titles()).toEqual(["Ashvale"]);
  });

  it("players don't get the secret/revealed filter", async () => {
    api.entries.mockResolvedValue(lore().filter((e) => e.visibility === "party"));
    renderAt(<BoardPage board="lore" />, playerCampaign, "/c/c1/lore", "/c/:cid/lore");
    await screen.findByText("Elara");
    expect(screen.queryByRole("radiogroup", { name: /show/i })).toBeNull();
  });
});

describe("UB2 debounced text filter", () => {
  it("asks the server once, 300 ms after typing stops", async () => {
    vi.useFakeTimers();
    api.entries.mockResolvedValue([]);
    renderAt(<BoardPage board="journal" />, dmCampaign, "/c/c1/journal", "/c/:cid/journal");
    await act(async () => {});
    expect(api.entries).toHaveBeenCalledTimes(1);
    const box = screen.getByRole("searchbox");
    for (const value of ["b", "ba", "bar"]) fireEvent.change(box, { target: { value } });
    await act(async () => {
      vi.advanceTimersByTime(299);
    });
    expect(api.entries).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    expect(api.entries).toHaveBeenCalledTimes(2);
    expect(api.entries).toHaveBeenLastCalledWith("c1", { board: "journal", q: "bar" });
  });
});

describe("UB3 notes render as safe Markdown", () => {
  it("renders headings, emphasis, lists, quotes and code", () => {
    const { container } = render(
      <NoteText
        text={"# Baron Vex\n\n**status:** alive, *wary*\n\n- one\n- two\n\n1. first\n2. second\n\n> a rumour\n\nUse `the key`."}
      />,
    );
    expect(screen.getByRole("heading", { level: 3, name: "Baron Vex" })).toBeInTheDocument();
    expect(container.querySelector("strong")).toHaveTextContent("status:");
    expect(container.querySelector("em")).toHaveTextContent("wary");
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(container.querySelectorAll("ol li")).toHaveLength(2);
    expect(container.querySelector("blockquote")).toHaveTextContent("a rumour");
    expect(container.querySelector("code")).toHaveTextContent("the key");
  });

  it("never produces links, images or raw HTML", () => {
    const { container } = render(
      <NoteText
        text={'See [map](https://evil.example) and ![x](https://evil.example/p.png)\n\n<img src=x onerror="alert(1)"> https://evil.example'}
      />,
    );
    expect(container.querySelector("a, img, script, iframe")).toBeNull();
    expect(container.textContent).toContain("https://evil.example");
    expect(container.textContent).toContain("<img");
  });
});

describe("UB4 journal order", () => {
  it("lists by session, newest first, then entries without a session", async () => {
    api.entries.mockResolvedValue([
      entry({ board: "journal", kind: "note", title: "No session, older", visibility: "party" }),
      entry({ board: "journal", kind: "note", title: "Session 1", session: 1, visibility: "party" }),
      entry({ board: "journal", kind: "note", title: "Session 3", session: 3, visibility: "party" }),
      entry({ board: "journal", kind: "note", title: "No session, newer", visibility: "party" }),
      entry({ board: "journal", kind: "note", title: "Session 2", session: 2, visibility: "party" }),
    ]);
    renderAt(<BoardPage board="journal" />, dmCampaign, "/c/c1/journal", "/c/:cid/journal");
    await screen.findByText("Session 3", { selector: ".entry-title" });
    expect(titles()).toEqual([
      "Session 3", "Session 2", "Session 1", "No session, newer", "No session, older",
    ]);
  });
});

describe("UB5 chat tidy-up", () => {
  beforeEach(() => {
    api.threads.mockResolvedValue([
      { id: 5, title: "Grappling", created_at: "" },
      { id: 6, title: "Elara", created_at: "" },
    ]);
    api.thread.mockResolvedValue({ thread: { id: 5, title: "Grappling", created_at: "" }, messages: [] });
  });

  it("renames a thread", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("Grapple rules");
    api.renameThread.mockResolvedValue({ id: 5, title: "Grapple rules", created_at: "" });
    renderAt(<ChatPage />, playerCampaign, "/c/c1/chat", "/c/:cid/chat");
    await userEvent.click(await screen.findByRole("button", { name: "Rename Grappling" }));
    expect(api.renameThread).toHaveBeenCalledWith("c1", 5, "Grapple rules");
    expect(api.threads).toHaveBeenCalledTimes(2);
  });

  it("deleting the open thread asks first and goes back to the start page", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    api.deleteThread.mockResolvedValue(undefined);
    renderAt(<ChatPage />, playerCampaign, "/c/c1/chat/5", "/c/:cid/chat");
    const del = await screen.findByRole("button", { name: "Delete Grappling" });
    await userEvent.click(del);
    expect(api.deleteThread).not.toHaveBeenCalled();
    await userEvent.click(del);
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(api.deleteThread).toHaveBeenCalledWith("c1", 5);
    expect(await screen.findByRole("heading", { name: /Ask about Ashvale/ })).toBeInTheDocument();
  });
});

describe("M9c review fixes", () => {
  it("R1: lore filters don't carry over to the journal", async () => {
    const loreEntries = [
      entry({ title: "Baron Vex", visibility: "private" }),
      entry({ title: "Elara", visibility: "party" }),
    ];
    const journal = [
      entry({ board: "journal", kind: "note", title: "J-private", visibility: "private" }),
      entry({ board: "journal", kind: "note", title: "J-party", visibility: "party" }),
    ];
    api.entries.mockImplementation((_cid: string, f: { board: string }) =>
      Promise.resolve(f.board === "lore" ? loreEntries : journal),
    );
    const { rerender } = render(
      <CampaignContext.Provider value={{ campaign: dmCampaign, user }}>
        <MemoryRouter>
          <BoardPage board="lore" />
        </MemoryRouter>
      </CampaignContext.Provider>,
    );
    await screen.findByText("Baron Vex");
    await userEvent.click(screen.getByRole("radio", { name: "Revealed" }));
    rerender(
      <CampaignContext.Provider value={{ campaign: dmCampaign, user }}>
        <MemoryRouter>
          <BoardPage board="journal" />
        </MemoryRouter>
      </CampaignContext.Provider>,
    );
    expect(await screen.findByText("J-private")).toBeInTheDocument();
    expect(screen.getByText("J-party")).toBeInTheDocument();
  });

  it("R2: a selected kind stays visible (count 0) when other filters empty it", async () => {
    api.entries.mockResolvedValue([
      entry({ title: "Baron Vex", kind: "npc", visibility: "private" }),
      entry({ title: "Ashvale", kind: "location", visibility: "party" }),
    ]);
    renderAt(<BoardPage board="lore" />, dmCampaign, "/c/c1/lore", "/c/:cid/lore");
    await screen.findByText("Ashvale");
    const kinds = screen.getByRole("group", { name: /kind/i });
    await userEvent.click(within(kinds).getByRole("button", { name: /Location/ }));
    await userEvent.click(screen.getByRole("radio", { name: "Secret" }));
    const location = within(kinds).getByRole("button", { name: /Location\s*0/ });
    expect(location).toHaveAttribute("aria-pressed", "true");
  });

  it("R3: a numbered list keeps its first number (Turkish ordinals)", () => {
    const { container } = render(<NoteText text={"3. oturumda Baron öldü.\n4. oturumda kaçtık."} />);
    expect(container.querySelector("ol")).toHaveAttribute("start", "3");
  });

  it("R11: no italics inside words", () => {
    const { container } = render(<NoteText text={"file_name_here and 2*3*4 but *this* and _that_"} />);
    expect([...container.querySelectorAll("em")].map((e) => e.textContent)).toEqual(["this", "that"]);
  });

  it("R5: the open chat can be renamed and deleted from the phone picker too", async () => {
    api.threads.mockResolvedValue([{ id: 5, title: "Grappling", created_at: "" }]);
    api.thread.mockResolvedValue({ thread: { id: 5, title: "Grappling", created_at: "" }, messages: [] });
    renderAt(<ChatPage />, playerCampaign, "/c/c1/chat/5", "/c/:cid/chat");
    expect(await screen.findByRole("button", { name: "Rename this chat" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete this chat" })).toBeInTheDocument();
  });

  it("R7: after deleting a chat, focus goes to New chat", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.threads.mockResolvedValue([{ id: 6, title: "Elara", created_at: "" }]);
    api.deleteThread.mockResolvedValue(undefined);
    renderAt(<ChatPage />, playerCampaign, "/c/c1/chat", "/c/:cid/chat");
    await userEvent.click(await screen.findByRole("button", { name: "Delete Elara" }));
    expect(screen.getByRole("button", { name: /New chat/ })).toHaveFocus();
  });
});

describe("M9c re-review fixes", () => {
  it("notes use no regex lookbehind (Safari before 16.4 can't parse it)", async () => {
    const source = await import("./notes.tsx?raw");
    expect(source.default).not.toMatch(/\(\?<[!=]/);
  });

  it("emphasis still works at word edges and in Turkish", () => {
    const { container } = render(<NoteText text={"(*x*) _Ağrı_, *ş* and word*s*"} />);
    expect([...container.querySelectorAll("em")].map((e) => e.textContent)).toEqual(["x", "Ağrı", "ş"]);
    expect(container.textContent).toBe("(x) Ağrı, ş and word*s*");
  });
});
