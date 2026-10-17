// One entry in a board list: title, kind, visibility, and the DM's Reveal action (M7 UI6).
import { EyeIcon } from "../icons";
import type { Entry, Role } from "../types";
import { VisibilityBadge } from "../visibility";

const KIND_LABEL: Record<string, string> = { npc: "NPC", sheet: "Character sheet" };

/** L2: a card preview as plain text, without Markdown markers. */
export function plainExcerpt(body: string, max = 220): string {
  const text = body
    .slice(0, 1500) // a preview never needs more; keeps typing in the filter smooth
    .split("\n")
    .filter((line) => !/^\s*(?:-{3,}|\*{3,}|_{3,}|\|?[\s:|-]*-{3,}[\s:|-]*\|?)\s*$/.test(line))
    .map((line) =>
      line
        .replace(/^\s{0,3}#{1,6}\s+/, "")
        .replace(/^\s*>\s?/, "")
        .replace(/^\s*(?:[-*•]|\d+[.)])\s+/, "")
        .replace(/\|/g, " "),
    )
    .join(" ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1") // [text](url) → text
    .replace(/\*\*|`/g, "")
    .replace(/(^|[^\p{L}\p{N}])[*_]+|[*_]+(?=[^\p{L}\p{N}]|$)/gu, "$1") // only at word edges
    .replace(/\s+/g, " ")
    .trim();
  const chars = Array.from(text); // never split an emoji
  return chars.length > max ? `${chars.slice(0, max - 1).join("")}…` : text;
}

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind.charAt(0).toUpperCase() + kind.slice(1);
}

/** The Reveal confirmation; warns when a DM section is still inside the text (M9f). */
export function revealPrompt(entry: Entry): string {
  const dmSection = /^#{1,6}\s*(DM\b.*)$/im.exec(entry.body)?.[1];
  const warning = dmSection
    ? `\n\nCareful: this entry still contains “${dmSection.trim()}”. Players will see that section too.`
    : "";
  return `Reveal “${entry.title}” to the party? Everyone in the campaign will see it, and the chatbot can use it in their answers.${warning}`;
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
    if (window.confirm(revealPrompt(entry))) {
      onReveal(entry);
    }
  };

  return (
    <article className={`entry-card entry-${entry.kind}`}>
      <button type="button" className="entry-open" onClick={() => onOpen(entry)}>
        <span className="entry-title">{entry.title}</span>
        <span className="entry-meta">
          {entry.board !== "journal" && (
            <span className={`kind kind-${entry.kind}`}>{kindLabel(entry.kind)}</span>
          )}
          {entry.session != null && <span className="kind">Session {entry.session}</span>}
        </span>
        <span className="entry-excerpt">{plainExcerpt(entry.body)}</span>
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
