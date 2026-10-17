// The Lore and Journal boards: filterable entry lists, grouped by kind on the lore board.
import { useEffect, useState } from "react";
import { api } from "../api";
import { Drawer } from "../components/Drawer";
import { EntryCard, kindLabel } from "../components/EntryCard";
import { EntryEditor } from "../components/EntryEditor";
import { EntryPanel } from "../components/EntryPanel";
import { message, useCampaign, useLoad } from "../hooks";
import { PlusIcon } from "../icons";
import { BOARD_KINDS, type Entry } from "../types";

const INTRO = {
  lore: {
    dm: "Your world's canon: NPCs, places, factions and secrets. New entries are secret until you reveal them, and the chatbot only tells players what you've revealed.",
    player: "What the DM has revealed about the world so far.",
  },
  journal: {
    dm: "Session notes and recaps from everyone at the table.",
    player: "Session notes, recaps and rumours. Party entries help everyone's chatbot answers.",
  },
};

/** UB4: by session, newest first; entries without a session after them, newest first. */
function journalOrder(a: Entry, b: Entry): number {
  if (a.session != null && b.session != null && a.session !== b.session) return b.session - a.session;
  if ((a.session == null) !== (b.session == null)) return a.session == null ? 1 : -1;
  return b.created_at.localeCompare(a.created_at);
}

export function BoardPage({ board }: { board: "lore" | "journal" }) {
  const { campaign, user } = useCampaign();
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState(""); // UB2: follows `typed` 300 ms after typing stops
  const [kindFilter, setKindFilter] = useState<string | null>(null);
  const [shown, setShown] = useState<"all" | "secret" | "revealed">("all");
  const [open, setOpen] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const { data: entries, loading, error: loadError, reload } = useLoad(
    () => api.entries(campaign.id, { board, q: query || undefined }),
    [campaign.id, board, query],
  );
  const canWrite = board === "journal" || campaign.role === "dm";

  useEffect(() => {
    const timer = setTimeout(() => setQuery(typed.trim()), 300);
    return () => clearTimeout(timer);
  }, [typed]);

  const reveal = async (entry: Entry) => {
    try {
      await api.updateEntry(campaign.id, entry.id, { visibility: "party" });
      reload();
    } catch (e) {
      setError(message(e));
    }
  };

  // UB1: kind and secret/revealed filters work on the loaded list, on the lore board only
  // (R1: the journal reuses this component, and must never inherit a hidden filter).
  const lore = board === "lore";
  const show = lore ? shown : "all";
  const visible = (entries ?? []).filter(
    (e) => show === "all" || (show === "secret" ? e.visibility !== "party" : e.visibility === "party"),
  );
  // R2: the selected kind keeps its chip (count 0) so it can always be seen and cleared.
  const kindCounts = BOARD_KINDS.lore
    .map((kind) => ({ kind, count: visible.filter((e) => e.kind === kind).length }))
    .filter((k) => k.count || k.kind === kindFilter);
  const filtering = kindFilter !== null || shown !== "all";
  // L1: one grid, ordered by kind then title (Turkish collation for Turkish names).
  const kindRank = (k: string) => {
    const i = BOARD_KINDS.lore.indexOf(k);
    return i === -1 ? BOARD_KINDS.lore.length : i;
  };
  const items = lore
    ? visible
        .filter((e) => kindFilter === null || e.kind === kindFilter)
        .sort((a, b) => kindRank(a.kind) - kindRank(b.kind) || a.title.localeCompare(b.title, "tr"))
    : [...visible].sort(journalOrder);

  return (
    <section className="board">
      <header className="board-head">
        <div>
          <h1>{board === "lore" ? "Lore" : "Journal"}</h1>
          <p className="muted">{INTRO[board][campaign.role]}</p>
        </div>
        {canWrite && (
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            <PlusIcon /> New {board === "lore" ? "lore entry" : "journal entry"}
          </button>
        )}
      </header>
      <div className="toolbar">
        <label className="search">
          <span className="sr-only">Filter entries</span>
          <input
            type="search"
            placeholder="Filter by title or text"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
          />
        </label>
        {lore && campaign.role === "dm" && entries && (entries.length > 0 || filtering) && (
          <div role="radiogroup" aria-label="Show" className="segmented">
            {(["all", "secret", "revealed"] as const).map((value) => (
              <label key={value} className={shown === value ? "active" : undefined}>
                <input
                  type="radio"
                  name="shown"
                  value={value}
                  checked={shown === value}
                  onChange={() => setShown(value)}
                />
                {value === "all" ? "All" : value === "secret" ? "Secret" : "Revealed"}
              </label>
            ))}
          </div>
        )}
      </div>
      {lore && entries && (entries.length > 0 || filtering) && (
        <div role="group" aria-label="Kind" className="chip-row kind-chips">
          <button
            type="button"
            className="chip"
            aria-pressed={kindFilter === null}
            onClick={() => setKindFilter(null)}
          >
            All <span className="count">{visible.length}</span>
          </button>
          {kindCounts.map(({ kind, count }) => (
            <button
              key={kind}
              type="button"
              className={`chip chip-${kind}`}
              aria-pressed={kindFilter === kind}
              onClick={() => setKindFilter(kindFilter === kind ? null : kind)}
            >
              {kindLabel(kind)} <span className="count">{count}</span>
            </button>
          ))}
        </div>
      )}
      {(error || loadError) && <p className="error" role="alert">{error ?? loadError}</p>}
      {loading && !entries && <p className="muted">Loading…</p>}
      {entries && entries.length === 0 && (
        <p className="empty">
          {query ? "Nothing matches that filter." : board === "lore" && campaign.role !== "dm"
            ? "Nothing has been revealed yet."
            : "No entries yet."}
        </p>
      )}
      {entries && entries.length > 0 && items.length === 0 && (
        <p className="empty">Nothing matches these filters.</p>
      )}
      <div className="entry-grid">
        {items.map((entry) => (
          <EntryCard
            key={entry.id}
            entry={entry}
            role={campaign.role}
            userId={user.id}
            onOpen={(e) => setOpen(e.id)}
            onReveal={reveal}
          />
        ))}
      </div>
      {open !== null && <EntryPanel entryId={open} onClose={() => setOpen(null)} onChanged={reload} />}
      {creating && (
        <Drawer
          title={board === "lore" ? "New lore entry" : "New journal entry"}
          onClose={() => setCreating(false)}
          dirty={dirty}
        >
          <EntryEditor
            board={board}
            onSaved={() => {
              setCreating(false);
              reload();
            }}
            onCancel={() => setCreating(false)}
            onDirtyChange={setDirty}
          />
        </Drawer>
      )}
    </section>
  );
}
