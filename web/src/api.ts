// Hub API client (M7 UI5). Same-origin requests with the session cookie; unsafe methods carry
// the CSRF header the server requires; errors surface the API's plain `detail` message.
import type {
  AccountSummary,
  Campaign,
  Character,
  Citation,
  Entry,
  EntryInput,
  Invite,
  Member,
  Message,
  Thread,
  User,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);
let onUnauthorized: (() => void) | undefined;

export function configureApi(opts: { onUnauthorized?: () => void }): void {
  onUnauthorized = opts.onUnauthorized;
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });
  if (UNSAFE.has(method)) headers.set("X-DungeonMind", "1");
  if (body !== undefined) headers.set("content-type", "application/json");
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Couldn't reach the server. Is `dungeonmind serve` running?");
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) onUnauthorized?.();
    const detail = data && typeof data.detail === "string" ? data.detail : response.statusText;
    throw new ApiError(response.status, detail || "Something went wrong.");
  }
  return data as T;
}

const c = (cid: string) => `/campaigns/${encodeURIComponent(cid)}`;

export interface SearchHit {
  entry_id: number;
  source: string;
  text: string;
  canon: boolean;
  score: number;
}

export const api = {
  me: () => request<User>("GET", "/me"),
  register: (username: string, password: string) =>
    request<User>("POST", "/auth/register", { username, password }),
  login: (username: string, password: string) =>
    request<User>("POST", "/auth/login", { username, password }),
  logout: () => request<void>("POST", "/auth/logout"),

  account: () => request<AccountSummary>("GET", "/account"),
  changePassword: (current_password: string, new_password: string) =>
    request<void>("POST", "/account/password", { current_password, new_password }),
  signOutOthers: () => request<{ signed_out: number }>("POST", "/account/sign-out-others"),
  deleteAccount: (password: string, confirm_username: string) =>
    request<void>("POST", "/account/delete", { password, confirm_username }),

  campaigns: () => request<Campaign[]>("GET", "/campaigns"),
  createCampaign: (name: string) => request<Campaign>("POST", "/campaigns", { name }),
  campaign: (cid: string) => request<Campaign>("GET", c(cid)),
  deleteCampaign: (cid: string) => request<void>("DELETE", c(cid)),
  createInvite: (cid: string) => request<Invite>("POST", `${c(cid)}/invites`),
  acceptInvite: (code: string) => request<Campaign>("POST", "/invites/accept", { code }),
  members: (cid: string) => request<Member[]>("GET", `${c(cid)}/members`),
  removeMember: (cid: string, uid: string) =>
    request<void>("DELETE", `${c(cid)}/members/${encodeURIComponent(uid)}`),

  characters: (cid: string) => request<Character[]>("GET", `${c(cid)}/characters`),
  createCharacter: (cid: string, name: string, summary: string) =>
    request<Character>("POST", `${c(cid)}/characters`, { name, summary }),
  updateCharacter: (cid: string, chid: number, changes: { name?: string; summary?: string }) =>
    request<Character>("PATCH", `${c(cid)}/characters/${chid}`, changes),
  deleteCharacter: (cid: string, chid: number) =>
    request<void>("DELETE", `${c(cid)}/characters/${chid}`),

  entries: (
    cid: string,
    filters: { board?: string; character_id?: number; session?: number; q?: string } = {},
  ) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "") params.set(key, String(value));
    }
    const query = params.toString();
    return request<Entry[]>("GET", `${c(cid)}/entries${query ? `?${query}` : ""}`);
  },
  entry: (cid: string, eid: number) => request<Entry>("GET", `${c(cid)}/entries/${eid}`),
  createEntry: (cid: string, input: EntryInput) =>
    request<Entry>("POST", `${c(cid)}/entries`, input),
  updateEntry: (cid: string, eid: number, changes: Partial<EntryInput>) =>
    request<Entry>("PATCH", `${c(cid)}/entries/${eid}`, changes),
  deleteEntry: (cid: string, eid: number) => request<void>("DELETE", `${c(cid)}/entries/${eid}`),
  search: (cid: string, q: string) =>
    request<SearchHit[]>("GET", `${c(cid)}/search?q=${encodeURIComponent(q)}`),

  threads: (cid: string) => request<Thread[]>("GET", `${c(cid)}/chats`),
  createThread: (cid: string, title: string) =>
    request<Thread>("POST", `${c(cid)}/chats`, { title }),
  thread: (cid: string, tid: number) =>
    request<{ thread: Thread; messages: Message[] }>("GET", `${c(cid)}/chats/${tid}`),
  ask: (cid: string, tid: number, text: string) =>
    request<Message>("POST", `${c(cid)}/chats/${tid}/messages`, { text }),
};

export type { Citation };
