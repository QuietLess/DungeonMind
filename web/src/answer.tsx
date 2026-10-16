// Bot answers, rendered safely (M7 UI3, closes H1) with clickable citations (UI4).
//
// This is deliberately not a Markdown renderer. It knows paragraphs, line breaks, simple lists
// and **bold**, and produces only React text and a few plain elements, never <a>, <img> or
// raw HTML. A note can carry instructions into the DM's own chat; without links or images
// there is no channel through which an answer could send anything elsewhere.
import { Fragment, type ReactNode } from "react";
import type { Citation } from "./types";

const INLINE = /\*\*([^*\n]+)\*\*|\[([ETN]\d+(?:\s*,\s*[ETN]\d+)*)\]/g;
const BULLET = /^\s*[-*•]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;

interface Props {
  text: string;
  citations: Citation[];
  onCitation: (citation: Citation) => void;
}

export function AnswerText({ text, citations, onCitation }: Props) {
  const byId = new Map(citations.map((c) => [c.id, c]));
  const blocks = text.split(/\n\s*\n/).filter((b) => b.trim());

  const inline = (line: string, key: string): ReactNode[] => {
    const out: ReactNode[] = [];
    let last = 0;
    let n = 0;
    for (const match of line.matchAll(INLINE)) {
      const start = match.index ?? 0;
      if (start > last) out.push(line.slice(last, start));
      if (match[1] !== undefined) {
        out.push(<strong key={`${key}-b${n++}`}>{match[1]}</strong>);
      } else {
        const ids = match[2].split(",").map((s) => s.trim());
        if (!ids.some((id) => byId.has(id))) {
          out.push(match[0]); // no matching citation: keep the marker as plain text
        } else {
          ids.forEach((id) => {
            const citation = byId.get(id);
            out.push(
              citation ? (
                <button
                  key={`${key}-c${n++}`}
                  type="button"
                  className={`cite cite-${citation.kind}`}
                  aria-label={`${id}: ${citation.label}`}
                  title={citation.label}
                  onClick={() => onCitation(citation)}
                >
                  {id}
                </button>
              ) : (
                <Fragment key={`${key}-u${n++}`}>{` ${id} `}</Fragment>
              ),
            );
          });
        }
      }
      last = start + match[0].length;
    }
    if (last < line.length) out.push(line.slice(last));
    return out;
  };

  return (
    <div className="answer">
      {blocks.map((block, i) => {
        const lines = block.split("\n").filter((l) => l.trim());
        if (lines.every((l) => BULLET.test(l))) {
          return (
            <ul key={i}>
              {lines.map((l, j) => (
                <li key={j}>{inline(l.replace(BULLET, ""), `${i}-${j}`)}</li>
              ))}
            </ul>
          );
        }
        if (lines.every((l) => NUMBERED.test(l))) {
          return (
            <ol key={i}>
              {lines.map((l, j) => (
                <li key={j}>{inline(l.replace(NUMBERED, ""), `${i}-${j}`)}</li>
              ))}
            </ol>
          );
        }
        return (
          <p key={i}>
            {lines.map((l, j) => (
              <Fragment key={j}>
                {j > 0 && <br />}
                {inline(l, `${i}-${j}`)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
