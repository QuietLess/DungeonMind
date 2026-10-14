"""SRD 5.2.1 PDF → heading-aware chunks with page/section provenance.

The SRD PDF uses a consistent typographic scheme, which we exploit instead of
fixed-size windows:

* GillSans-SemiBold at 26/18/14/12 pt are heading levels 1–4 (26 pt is a
  chapter title, 12 pt is a spell / glossary entry).
* 14.8 pt is a stat-block name. It is a leaf (level 5) so that a summoned
  creature's stat block inside a spell doesn't become the parent of the spells
  that follow it.
* 10.5 pt GillSans-SemiBold is a table caption (kept as text, not a heading).
* The running footer ("System Reference Document 5.2.1" + page number) sits
  at the bottom margin and is dropped.
* Pages are two-column; reading order is left column then right column.

Chapters come from the 26 pt titles rather than the PDF outline, whose page
numbers are off by one. The chapter drives `entity_type`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from dungeonmind.schemas import Chunk, EntityType

HEADING_FONT = "GillSans-SemiBold"
HEADING_LEVELS = {26.0: 1, 18.0: 2, 14.0: 3, 12.0: 4, 14.8: 5}
TABLE_CAPTION_SIZE = 10.5
FOOTER_Y = 735  # anything whose baseline is below this is page furniture
COLUMN_SPLIT_X = 300
LINE_TOLERANCE = 2.5  # pt; spans whose baselines differ less are one visual line
CELL_GAP = 4  # pt; running text spans abut, so any real gap is a table cell
TITLE_MIN_SIZE = 14.0  # spans this large are titles and get their own visual line
DOT_LEADER = re.compile(r"\.{4,}\s*\d*$")
MAX_CHUNK_WORDS = 450

CHAPTER_ENTITY = {
    "Legal Information": EntityType.LEGAL,
    "Playing the Game": EntityType.RULE,
    "Character Creation": EntityType.RULE,
    "Classes": EntityType.CLASS,
    "Character Origins": EntityType.ORIGIN,
    "Feats": EntityType.FEAT,
    "Equipment": EntityType.EQUIPMENT,
    "Spells": EntityType.SPELL,
    "Rules Glossary": EntityType.GLOSSARY,
    "Gameplay Toolbox": EntityType.TOOLBOX,
    "Magic Items": EntityType.MAGIC_ITEM,
    "Monsters": EntityType.MONSTER,
    "Monsters A–Z": EntityType.MONSTER,
    "Animals": EntityType.MONSTER,
}


@dataclass
class Line:
    page: int
    column: int
    baseline: float
    x0: float
    text: str
    size: float = 0.0
    heading_level: int | None = None
    is_caption: bool = False
    starts_bold: bool = False  # run-in labels: "Speed 0.", "Skills", "Traits"


@dataclass
class Section:
    heading_path: list[str]
    page_start: int
    page_end: int
    paragraphs: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    return (
        text.replace("­", "")
        .replace("\t", " ")
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
    )


def _heading_level(size: float, font: str) -> int | None:
    if not font.startswith(HEADING_FONT):  # includes the small-caps "-SC700" cut
        return None
    return HEADING_LEVELS.get(round(size, 1))


def extract_lines(doc: pymupdf.Document) -> list[Line]:
    """Return visual lines in reading order across the whole document."""
    lines: list[Line] = []
    for page_index, page in enumerate(doc):
        spans = []
        for block in page.get_text("dict")["blocks"]:
            for raw_line in block.get("lines", []):
                for span in raw_line["spans"]:
                    if not span["text"].strip():
                        continue
                    x0, y = span["bbox"][0], span["origin"][1]
                    if y > FOOTER_Y:
                        continue
                    spans.append((0 if x0 < COLUMN_SPLIT_X else 1, y, x0, span))
        spans.sort(key=lambda s: (s[0], s[1], s[2]))

        def is_title(item: tuple) -> bool:
            return item[3]["size"] >= TITLE_MIN_SIZE

        groups: list[list[tuple]] = []
        for item in spans:
            last = groups[-1] if groups else None
            if (
                last
                and last[0][0] == item[0]
                and abs(last[0][1] - item[1]) <= LINE_TOLERANCE
                and is_title(last[0]) == is_title(item)  # a big title never shares a line
            ):
                last.append(item)
            else:
                groups.append([item])

        for group in groups:
            group.sort(key=lambda s: s[2])
            parts, prev_x1 = [], None
            for _, _, x0, span in group:
                text = _normalize(span["text"])
                if prev_x1 is not None:
                    gap = x0 - prev_x1
                    if gap > CELL_GAP:
                        parts.append(" | ")
                    elif gap > 0.5 and not parts[-1].endswith(" ") and not text.startswith(" "):
                        parts.append(" ")
                parts.append(text)
                prev_x1 = span["bbox"][2]
            text = re.sub(r"\s+", " ", "".join(parts)).strip()
            if DOT_LEADER.search(text):  # table-of-contents entry
                continue
            first = group[0][3]
            lines.append(
                Line(
                    page=page_index + 1,
                    column=group[0][0],
                    baseline=group[0][1],
                    x0=group[0][2],
                    text=text,
                    size=first["size"],
                    heading_level=_heading_level(first["size"], first["font"])
                    if len(group) == 1 or all(g[3]["font"].startswith(HEADING_FONT) for g in group)
                    else None,
                    is_caption=first["font"] == HEADING_FONT
                    and round(first["size"], 1) == TABLE_CAPTION_SIZE,
                    starts_bold="Bold" in first["font"] or first["font"].startswith(HEADING_FONT),
                )
            )
    return lines


def _join_wrapped(prev: str, nxt: str) -> str:
    if prev.endswith("-") and nxt[:1].islower():
        return prev[:-1] + nxt
    return f"{prev} {nxt}"


SKIP_CHAPTERS = {"Contents", "Index of Stat Blocks"}  # navigation, not rules


def build_sections(lines: list[Line]) -> list[Section]:
    sections: list[Section] = []
    path: list[str] = []
    current: Section | None = None
    prev: Line | None = None

    def start_section(level: int, title: str, page: int) -> None:
        nonlocal current, path
        # Pad skipped levels with the parent title, then collapse duplicates so
        # a 12 pt spell directly under an 18 pt heading reads ["Spells", "Spell
        # Descriptions", "Fireball"] rather than repeating "Spell Descriptions".
        parent = path[: level - 1]
        if level > 1 and not parent:
            parent = ["Front Matter"]
        while len(parent) < level - 1:
            parent.append(parent[-1])
        path = [*parent, title]
        deduped = [p for i, p in enumerate(path) if i == 0 or p != path[i - 1]]
        current = Section(heading_path=deduped, page_start=page, page_end=page)
        sections.append(current)

    for line in lines:
        if line.heading_level is not None:
            title = line.text.replace(" | ", " ")  # display headings are letter-spaced
            continues_heading = (
                prev is not None
                and prev.heading_level == line.heading_level
                and prev.page == line.page
                and prev.column == line.column
                and line.baseline - prev.baseline < line.size * 1.5
            )
            if continues_heading:  # a long title wrapped onto a second line
                path[-1] = f"{path[-1]} {title}"
                current.heading_path[-1] = path[-1]
            else:
                start_section(line.heading_level, title, line.page)
            prev = line
            continue

        if current is None:
            start_section(1, "Front Matter", line.page)
        current.page_end = line.page
        text = f"Table: {line.text}" if line.is_caption else line.text

        same_flow = prev is not None and line.page == prev.page and line.column == prev.column
        table_cell_wrap = (
            same_flow
            and " | " in prev.text
            and " | " not in text
            and line.x0 - prev.x0 > 20
            and line.baseline - prev.baseline < 14
        )
        # Stat-block fields use a hanging indent when they wrap ("Immunities Poison,
        # Psychic; Charmed, Exhaustion,\n  Frightened, …"), which looks like a new paragraph.
        hanging_wrap = (
            same_flow
            and prev.text.endswith(",")
            and not line.starts_bold
            and line.baseline - prev.baseline < 14
        )
        new_paragraph = not (table_cell_wrap or hanging_wrap) and (
            prev is None
            or prev.heading_level is not None
            or line.is_caption
            or prev.is_caption
            or line.starts_bold
            or " | " in text  # table rows / stat-block grids stay one per line
            or " | " in prev.text
            or (same_flow and line.baseline - prev.baseline > 17)  # vertical gap
            or (same_flow and line.x0 - prev.x0 > 4)  # first-line indent
        )
        if new_paragraph or not current.paragraphs:
            current.paragraphs.append(text)
        else:
            current.paragraphs[-1] = _join_wrapped(current.paragraphs[-1], text)
        prev = line

    return [s for s in sections if s.paragraphs and s.heading_path[0] not in SKIP_CHAPTERS]


def _slug(parts: list[str]) -> str:
    joined = "/".join(parts).lower()
    return re.sub(r"[^a-z0-9/]+", "-", joined).strip("-")


def sections_to_chunks(sections: list[Section]) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: dict[str, int] = {}
    for section in sections:
        chapter = section.heading_path[0]
        entity = CHAPTER_ENTITY.get(chapter, EntityType.RULE)

        # Split oversized sections on paragraph boundaries.
        parts: list[list[str]] = [[]]
        words = 0
        for para in section.paragraphs:
            n = len(para.split())
            if parts[-1] and words + n > MAX_CHUNK_WORDS:
                parts.append([])
                words = 0
            parts[-1].append(para)
            words += n

        base = "srd521:" + _slug(section.heading_path)
        seen[base] = seen.get(base, 0) + 1
        if seen[base] > 1:
            base = f"{base}~{seen[base]}"
        for i, paras in enumerate(parts):
            chunk_id = base if len(parts) == 1 else f"{base}:part{i + 1}"
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    chapter=chapter,
                    heading_path=section.heading_path,
                    entity_type=entity,
                    page_start=section.page_start,
                    page_end=section.page_end,
                    text="\n\n".join(paras),
                )
            )
    return chunks


def ingest(pdf_path: Path) -> list[Chunk]:
    doc = pymupdf.open(pdf_path)
    return sections_to_chunks(build_sections(extract_lines(doc)))


def write_jsonl(chunks: list[Chunk], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk.model_dump(mode="json"), ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[Chunk]:
    with path.open(encoding="utf-8") as f:
        return [Chunk.model_validate_json(line) for line in f if line.strip()]
