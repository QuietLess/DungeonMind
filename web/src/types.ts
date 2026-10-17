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
