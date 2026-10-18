// M10c: the character sheet page, its roll buttons, editing, and the game log.
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { GameLog } from "./components/GameLog";
import { CampaignContext } from "./hooks";
import { SheetPage } from "./pages/SheetPage";
import type { Campaign } from "./types";

const api = vi.hoisted(() => ({
  characters: vi.fn(), sheet: vi.fn(), saveSheet: vi.fn(), roll: vi.fn(), rolls: vi.fn(),
}));
vi.mock("./api", async (orig) => ({ ...(await orig<typeof import("./api")>()), api }));

const campaign: Campaign = { id: "c1", name: "Ashvale", dm_id: "dm", role: "player", created_at: "" };
const user = { id: "u1", username: "mia" };

const sheet = {
  classes: [{ name: "Rogue", subclass: "", level: 5, hit_die: 8 }],
  species: "Elf", background: "Criminal", alignment: "", xp: 0,
  abilities: { str: 10, dex: 18, con: 14, int: 12, wis: 13, cha: 8 },
  saves: ["dex", "int"], save_misc: {}, skills: { stealth: "expertise" }, skill_misc: {},
  jack_of_all_trades: false, armor_class: 15, speed: 30, initiative_misc: 0,
  hp: { max: 38, current: 30, temp: 0 }, hit_dice_used: {}, death_saves: { successes: 0, failures: 0 },
  inspiration: false, exhaustion: 0, conditions: [],
  attacks: [{ name: "Rapier", ability: "dex", proficient: true, to_hit_misc: 0, damage: "1d8",
              add_ability_to_damage: true, damage_type: "piercing", notes: "" }],
  spellcasting: { ability: null, slots: [] }, spells: [],
  inventory: [{ name: "Rope", quantity: 1, weight: 5, equipped: false, notes: "" }],
  currency: { cp: 0, sp: 0, ep: 0, gp: 12, pp: 0 }, features: [], proficiencies: "",
  custom_dice: [{ name: "Sneak Attack", expression: "3d6", notes: "" }], notes: "",
};
const derived = {
  level: 5, proficiency_bonus: 3,
  modifiers: { str: 0, dex: 4, con: 2, int: 1, wis: 1, cha: -1 },
  saves: { str: 0, dex: 7, con: 2, int: 4, wis: 1, cha: -1 },
  skills: { acrobatics: 4, animal_handling: 1, arcana: 1, athletics: 0, deception: -1, history: 1,
            insight: 1, intimidation: -1, investigation: 1, medicine: 1, nature: 1, perception: 1,
            performance: -1, persuasion: -1, religion: 1, sleight_of_hand: 4, stealth: 10, survival: 1 },
  passive_perception: 11, initiative: 4, attacks: [{ to_hit: 7, damage: "1d8+4" }],
  spell_save_dc: null, spell_attack: null, hit_dice: { "8": 5 }, carried_weight: 5.24,
  carrying_capacity: 150, d20_penalty: 0, speed: 30,
};
const rollResult = {
  id: 1, label: "Mira — Stealth", author: "mia", character_id: 7, expression: "1d20+10",
  terms: [{ term: "1d20", rolls: [14], kept: [14], subtotal: 14 }], bonus: 10, total: 24,
  natural: 14, outcome: null, mode: "normal" as const, to_dm: false, created_at: "2026-10-18T10:00:00Z",
};

function renderSheet(canEdit = true) {
  api.sheet.mockResolvedValue({ sheet, derived, version: 3, can_edit: canEdit });
  return render(
    <CampaignContext.Provider value={{ campaign, user }}>
      <MemoryRouter initialEntries={["/c/c1/characters/7/sheet"]}>
        <Routes>
          <Route path="/c/:cid/characters/:chid/sheet" element={<SheetPage />} />
        </Routes>
      </MemoryRouter>
    </CampaignContext.Provider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  api.characters.mockResolvedValue([{ id: 7, owner_id: "u1", name: "Mira", summary: "" }]);
  api.rolls.mockResolvedValue([]);
  api.roll.mockResolvedValue(rollResult);
});

