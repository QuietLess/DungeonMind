// One entry in a board list: title, kind, visibility, and the DM's Reveal action (M7 UI6).
import { EyeIcon } from "../icons";
import type { Entry, Role } from "../types";
import { VisibilityBadge } from "../visibility";

const KIND_LABEL: Record<string, string> = { npc: "NPC" };

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind.charAt(0).toUpperCase() + kind.slice(1);
}

export function EntryCard({
  entry,
  role,
  userId,
  onOpen,
  onReveal,
}: {
  entry: Entry;
  role: Role;
  userId: string;
  onOpen: (e: Entry) => void;
  onReveal: (e: Entry) => void;
}) {
  const canReveal =
    role === "dm" &&
    entry.board === "lore" &&
    entry.visibility === "private" &&
    entry.author_id === userId;

  const reveal = () => {
    if (window.confirm(`Reveal “${entry.title}” to the party? Everyone in the campaign will see it, and the chatbot can use it in their answers.`)) {
      onReveal(entry);
    }
  };

  return (
    <article className="entry-card">
      <button type="button" className="entry-open" onClick={() => onOpen(entry)}>
        <span className="entry-title">{entry.title}</span>
        <span className="entry-meta">
          {entry.board !== "journal" && <span className="kind">{kindLabel(entry.kind)}</span>}
          {entry.session != null && <span className="kind">Session {entry.session}</span>}
        </span>
        <span className="entry-excerpt">{entry.body.slice(0, 160)}</span>
      </button>
      <footer>
        <VisibilityBadge visibility={entry.visibility} />
        {canReveal && (
          <button type="button" className="btn btn-small btn-ghost" onClick={reveal}>
            <EyeIcon /> Reveal to party
          </button>
        )}
      </footer>
    </article>
  );
}
