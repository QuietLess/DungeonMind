import { type FormEvent, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { message, useCampaign } from "../hooks";
import { BOARD_KINDS, type Board, type Entry, type Visibility } from "../types";
import { VisibilityPicker, visibilityOptions } from "../visibility";
import { kindLabel } from "./EntryCard";

const DEFAULT_VISIBILITY: Record<Board, Visibility> = {
  lore: "private",
  character: "dm",
  journal: "party",
};

export function EntryEditor({
  board,
  characterId,
  entry,
  onSaved,
  onCancel,
  onDirtyChange,
}: {
  board: Board;
  characterId?: number;
  entry?: Entry;
  onSaved: (entry: Entry) => void;
  onCancel: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const { campaign } = useCampaign();
  const allowed = visibilityOptions(board, campaign.role);
  const initialVisibility = entry?.visibility ?? DEFAULT_VISIBILITY[board];
  const [title, setTitle] = useState(entry?.title ?? "");
  const [body, setBody] = useState(entry?.body ?? "");
  const [kind, setKind] = useState(entry?.kind ?? BOARD_KINDS[board].at(-1)!);
  const [visibility, setVisibility] = useState<Visibility>(
    allowed.includes(initialVisibility) ? initialVisibility : "private",
  );
  const [session, setSession] = useState(entry?.session?.toString() ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);

  const dirty =
    title !== (entry?.title ?? "") ||
    body !== (entry?.body ?? "") ||
    session !== (entry?.session?.toString() ?? "") ||
    (entry !== undefined && (kind !== entry.kind || visibility !== entry.visibility));
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (sending.current) return; // F2: one request per save
    sending.current = true;
    setBusy(true);
    setError(null);
    const fields = {
      title,
      body,
      kind,
      visibility,
      session: session ? Number(session) : null,
    };
    try {
      const saved = entry
        ? await api.updateEntry(campaign.id, entry.id, fields)
        : await api.createEntry(campaign.id, { board, character_id: characterId ?? null, ...fields });
      onSaved(saved);
    } catch (err) {
      setError(message(err));
    } finally {
      sending.current = false;
      setBusy(false);
    }
  };

  return (
    <form className="editor" onSubmit={submit}>
      <label>
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={120} />
      </label>
      {BOARD_KINDS[board].length > 1 && (
        <label>
          Kind
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {BOARD_KINDS[board].map((k) => (
              <option key={k} value={k}>
                {kindLabel(k)}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Text
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          required
          maxLength={10000}
          rows={10}
        />
        <span className="hint">{body.length.toLocaleString()} / 10,000</span>
      </label>
      <label>
        Session (optional)
        <input
          type="number"
          min={1}
          max={999}
          value={session}
          onChange={(e) => setSession(e.target.value)}
          inputMode="numeric"
        />
      </label>
      <VisibilityPicker board={board} role={campaign.role} value={visibility} onChange={setVisibility} />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        <button type="button" className="btn btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "Saving…" : entry ? "Save changes" : "Add entry"}
        </button>
      </div>
    </form>
  );
}
