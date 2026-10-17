// UI contract L1–L6: layout for real campaigns.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { plainExcerpt } from "./components/EntryCard";
import { CampaignContext } from "./hooks";
import { suggestions } from "./pages/ChatPage";
import { BoardPage } from "./pages/BoardPage";
import { OverviewPage } from "./pages/OverviewPage";
import type { Campaign, Entry } from "./types";

const api = vi.hoisted(() => ({
  entries: vi.fn(), entry: vi.fn(), characters: vi.fn(), members: vi.fn(),
  campaigns: vi.fn(), updateEntry: vi.fn(),
}));
vi.mock("./api", async (orig) => ({ ...(await orig<typeof import("./api")>()), api }));

const dm: Campaign = { id: "c1", name: "Hakikatin Parmağı", dm_id: "u1", role: "dm", created_at: "2026-10-01T00:00:00Z" };
const player: Campaign = { ...dm, role: "player" };
const user = { id: "u1", username: "dana" };
let n = 0;
const entry = (over: Partial<Entry>): Entry => {
  n += 1;
  return {
    id: n, board: "lore", character_id: null, author_id: "u1", kind: "npc", title: `E${n}`,
    body: "text", visibility: "private", session: null,
    created_at: `2026-10-${String(n).padStart(2, "0")}T00:00:00Z`,
    updated_at: `2026-10-${String(n).padStart(2, "0")}T00:00:00Z`, ...over,
  };
};