afterEach(() => vi.useRealTimers());

describe("sheet view", () => {
  it("shows the derived numbers", async () => {
    renderSheet();
    expect(await screen.findByRole("heading", { name: "Mira" })).toBeInTheDocument();
    expect(screen.getByText(/Rogue 5/)).toBeInTheDocument();
    const ac = screen.getByRole("group", { name: "Armor Class" });
    expect(ac).toHaveTextContent("15");
    expect(screen.getByRole("button", { name: "Stealth +10" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dexterity save +7" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dexterity check +4" })).toBeInTheDocument();
  });

  it("every bonus rolls on the server with the chosen mode", async () => {
    renderSheet();
    await userEvent.click(await screen.findByRole("button", { name: "Stealth +10" }));
    expect(api.roll).toHaveBeenLastCalledWith("c1", {
      character_id: 7, kind: "skill", key: "stealth", mode: "normal", to_dm: false,
    });
    expect(await screen.findByRole("status")).toHaveTextContent("24");
    await userEvent.click(screen.getByRole("radio", { name: "Advantage" }));
    await userEvent.click(screen.getByLabelText(/to DM only/i));
    await userEvent.click(screen.getByRole("button", { name: "Initiative +4" }));
    expect(api.roll).toHaveBeenLastCalledWith("c1", {
      character_id: 7, kind: "initiative", mode: "advantage", to_dm: true,
    });
  });

  it("attacks, damage, criticals and custom dice", async () => {
    renderSheet();
    await userEvent.click(await screen.findByRole("tab", { name: "Actions" }));
    await userEvent.click(screen.getByRole("button", { name: "Rapier attack +7" }));
    expect(api.roll).toHaveBeenLastCalledWith("c1", expect.objectContaining({ kind: "attack", key: "0" }));
    await userEvent.click(screen.getByRole("button", { name: "Rapier damage 1d8+4" }));
    expect(api.roll).toHaveBeenLastCalledWith("c1", expect.objectContaining({ kind: "damage", key: "0", mode: "normal" }));
    await userEvent.click(screen.getByRole("button", { name: "Rapier critical damage" }));
    expect(api.roll).toHaveBeenLastCalledWith("c1", expect.objectContaining({ kind: "damage", mode: "critical" }));
    await userEvent.click(screen.getByRole("tab", { name: "Custom dice" }));
    await userEvent.click(screen.getByRole("button", { name: "Sneak Attack 3d6" }));
    expect(api.roll).toHaveBeenLastCalledWith("c1", expect.objectContaining({ kind: "custom", key: "0" }));
  });

  it("viewers who can't edit see no roll or edit buttons", async () => {
    renderSheet(false);
    await screen.findByRole("heading", { name: "Mira" });
    expect(screen.queryByRole("button", { name: /Stealth/ })).toBeNull();
    expect(screen.getByText("+10")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit sheet/i })).toBeNull();
  });

  it("HP buttons save the sheet with its version", async () => {
    api.saveSheet.mockResolvedValue({ sheet: { ...sheet, hp: { ...sheet.hp, current: 29 } }, derived, version: 4, can_edit: true });
    renderSheet();
    await userEvent.click(await screen.findByRole("button", { name: /lose 1 hp/i }));
    expect(api.saveSheet).toHaveBeenCalledWith("c1", 7, expect.objectContaining({ hp: { max: 38, current: 29, temp: 0 } }), 3);
  });
});

describe("sheet editing", () => {
  it("edits and saves; a conflict is shown", async () => {
    api.saveSheet.mockRejectedValueOnce(new ApiError(409, "This sheet changed while you were editing. Reload and try again."));
    renderSheet();
    await userEvent.click(await screen.findByRole("button", { name: /edit sheet/i }));
    const ac = screen.getByLabelText("Armor Class");
    await userEvent.clear(ac);
    await userEvent.type(ac, "17");
    await userEvent.click(screen.getByRole("button", { name: "Save sheet" }));
    expect(api.saveSheet).toHaveBeenCalledWith("c1", 7, expect.objectContaining({ armor_class: 17 }), 3);
    // M10 review: a conflict now reloads the newest sheet and says so
    expect(await screen.findByRole("alert")).toHaveTextContent(/changed this sheet/i);
  });
});

