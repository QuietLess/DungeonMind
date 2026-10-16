// Shows one entry in a drawer. Always fetched through the API, so opening an entry from a chat
// citation re-checks that the viewer may still read it (M7 UI4).
import { useState } from "react";
import { api } from "../api";
import { message, useCampaign, useLoad } from "../hooks";
import { EyeIcon } from "../icons";
import type { Entry } from "../types";
import { VisibilityBadge } from "../visibility";
import { Drawer } from "./Drawer";
import { kindLabel } from "./EntryCard";
import { EntryEditor } from "./EntryEditor";

export function EntryPanel({
  entryId,
  onClose,
  onChanged,
}: {
  entryId: number;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const { campaign, user } = useCampaign();
  const { data: entry, setData, error } = useLoad(() => api.entry(campaign.id, entryId), [entryId]);
  const [editing, setEditing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const mine = entry?.author_id === user.id;

  const act = async (fn: () => Promise<Entry | void>) => {
    setActionError(null);
    try {
      const result = await fn();
      if (result) setData(result);
      onChanged?.();
    } catch (e) {
      setActionError(message(e));
    }
  };

  const remove = () => {
    if (entry && window.confirm(`Delete “${entry.title}”? This can't be undone.`)) {
      act(() => api.deleteEntry(campaign.id, entry.id)).then(onClose);
    }
  };

  return (
    <Drawer title={editing ? "Edit entry" : entry?.title ?? "Entry"} onClose={onClose}>
      {error && <p className="error" role="alert">{error}</p>}
      {entry && editing && (
        <EntryEditor
          board={entry.board}
          entry={entry}
          onSaved={(saved) => {
            setData(saved);
            setEditing(false);
            onChanged?.();
          }}
          onCancel={() => setEditing(false)}
        />
      )}
      {entry && !editing && (
        <>
          <div className="entry-head">
            <VisibilityBadge visibility={entry.visibility} />
            {entry.board !== "journal" && <span className="kind">{kindLabel(entry.kind)}</span>}
            {entry.session != null && <span className="kind">Session {entry.session}</span>}
          </div>
          <div className="entry-body">
            {entry.body.split(/\n\s*\n/).map((para, i) => (
              <p key={i}>{para}</p>
            ))}
          </div>
          {actionError && <p className="error" role="alert">{actionError}</p>}
          {mine && (
            <div className="actions">
              {entry.board === "lore" && entry.visibility === "private" && (
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() =>
                    window.confirm(`Reveal “${entry.title}” to the party?`) &&
                    act(() => api.updateEntry(campaign.id, entry.id, { visibility: "party" }))
                  }
                >
                  <EyeIcon /> Reveal to party
                </button>
              )}
              <button type="button" className="btn btn-ghost danger" onClick={remove}>
                Delete
              </button>
              <button type="button" className="btn" onClick={() => setEditing(true)}>
                Edit
              </button>
            </div>
          )}
        </>
      )}
    </Drawer>
  );
}
