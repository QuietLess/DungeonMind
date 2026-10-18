// Shapes returned by the hub API (src/dungeonmind/hub/models.py and graph/state.py).

export type Role = "dm" | "player";
export type Visibility = "private" | "dm" | "party";
export type Board = "lore" | "character" | "journal";

export interface User {
  id: string;
  username: string;
}

export interface AccountSummary {
  username: string;
  created_at: string;
  sessions: number;
  questions_today: number;
  daily_limit: number;
}

export interface Campaign {
  id: string;
  name: string;
  dm_id: string;
  role: Role;
  created_at: string;
}

export interface Member {
  user_id: string;
  username: string;
  role: Role;
}

export interface Invite {
  code: string;
  expires_at: string;
}

export interface Character {
  id: number;
  owner_id: string;
  name: string;
  summary: string;
  is_npc?: boolean;
}

export interface Entry {
  id: number;
  board: Board;
  character_id: number | null;
  author_id: string;
  kind: string;
  title: string;
  body: string;
  visibility: Visibility;
  session: number | null;
  created_at: string;
  updated_at: string;
}

export interface EntryInput {
  board: Board;
  title: string;
  body: string;
  kind?: string;
  visibility?: Visibility;
  character_id?: number | null;
  session?: number | null;
}

export interface Citation {
  id: string; // N1, E2, T1
  kind: "note" | "srd" | "tool";
  label: string;
  entry_id?: number;
}

export interface Thread {
  id: number;
  title: string;
  created_at: string;
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  created_at: string;
  citations: Citation[];
  confidence: string | null;
  insufficient_evidence: boolean | null;
  stop_reason: string | null;
}

export const BOARD_KINDS: Record<Board, string[]> = {
  lore: ["npc", "location", "faction", "item", "event", "plot", "other"],
  character: ["backstory", "goal", "bond", "secret", "relationship", "other"],
  journal: ["note"],
};

// --- M10 character sheets and rolls (src/dungeonmind/hub/sheets.py, rolls.py) ---------------

export type AbilityKey = "str" | "dex" | "con" | "int" | "wis" | "cha";
export type SkillLevel = "none" | "proficient" | "expertise";

export interface SheetData {
  classes: { name: string; subclass: string; level: number; hit_die: number }[];
  species: string;
  background: string;
  alignment: string;
  xp: number;
  abilities: Record<AbilityKey, number>;
  saves: AbilityKey[];
  save_misc: Partial<Record<AbilityKey, number>>;
  skills: Record<string, SkillLevel>;
  skill_misc: Record<string, number>;
  jack_of_all_trades: boolean;
  armor_class: number;
  speed: number;
  initiative_misc: number;
  hp: { max: number; current: number; temp: number };
  hit_dice_used: Record<string, number>;
  death_saves: { successes: number; failures: number };
  inspiration: boolean;
  exhaustion: number;
  conditions: string[];
  attacks: {
    name: string;
    ability: AbilityKey | null;
    proficient: boolean;
    to_hit_misc: number;
    damage: string;
    add_ability_to_damage: boolean;
    damage_type: string;
    notes: string;
  }[];
  spellcasting: { ability: AbilityKey | null; slots: { max: number; used: number }[] };
  spells: { name: string; level: number; prepared: boolean; notes: string }[];
  inventory: { name: string; quantity: number; weight: number; equipped: boolean; notes: string }[];
  currency: { cp: number; sp: number; ep: number; gp: number; pp: number };
  features: { name: string; source: string; text: string }[];
  proficiencies: string;
  custom_dice: { name: string; expression: string; notes: string }[];
  notes: string;
}

export interface Derived {
  level: number;
  proficiency_bonus: number;
  modifiers: Record<AbilityKey, number>;
  saves: Record<AbilityKey, number>;
  skills: Record<string, number>;
  passive_perception: number;
  initiative: number;
  attacks: { to_hit: number; damage: string }[];
  spell_save_dc: number | null;
  spell_attack: number | null;
  hit_dice: Record<string, number>;
  carried_weight: number;
  carrying_capacity: number;
  d20_penalty: number;
  speed: number;
}

export interface SheetView {
  sheet: SheetData;
  derived: Derived;
  version: number;
  can_edit: boolean;
}

export type RollMode = "normal" | "advantage" | "disadvantage" | "critical";

export interface RollRequest {
  kind: string;
  key?: string;
  mode: RollMode;
  to_dm: boolean;
  character_id?: number;
  expression?: string;
  dice?: Record<string, number>;
  stat?: string;
  modifier?: number;
}

export interface Roll {
  id: number;
  label: string;
  author: string;
  character_id: number | null;
  expression: string;
  terms: { term: string; rolls: number[]; kept: number[]; subtotal: number }[];
  bonus: number;
  total: number;
  natural: number | null;
  outcome: string | null;
  mode: RollMode;
  to_dm: boolean;
  created_at: string;
}

// --- M11 combat tracker ------------------------------------------------------------------

export interface Combatant {
  id: number;
  name: string;
  initiative: number | null;
  is_npc: boolean;
  conditions: string[];
  health: "unharmed" | "injured" | "bloodied" | "down";
  can_edit: boolean;
  character_id?: number;
  hp?: { max: number; current: number; temp: number };
  ac?: number;
  hidden?: boolean;
  init_bonus?: number;
}

export interface Encounter {
  id: number;
  name: string;
  round: number;
  turn: number;
  current_id: number | null;
  combatants: Combatant[];
}