function inCampaign(ui: React.ReactNode, campaign = dm, path = "/c/c1/overview") {
  return render(
    <CampaignContext.Provider value={{ campaign, user }}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/c/:cid/*" element={ui} />
        </Routes>
      </MemoryRouter>
    </CampaignContext.Provider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  n = 0;
  api.characters.mockResolvedValue([{ id: 1, owner_id: "u2", name: "Mira", summary: "" }]);
  api.members.mockResolvedValue([
    { user_id: "u1", username: "dana", role: "dm" },
    { user_id: "u2", username: "mia", role: "player" },
  ]);
});

describe("L2 plain previews", () => {
  it("removes Markdown markers", () => {
    expect(plainExcerpt("# Madalyon\n\n**status:** alive\n\n## Bilinenler\n- Sahibi *Erius*.\n> söylenti `kod`"))
      .toBe("Madalyon status: alive Bilinenler Sahibi Erius. söylenti kod");
  });
});

describe("L1/L3 lore board", () => {
  it("one grid ordered by kind, kind badges, no per-kind sections", async () => {
    api.entries.mockResolvedValue([
      entry({ title: "Zindar", kind: "location", visibility: "party" }),
      entry({ title: "Erius", kind: "npc" }),
      entry({ title: "Alaric", kind: "npc" }),
    ]);
    inCampaign(<BoardPage board="lore" />, dm, "/c/c1/lore");
    await screen.findByText("Zindar");
    const cards = screen.getAllByRole("article");
    expect(cards.map((c) => c.querySelector(".entry-title")?.textContent)).toEqual(["Alaric", "Erius", "Zindar"]);
    expect(within(cards[2]).getByText("Location")).toHaveClass("kind-location");
    expect(screen.queryByRole("heading", { level: 2, name: "NPC" })).toBeNull();
  });

  it("secret/revealed is a radio group in the toolbar", async () => {
    api.entries.mockResolvedValue([entry({ title: "Erius" }), entry({ title: "Zindar", visibility: "party" })]);
    inCampaign(<BoardPage board="lore" />, dm, "/c/c1/lore");
    await screen.findByText("Erius");
    const show = screen.getByRole("radiogroup", { name: /show/i });
    await userEvent.click(within(show).getByRole("radio", { name: "Revealed" }));
    expect(within(show).getByRole("radio", { name: "Revealed" })).toBeChecked();
    expect(screen.queryByText("Erius")).toBeNull();
  });
});

describe("L4 overview", () => {
  const all = () => [
    entry({ title: "Erius", kind: "npc" }),
    entry({ title: "Zindar", kind: "location", visibility: "party" }),
    entry({ board: "journal", kind: "note", title: "Oturum 1", session: 1, visibility: "party" }),
    entry({ board: "journal", kind: "note", title: "Oturum 2", session: 2, visibility: "party" }),
  ];

  it("shows counts, secret vs revealed for the DM, latest sessions and recent entries", async () => {
    api.entries.mockResolvedValue(all());
    inCampaign(<OverviewPage />);
    const lore = await screen.findByRole("region", { name: /lore/i });
    expect(lore).toHaveTextContent("2");
    expect(lore).toHaveTextContent(/1 secret/i);
    expect(lore).toHaveTextContent(/1 revealed/i);
    expect(screen.getByRole("region", { name: /members/i })).toHaveTextContent("2");
    const sessions = screen.getByRole("list", { name: /latest sessions/i });
    expect(within(sessions).getAllByRole("listitem")[0]).toHaveTextContent("Oturum 2");
    const recent = screen.getByRole("list", { name: /recently changed/i });
    expect(within(recent).getAllByRole("listitem")[0]).toHaveTextContent("Oturum 2");
    expect(screen.getByRole("link", { name: /ask the chatbot/i })).toHaveAttribute("href", "/c/c1/chat");
    expect(screen.getByRole("link", { name: /new lore entry/i })).toBeInTheDocument();
  });

  it("players see only their own counts and no secret split", async () => {
    api.entries.mockResolvedValue(all().filter((e) => e.visibility === "party"));
    inCampaign(<OverviewPage />, player);
    const lore = await screen.findByRole("region", { name: /lore/i });
    expect(lore).toHaveTextContent("1");
    expect(lore).not.toHaveTextContent(/secret/i);
    expect(screen.queryByRole("link", { name: /new lore entry/i })).toBeNull();
    expect(screen.getByRole("link", { name: /new journal entry/i })).toBeInTheDocument();
  });
});

describe("L5 chat suggestions", () => {
  it("use the campaign's NPCs and latest session", () => {
    const s = suggestions([
      entry({ title: "Erius", kind: "npc" }),
      entry({ board: "journal", kind: "note", title: "Oturum 3", session: 3 }),
    ]);
    expect(s[0]).toBe("What do we know about Erius?");
    expect(s).toContain("Summarise the last session");
    expect(s).toHaveLength(4);
  });

  it("fall back to general questions in an empty campaign", () => {
    const s = suggestions([]);
    expect(s.some((q) => q.includes("baron"))).toBe(false);
    expect(s.length).toBeGreaterThanOrEqual(2);
  });
});

describe("layout review fixes", () => {
  it("previews drop links, tables and rules but keep words with underscores", () => {
    expect(plainExcerpt("See [the map](https://x.com/my_map) for fire_bolt.\n\n---\n| Ad | HP |\n|---|---|\n| Orc | 15 |"))
      .toBe("See the map for fire_bolt. Ad HP Orc 15");
  });

  it("sorts Turkish titles with Turkish rules", async () => {
    api.entries.mockResolvedValue([
      entry({ title: "Çınar" }), entry({ title: "Cem" }), entry({ title: "Dora" }),
    ]);
    inCampaign(<BoardPage board="lore" />, dm, "/c/c1/lore");
    await screen.findByText("Dora");
    expect(screen.getAllByRole("article").map((c) => c.querySelector(".entry-title")?.textContent))
      .toEqual(["Cem", "Çınar", "Dora"]);
  });

  it("the overview refreshes after an entry opened from it changes", async () => {
    api.entries.mockResolvedValueOnce([entry({ title: "Erius" })]).mockResolvedValue([]);
    api.entry.mockResolvedValue(entry({ title: "Erius", id: 99 }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    (api as Record<string, ReturnType<typeof vi.fn>>).deleteEntry = vi.fn().mockResolvedValue(undefined);
    inCampaign(<OverviewPage />);
    const recent = await screen.findByRole("list", { name: /recently changed/i });
    await userEvent.click(within(recent).getByRole("button", { name: /Erius/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByText(/nothing written yet/i)).toBeInTheDocument();
  });

  it("the overview says so when loading fails", async () => {
    const { ApiError } = await import("./api");
    api.entries.mockRejectedValue(new ApiError(500, "Something went wrong on the server."));
    inCampaign(<OverviewPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
  });

  it("quick actions open the new-entry form", async () => {
    api.entries.mockResolvedValue([]);
    inCampaign(<OverviewPage />);
    expect(await screen.findByRole("link", { name: /new lore entry/i })).toHaveAttribute("href", "/c/c1/lore?new=1");
    expect(screen.getByRole("link", { name: /new journal entry/i })).toHaveAttribute("href", "/c/c1/journal?new=1");
  });

  it("the board opens the form for ?new=1", async () => {
    api.entries.mockResolvedValue([]);
    inCampaign(<BoardPage board="journal" />, dm, "/c/c1/journal?new=1");
    expect(await screen.findByRole("dialog", { name: /new journal entry/i })).toBeInTheDocument();
  });
});