describe("game log", () => {
  it("polls for newer rolls and marks DM-only ones", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.rolls.mockResolvedValueOnce([rollResult]).mockResolvedValueOnce([
      { ...rollResult, id: 2, label: "Mira — Insight", total: 9, to_dm: true },
    ]).mockResolvedValue([]);
    render(<GameLog cid="c1" />);
    expect(await screen.findByText("Mira — Stealth")).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(3100);
    });
    expect(await screen.findByText("Mira — Insight")).toBeInTheDocument();
    expect(api.rolls).toHaveBeenLastCalledWith("c1", 1);
    const item = screen.getByText("Mira — Insight").closest("li")!;
    expect(within(item).getByText(/DM only/i)).toBeInTheDocument();
  });

  it("rolls a free expression", async () => {
    render(<GameLog cid="c1" roller />);
    await userEvent.type(screen.getByLabelText(/dice/i), "2d6+3");
    await userEvent.click(screen.getByRole("button", { name: "Roll" }));
    expect(api.roll).toHaveBeenCalledWith("c1", { kind: "expression", expression: "2d6+3", mode: "normal", to_dm: false });
  });
});

describe("generated sheet entry", () => {
  it("links to the sheet instead of offering edit or delete", async () => {
    const { EntryPanel } = await import("./components/EntryPanel");
    const entryApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
    entryApi.entry = vi.fn().mockResolvedValue({
      id: 9, board: "character", character_id: 7, author_id: "u1", kind: "sheet",
      title: "Mira — character sheet", body: "AC 15", visibility: "party", session: null,
      created_at: "", updated_at: "",
    });
    render(
      <CampaignContext.Provider value={{ campaign, user }}>
        <MemoryRouter>
          <EntryPanel entryId={9} onClose={() => {}} />
        </MemoryRouter>
      </CampaignContext.Provider>,
    );
    expect(await screen.findByRole("link", { name: /open character sheet/i })).toHaveAttribute(
      "href", "/c/c1/characters/7/sheet",
    );
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });
});

