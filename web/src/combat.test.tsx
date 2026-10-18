// M11 D3 dice tray, C6 combat page, N1 NPCs on the characters page.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DiceTray } from "./components/DiceTray";
import { CampaignContext } from "./hooks";
import { CombatPage } from "./pages/CombatPage";
import type { Campaign } from "./types";

const api = vi.hoisted(() => ({
  characters: vi.fn(), sheet: vi.fn(), roll: vi.fn(), rolls: vi.fn(),
  encounter: vi.fn(), startEncounter: vi.fn(), addCombatants: vi.fn(), rollInitiative: vi.fn(),
  turn: vi.fn(), combatHp: vi.fn(), combatConditions: vi.fn(), endEncounter: vi.fn(),
  updateCombatant: vi.fn(), removeCombatant: vi.fn(), monsters: vi.fn(),
}));
vi.mock("./api", async (orig) => ({ ...(await orig<typeof import("./api")>()), api }));

const dm: Campaign = { id: "c1", name: "Ashvale", dm_id: "u1", role: "dm", created_at: "" };
const player: Campaign = { ...dm, role: "player" };
const user = { id: "u1", username: "dana" };
const rollResult = {
  id: 1, label: "dana — 2d6+1d20", author: "dana", character_id: null, expression: "2d6+1d20",
  terms: [{ term: "2d6", rolls: [3, 4], kept: [3, 4], subtotal: 7 }, { term: "1d20", rolls: [12], kept: [12], subtotal: 12 }],
  bonus: 0, total: 19, natural: null, outcome: null, mode: "normal" as const, to_dm: false, created_at: "",
};

function inCampaign(ui: React.ReactNode, campaign = dm) {
  return render(
    <CampaignContext.Provider value={{ campaign, user }}>
      <MemoryRouter initialEntries={["/c/c1/combat"]}>
        <Routes>
          <Route path="/c/:cid/*" element={ui} />
        </Routes>
      </MemoryRouter>
    </CampaignContext.Provider>,
  );
}

const derived = {
  level: 5, proficiency_bonus: 3,
  modifiers: { str: 0, dex: 4, con: 2, int: 1, wis: 1, cha: -1 },
  saves: { str: 0, dex: 7, con: 2, int: 1, wis: 1, cha: -1 },
  skills: { stealth: 10, perception: 1 }, passive_perception: 11, initiative: 4,
  attacks: [{ to_hit: 7, damage: "1d8+4" }], spell_save_dc: null, spell_attack: null,
  hit_dice: { "8": 5 }, carried_weight: 0, carrying_capacity: 150, d20_penalty: 0, speed: 30,
};

beforeEach(() => {
  vi.resetAllMocks();
  api.roll.mockResolvedValue(rollResult);
  api.rolls.mockResolvedValue([]);
  api.characters.mockResolvedValue([
    { id: 7, owner_id: "u2", name: "Mira", summary: "", is_npc: false },
    { id: 9, owner_id: "u1", name: "Goblin Warrior 1", summary: "", is_npc: true },
  ]);
  api.sheet.mockResolvedValue({ sheet: { attacks: [{ name: "Rapier" }] }, derived, version: 1, can_edit: true });
});

