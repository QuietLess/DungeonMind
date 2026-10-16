// M7 frontend review fixes: failures are shown where the user is, and nothing is sent twice.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CampaignContext } from "./hooks";
import { CampaignsPage } from "./pages/CampaignsPage";
import { CharactersPage } from "./pages/CharactersPage";
import { EntryPanel } from "./components/EntryPanel";
import { ApiError } from "./api";
import type { Campaign, Entry } from "./types";

const api = vi.hoisted(() => ({
  characters: vi.fn(), createCharacter: vi.fn(), entries: vi.fn(), entry: vi.fn(),
  deleteEntry: vi.fn(), campaigns: vi.fn(), createCampaign: vi.fn(), acceptInvite: vi.fn(),
  logout: vi.fn(),
}));
vi.mock("./api", async (orig) => ({ ...(await orig<typeof import("./api")>()), api }));

const campaign: Campaign = { id: "c1", name: "Ashvale", dm_id: "dm", role: "player", created_at: "" };
const user = { id: "u1", username: "mia" };
const entry: Entry = {
  id: 4, board: "journal", character_id: null, author_id: "u1", kind: "note", title: "Recap",
  body: "We met the baron.", visibility: "party", session: 1, created_at: "", updated_at: "",
};

function inCampaign(ui: React.ReactNode, path = "/c/c1/characters") {
  return render(
    <CampaignContext.Provider value={{ campaign, user }}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/c/:cid/characters" element={ui} />
          <Route path="/c/:cid/characters/:chid" element={ui} />
          <Route path="/" element={ui} />
        </Routes>
      </MemoryRouter>
    </CampaignContext.Provider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  api.characters.mockResolvedValue([]);
  api.entries.mockResolvedValue([]);
  api.entry.mockResolvedValue(entry);
  api.campaigns.mockResolvedValue([]);
});

describe("form feedback", () => {
  it("F1: a failed character creation shows its error inside the open form", async () => {
    api.createCharacter.mockRejectedValue(new ApiError(422, "Character name must be 1–60 characters."));
    inCampaign(<CharactersPage />);
    await userEvent.click(screen.getByRole("button", { name: /new character/i }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(screen.getByLabelText("Name"), "Thorn");
    await userEvent.click(screen.getByRole("button", { name: "Create character" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("1–60 characters");
    expect(dialog).toContainElement(screen.getByRole("alert"));
  });

  it("F2: double-clicking create sends one request", async () => {
    let finish!: () => void;
    api.createCharacter.mockImplementation(
      () => new Promise((resolve) => { finish = () => resolve({ id: 1, owner_id: "u1", name: "Thorn", summary: "" }); }),
    );
    inCampaign(<CharactersPage />);
    await userEvent.click(screen.getByRole("button", { name: /new character/i }));
    await userEvent.type(screen.getByLabelText("Name"), "Thorn");
    const create = screen.getByRole("button", { name: "Create character" });
    await userEvent.dblClick(create);
    finish();
    expect(api.createCharacter).toHaveBeenCalledOnce();
  });

  it("F2: double-submitting a new campaign sends one request", async () => {
    api.createCampaign.mockImplementation(() => new Promise(() => {}));
    inCampaign(<CampaignsPage />, "/");
    await userEvent.type(screen.getByLabelText("Campaign name"), "Ashvale");
    await userEvent.dblClick(screen.getByRole("button", { name: /create as dm/i }));
    expect(api.createCampaign).toHaveBeenCalledOnce();
  });

  it("F3: a failed delete keeps the panel open and shows why", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.deleteEntry.mockRejectedValue(new ApiError(403, "Only the author can delete an entry."));
    const onClose = vi.fn();
    inCampaign(<EntryPanel entryId={4} onClose={onClose} />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only the author");
    expect(onClose).not.toHaveBeenCalled();
  });
});
