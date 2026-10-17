// Entry bodies as Markdown, rendered safely (M9c UB3). Same rule as answers (M7 UI3): only
// React text and a few plain elements, never <a>, <img> or raw HTML, so a note can't link out,
// load anything, or inject markup. URLs and HTML in a note stay visible as plain text.
import { Fragment, type ReactNode } from "react";

// Emphasis only at word edges (R11): file_name_here and 2*3*4 stay as written. The character
// before an opening * or _ is matched (and given back as text) instead of using a lookbehind,
// which Safari before 16.4 can't parse.
const INLINE =
  /\*\*([^*\n]+)\*\*|`([^`\n]+)`|(^|[^\p{L}\p{N}*])\*([^*\s](?:[^*\n]*[^*\s])?)\*(?![\p{L}\p{N}*])|(^|[^\p{L}\p{N}_])_([^_\s](?:[^_\n]*[^_\s])?)_(?![\p{L}\p{N}_])/gu;
const HEADING = /^(#{1,3})\s+(.*)$/;
const BULLET = /^\s*[-*•]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;
const QUOTE = /^\s*>\s?/;

type Kind = "bullet" | "numbered" | "quote" | "para";

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const m of text.matchAll(INLINE)) {
    const start = m.index ?? 0;
    if (start > last) out.push(text.slice(last, start));
    const k = `${key}-${n++}`;
    if (m[1] !== undefined) out.push(<strong key={k}>{m[1]}</strong>);
    else if (m[2] !== undefined) out.push(<code key={k}>{m[2]}</code>);
    else {
      const before = m[3] ?? m[5];
      if (before) out.push(before);
      out.push(<em key={k}>{m[4] ?? m[6]}</em>);
    }
    last = start + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function kindOf(line: string): Kind {
  if (BULLET.test(line)) return "bullet";
  if (NUMBERED.test(line)) return "numbered";
  if (QUOTE.test(line)) return "quote";
  return "para";
}

export function NoteText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let group: { kind: Kind; lines: string[] } | null = null;

  const flush = () => {
    if (!group) return;
    const key = `b${blocks.length}`;
    const { kind, lines } = group;
    if (kind === "bullet" || kind === "numbered") {
      const strip = kind === "bullet" ? BULLET : NUMBERED;
      const items = lines.map((l, i) => <li key={i}>{inline(l.replace(strip, ""), `${key}-${i}`)}</li>);
      // R3: keep the first number ("3. oturumda…" is an ordinal in Turkish, not item 1).
      const start = kind === "numbered" ? Number.parseInt(lines[0], 10) : 1;
      blocks.push(
        kind === "bullet" ? (
          <ul key={key}>{items}</ul>
        ) : (
          <ol key={key} start={start !== 1 ? start : undefined}>
            {items}
          </ol>
        ),
      );
    } else {
      const strip = kind === "quote" ? QUOTE : /^/;
      const body = lines.map((l, i) => (
        <Fragment key={i}>
          {i > 0 && <br />}
          {inline(l.replace(strip, ""), `${key}-${i}`)}
        </Fragment>
      ));
      blocks.push(kind === "quote" ? <blockquote key={key}>{body}</blockquote> : <p key={key}>{body}</p>);
    }
    group = null;
  };

  for (const line of text.split("\n")) {
    if (!line.trim()) {
      flush();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      const key = `b${blocks.length}`;
      const content = inline(heading[2], key);
      const level = heading[1].length;
      blocks.push(
        level === 1 ? <h3 key={key}>{content}</h3> : level === 2 ? <h4 key={key}>{content}</h4> : <h5 key={key}>{content}</h5>,
      );
      continue;
    }
    const kind = kindOf(line);
    if (group && group.kind !== kind) flush();
    if (!group) group = { kind, lines: [] };
    group.lines.push(line);
  }
  flush();
  return <div className="entry-body">{blocks}</div>;
}
