// The Lore and Journal boards: filterable entry lists, grouped by kind on the lore board.
import { useState } from "react";
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

export function BoardPage({ board }: { board: "lore" | "journal" }) {
  const { campaign, user } = useCampaign();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: entries, loading, error: loadError, reload } = useLoad(
    () => api.entries(campaign.id, { board, q: query || undefined }),
    [campaign.id, board, query],
  );
  const canWrite = board === "journal" || campaign.role === "dm";

  const reveal = async (entry: Entry) => {
    try {
      await api.updateEntry(campaign.id, entry.id, { visibility: "party" });
      reload();
    } catch (e) {
      setError(message(e));
    }
  };

  const groups =
    board === "lore"
      ? BOARD_KINDS.lore
          .map((kind) => ({ kind, items: entries?.filter((e) => e.kind === kind) ?? [] }))
          .filter((g) => g.items.length)
      : [{ kind: "", items: entries ?? [] }];

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
      <label className="search">
        <span className="sr-only">Filter entries</span>
        <input
          type="search"
          placeholder="Filter by title or text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      {(error || loadError) && <p className="error" role="alert">{error ?? loadError}</p>}
      {loading && !entries && <p className="muted">Loading…</p>}
      {entries && entries.length === 0 && (
        <p className="empty">
          {query ? "Nothing matches that filter." : board === "lore" && campaign.role !== "dm"
            ? "Nothing has been revealed yet."
            : "No entries yet."}
        </p>
      )}
      {groups.map((group) => (
        <div key={group.kind || "all"} className="entry-group">
          {group.kind && <h2>{kindLabel(group.kind)}</h2>}
          <div className="entry-grid">
            {group.items.map((entry) => (
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
        </div>
      ))}
      {open !== null && <EntryPanel entryId={open} onClose={() => setOpen(null)} onChanged={reload} />}
      {creating && (
        <Drawer title={board === "lore" ? "New lore entry" : "New journal entry"} onClose={() => setCreating(false)}>
          <EntryEditor
            board={board}
            onSaved={() => {
              setCreating(false);
              reload();
            }}
            onCancel={() => setCreating(false)}
          />
        </Drawer>
      )}
    </section>
  );
}