describe("M10 review fixes", () => {
  it("the game log keeps its history across Edit and Cancel", async () => {
    api.rolls.mockResolvedValueOnce([rollResult]).mockResolvedValue([]);
    renderSheet();
    const log = await screen.findByRole("region", { name: "Game log" });
    expect(await within(log).findByText("Mira — Stealth")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /edit sheet/i }));
    await userEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]);
    expect(within(screen.getByRole("region", { name: "Game log" })).getByText("Mira — Stealth")).toBeInTheDocument();
  });

  it("the page's own roll never makes the log skip someone else's", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const theirs = { ...rollResult, id: 4, label: "Bo — 1d4" };
    const mine = { ...rollResult, id: 5, label: "Mira — Insight" };
    api.rolls.mockResolvedValueOnce([]).mockResolvedValueOnce([theirs, mine]).mockResolvedValue([]);
    const { rerender } = render(<GameLog cid="c1" />);
    await act(async () => {});
    rerender(<GameLog cid="c1" incoming={mine} />);
    await act(async () => {
      vi.advanceTimersByTime(3100);
    });
    expect(api.rolls).toHaveBeenLastCalledWith("c1", 0);
    expect(await screen.findByText("Bo — 1d4")).toBeInTheDocument();
    expect(screen.getAllByText("Mira — Insight")).toHaveLength(1);
  });

  it("quick HP clicks queue up: none lost, focus kept, saved one after another", async () => {
    const finishers: ((v: unknown) => void)[] = [];
    api.saveSheet.mockImplementation((_c: string, _id: number, next: typeof sheet, version: number) =>
      new Promise((r) => finishers.push(() => r({ sheet: next, derived, version: version + 1, can_edit: true }))),
    );
    renderSheet();
    const lose = await screen.findByRole("button", { name: /lose 1 hp/i });
    lose.focus();
    await userEvent.keyboard("{Enter}");
    await userEvent.keyboard("{Enter}");
    await userEvent.keyboard("{Enter}");
    expect(lose).toHaveFocus();
    expect(lose).not.toBeDisabled();
    expect(api.saveSheet).toHaveBeenCalledTimes(1);
    expect(api.saveSheet.mock.calls[0][2].hp.current).toBe(29);
    await act(async () => finishers[0](undefined));
    expect(api.saveSheet).toHaveBeenCalledTimes(2);
    expect(api.saveSheet.mock.calls[1][2].hp.current).toBe(27); // the two queued clicks together
    expect(api.saveSheet.mock.calls[1][3]).toBe(4);
    await act(async () => finishers[1](undefined));
    expect(screen.getByRole("group", { name: "Hit points" })).toHaveTextContent("27");
  });

  it("a conflict reloads the newest sheet", async () => {
    api.saveSheet.mockRejectedValueOnce(new ApiError(409, "This sheet changed while you were editing. Reload and try again."));
    renderSheet();
    await userEvent.click(await screen.findByRole("button", { name: /lose 1 hp/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/reloaded/i);
    expect(api.sheet).toHaveBeenCalledTimes(2);
  });

  it("number fields take negative numbers and can be cleared", async () => {
    api.saveSheet.mockResolvedValue({ sheet, derived, version: 4, can_edit: true });
    renderSheet();
    await userEvent.click(await screen.findByRole("button", { name: /edit sheet/i }));
    const init = screen.getByLabelText("Initiative bonus (extra)");
    await userEvent.clear(init);
    expect(init).toHaveValue(null);
    await userEvent.type(init, "-2");
    expect(init).toHaveValue(-2);
    await userEvent.click(screen.getByRole("button", { name: "Save sheet" }));
    expect(api.saveSheet).toHaveBeenCalledWith("c1", 7, expect.objectContaining({ initiative_misc: -2 }), 3);
  });

  it("focus moves into the editor and back", async () => {
    renderSheet();
    await userEvent.click(await screen.findByRole("button", { name: /edit sheet/i }));
    expect(screen.getByRole("heading", { name: "Edit sheet" })).toHaveFocus();
    await userEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]);
    expect(screen.getByRole("button", { name: /edit sheet/i })).toHaveFocus();
  });
});

describe("M10.1 polish", () => {
  it("P3: an open sheet picks up a newer version saved elsewhere", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderSheet();
    await screen.findByRole("heading", { name: "Mira" });
    api.sheet.mockResolvedValue({ sheet: { ...sheet, hp: { ...sheet.hp, current: 7 } }, derived, version: 9, can_edit: true });
    expect(screen.getByRole("group", { name: "Hit points" })).toHaveTextContent(/Hit points30/);
    await act(async () => {
      vi.advanceTimersByTime(5100);
    });
    await vi.waitFor(() =>
      expect(screen.getByRole("group", { name: "Hit points" })).toHaveTextContent(/Hit points7\b/),
    );
  });

  it("P4: naturals are labelled in words", async () => {
    api.roll.mockResolvedValue({ ...rollResult, natural: 20, total: 30 });
    renderSheet();
    await userEvent.click(await screen.findByRole("button", { name: "Stealth +10" }));
    expect(await screen.findByRole("status")).toHaveTextContent(/natural 20/i);
  });

  it("P5: arrow keys move between sheet tabs", async () => {
    renderSheet();
    const actions = await screen.findByRole("tab", { name: "Actions" });
    actions.focus();
    await userEvent.keyboard("{ArrowRight}");
    const spells = screen.getByRole("tab", { name: "Spells" });
    expect(spells).toHaveFocus();
    expect(spells).toHaveAttribute("aria-selected", "true");
    expect(spells).toHaveAttribute("aria-controls", screen.getByRole("tabpanel").id);
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Notes" })).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(actions).toHaveFocus();
  });
});