describe("dice tray", () => {
  it("click dice to build a roll, right-click to take one away", async () => {
    inCampaign(<DiceTray />);
    await userEvent.click(screen.getByRole("button", { name: /open dice/i }));
    const d6 = screen.getByRole("button", { name: /^d6/ });
    await userEvent.click(d6);
    await userEvent.click(d6);
    await userEvent.click(d6);
    await userEvent.pointer({ keys: "[MouseRight]", target: d6 });
    await userEvent.click(screen.getByRole("button", { name: /^d20/ }));
    expect(d6).toHaveAccessibleName("d6, 2 selected");
    await userEvent.click(screen.getByRole("button", { name: /^roll/i }));
    expect(api.roll).toHaveBeenCalledWith("c1", { kind: "pool", dice: { "6": 2, "20": 1 }, modifier: 0, mode: "normal", to_dm: false });
    expect(await screen.findByRole("status")).toHaveTextContent("19");
  });

  it("roll as a character and add a stat; advantage only for a lone d20", async () => {
    inCampaign(<DiceTray />);
    await userEvent.click(screen.getByRole("button", { name: /open dice/i }));
    await userEvent.click(screen.getByRole("button", { name: /^d20/ }));
    await userEvent.selectOptions(screen.getByLabelText("Roll as"), "7");
    await userEvent.selectOptions(await screen.findByLabelText("Add"), "skill:stealth");
    expect(screen.getByRole("option", { name: "Stealth +10" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Increase modifier" }));
    await userEvent.click(screen.getByRole("radio", { name: "Advantage" }));
    await userEvent.click(screen.getByRole("button", { name: /^roll/i }));
    expect(api.roll).toHaveBeenCalledWith("c1", {
      kind: "pool", dice: { "20": 1 }, modifier: 1, mode: "advantage", to_dm: false,
      character_id: 7, stat: "skill:stealth",
    });
    await userEvent.click(screen.getByRole("button", { name: /^d6/ }));
    expect(screen.getByRole("radio", { name: "Advantage" })).toBeDisabled();
  });

  it("rolling as an NPC defaults to the DM only", async () => {
    inCampaign(<DiceTray />);
    await userEvent.click(screen.getByRole("button", { name: /open dice/i }));
    await userEvent.selectOptions(screen.getByLabelText("Roll as"), "9");
    expect(screen.getByLabelText(/to DM only/i)).toBeChecked();
  });
});

const encounter = {
  id: 3, name: "Ambush", round: 2, turn: 1, current_id: 12,
  combatants: [
    { id: 11, name: "Mira", initiative: 18, is_npc: false, conditions: [], health: "injured",
      can_edit: true, character_id: 7, hp: { max: 30, current: 22, temp: 0 }, ac: 15, hidden: false, init_bonus: 4 },
    { id: 12, name: "Goblin Warrior 1", initiative: 12, is_npc: true, conditions: ["prone"], health: "bloodied",
      can_edit: true, character_id: 9, hp: { max: 10, current: 4, temp: 0 }, ac: 15, hidden: false, init_bonus: 2 },
  ],
};

describe("combat page", () => {
  it("shows the order with the current turn and DM controls", async () => {
    api.encounter.mockResolvedValue(encounter);
    api.turn.mockResolvedValue(encounter);
    api.combatHp.mockResolvedValue(encounter);
    inCampaign(<CombatPage />);
    const rows = await screen.findAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Mira");
    expect(rows[1]).toHaveAttribute("aria-current", "true");
    expect(screen.getByText(/round 2/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next turn" }));
    expect(api.turn).toHaveBeenCalledWith("c1", 3, "next");
    await userEvent.click(within(rows[1]).getByRole("button", { name: /damage/i }));
    expect(api.combatHp).toHaveBeenCalledWith("c1", 3, 12, -1);
  });

  it("players see health states for NPCs and no DM controls", async () => {
    api.encounter.mockResolvedValue({
      ...encounter,
      combatants: [
        encounter.combatants[0],
        { id: 12, name: "Goblin Warrior 1", initiative: 12, is_npc: true, conditions: ["prone"],
          health: "bloodied", can_edit: false },
      ],
    });
    inCampaign(<CombatPage />, player);
    const goblin = (await screen.findAllByRole("listitem"))[1];
    expect(goblin).toHaveTextContent(/bloodied/i);
    expect(goblin).not.toHaveTextContent("4 / 10");
    expect(screen.queryByRole("button", { name: "Next turn" })).toBeNull();
  });

  it("the DM starts an encounter and adds SRD monsters", async () => {
    api.encounter.mockResolvedValueOnce(null).mockResolvedValue({ ...encounter, combatants: [] });
    api.startEncounter.mockResolvedValue({ id: 3, name: "Ambush" });
    api.monsters.mockResolvedValue(["Goblin Warrior"]);
    api.addCombatants.mockResolvedValue([]);
    inCampaign(<CombatPage />);
    await userEvent.type(await screen.findByLabelText("Encounter name"), "Ambush");
    await userEvent.click(screen.getByRole("button", { name: "Start encounter" }));
    expect(api.startEncounter).toHaveBeenCalledWith("c1", "Ambush");
    await userEvent.type(await screen.findByLabelText("Find a monster"), "gob");
    await userEvent.click(await screen.findByRole("button", { name: /add goblin warrior/i }));
    expect(api.addCombatants).toHaveBeenCalledWith("c1", 3, { monster: "Goblin Warrior", count: 1 });
  });
});
