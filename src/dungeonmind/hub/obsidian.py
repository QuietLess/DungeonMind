"""Import an Obsidian vault and session notes into a campaign (M8d contract I1–I7).

The vault is only ever read: files are opened for reading and nothing is written, moved or
deleted under it. `plan_import` builds the whole plan in memory (that is what `--preview`
shows); `apply_import` writes the plan into the hub, never back to the vault.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dungeonmind.hub import models

KIND_MAP = {
    "npc": "npc",
    "place": "location",
    "location": "location",
    "faction": "faction",
    "event": "event",
    "item": "item",
    "plot": "plot",
}
CHARACTER_FOLDERS = {"karakterler", "characters", "npcs", "npc", "kisiler", "kişiler"}
HEADER_FIELDS = (
    "aliases",
    "type",
    "status",
    "certainty",
    "role",
    "race",
    "faction",
    "location",
    "importance",
)
SECRET_FIELDS = ("dm_note", "secrets")
# I10: sections whose heading starts with one of these go to the "— DM notes" entry too.
SECRET_HEADINGS = ("DM",)
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)
_EMBED = re.compile(r"!\[\[[^\]]*\]\]|!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
_COMMENT = re.compile(r"<!--.*?-->|%%.*?%%", re.S)
_FIRST_NUMBER = re.compile(r"\d+")
MAX_BODY = models.BODY_CHARS


@dataclass
class PlannedEntry:
    source: str
    board: str
    kind: str
    visibility: str
    title: str
    body: str
    session: int | None = None


@dataclass
class ImportPlan:
    entries: list[PlannedEntry] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (relative path, reason)


def _nfc(text: str) -> str:
    """I9: one Unicode form. File names unzipped on macOS arrive decomposed (NFD), and then
    'Köy' and 'Köy' would be two different titles."""
    return unicodedata.normalize("NFC", text)


def _read(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as f:  # read-only (I1)
        return _nfc(f.read())


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    return (data if isinstance(data, dict) else {}), text[match.end() :]


def _clean(text: str) -> str:
    """I4: Obsidian links to plain text; embeds, images and comments dropped."""
    text = _COMMENT.sub("", text)
    text = _EMBED.sub("", text)
    text = _LINK.sub(lambda m: (m.group(2) or m.group(1)).strip(), text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_value(v) for v in value if _value(v))
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_value(v)}" for k, v in value.items())
    return _clean(str(value))


def _header(meta: dict[str, Any]) -> str:
    fields = [k for k in HEADER_FIELDS if k in meta] + sorted(
        k for k in meta if str(k).startswith("related_")
    )
    lines = [f"**{k}:** {_value(meta[k])}" for k in fields if _value(meta[k])]
    return "\n".join(lines)


def _strip_title_heading(body: str, title: str) -> str:
    first, _, rest = body.partition("\n")
    if first.lstrip("# ").strip() == title:
        return rest.strip()
    return body


def _parts(body: str) -> list[str]:
    """I6: split at paragraph breaks so every part fits the entry limit."""
    if len(body) <= MAX_BODY:
        return [body]
    parts, current = [], ""
    for para in body.split("\n\n"):
        while len(para) > MAX_BODY:  # one huge paragraph: hard split
            if current:
                parts.append(current)
                current = ""
            parts.append(para[:MAX_BODY])
            para = para[MAX_BODY:]
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > MAX_BODY:
            parts.append(current)
            current = para
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _lore_kind(meta: dict[str, Any], rel: Path) -> tuple[str, bool]:
    raw = str(meta.get("kind", "") or "").strip().lower()
    if raw in KIND_MAP:
        return KIND_MAP[raw], False
    if raw == "pc":
        return "other", True
    if not raw and any(p.lower() in CHARACTER_FOLDERS for p in rel.parts[:-1]):
        return "npc", False
    return "other", False


def _title_for(base: str, folder: str, used: dict[str, str], body: str) -> str | None:
    """I6: identical duplicates import once; different text gets a folder suffix."""
    if base not in used:
        used[base] = body
        return base
    if used[base] == body:
        return None
    title = f"{base} ({folder or 'vault'})"
    n = 2
    while title in used:
        title = f"{base} ({folder or 'vault'} {n})"
        n += 1
    used[title] = body
    return title


def _add(
    plan: ImportPlan,
    source: str,
    board: str,
    kind: str,
    title: str,
    body: str,
    session: int | None = None,
    label: str = "",
) -> None:
    pieces = _parts(body)
    for i, piece in enumerate(pieces, 1):
        # Review R10: a long title is shortened, never its label or part number, so
        # "… — DM notes" can't end up with the main entry's exact title.
        tail = label + ("" if len(pieces) == 1 else f" ({i}/{len(pieces)})")
        name = title[: models.TITLE_CHARS - len(tail)] + tail
        plan.entries.append(PlannedEntry(source, board, kind, "private", name, piece, session))


def _fold(text: str) -> str:
    """Caseless compare that also works for Turkish İ ("GİZLİ" matches "Gizli")."""
    return text.casefold().replace("\u0307", "")


def _starts_with_word(text: str, prefixes: tuple[str, ...]) -> bool:
    folded = _fold(text.strip())
    return any(folded.startswith(p) and not folded[len(p) : len(p) + 1].isalnum() for p in prefixes)


def _split_sections(body: str, prefixes: tuple[str, ...]) -> tuple[str, list[str]]:
    """I10: (body without secret sections, the secret sections). A section runs from its
    heading to the next heading of the same or a higher level, subsections included; lines
    inside code fences are never headings (review R4)."""
    wanted = tuple(_fold(p.strip()) for p in prefixes if p.strip())
    kept: list[str] = []
    secret: list[list[str]] = []
    level = 0  # > 0 while inside a secret section of that heading level
    fenced = False
    for line in body.split("\n"):
        heading = None if fenced else _HEADING.match(line)
        if _FENCE.match(line):
            fenced = not fenced
        if heading and level and len(heading.group(1)) <= level:
            level = 0
        if heading and not level and _starts_with_word(heading.group(2), wanted):
            level = len(heading.group(1))
            secret.append([])
        (secret[-1] if level else kept).append(line)
    sections = ["\n".join(lines).strip() for lines in secret]
    return "\n".join(kept).strip(), [x for x in sections if x]


def _add_note(
    plan: ImportPlan,
    path: Path,
    rel: Path,
    used: dict[str, str],
    suffix: str | None = None,
    secret_headings: tuple[str, ...] = SECRET_HEADINGS,
) -> None:
    """One vault (or archive) note → a private lore entry, plus a DM-notes entry for secrets."""
    meta, text = _split_frontmatter(_read(path))
    title = _nfc(path.stem)
    body = _strip_title_heading(_clean(text), title)
    # `dm_note: true` is a flag (the whole note is DM-only); text values are secrets.
    dm_only = meta.get("dm_note") is True
    secrets = [
        f"**{k}:** {_value(meta[k])}"
        for k in SECRET_FIELDS
        if not isinstance(meta.get(k), bool) and _value(meta.get(k))
    ]
    body, sections = _split_sections(body, secret_headings)
    secrets += sections
    if not body and not secrets:
        plan.skipped.append((str(rel), "empty note"))
        return
    kind, is_pc = _lore_kind(meta, rel)
    if is_pc:
        title = f"{title} (PC)"
    marks = ["**DM only:** yes" if dm_only else "", "**source:** archive" if suffix else ""]
    header = "\n".join(x for x in (*marks, _header(meta)) if x)
    main = "\n\n".join(x for x in (header, body) if x) or "(no text)"
    folder = suffix or (rel.parts[-2] if len(rel.parts) > 1 else "")
    # Review R9: notes with the same public text but different secrets aren't duplicates.
    final = _title_for(title, folder, used, "\n\n".join([main, *secrets]))
    if final is None:
        plan.skipped.append((str(rel), f"same text as an earlier note titled {title!r}"))
        return
    _add(plan, str(rel), "lore", kind, final, main)
    if secrets:  # I5: secrets never ride along when the main entry is revealed
        _add(plan, str(rel), "lore", kind, final, "\n\n".join(secrets), label=" — DM notes")


def _session_number(meta: dict[str, Any], stem: str, mode: str) -> int | None:
    """I7: a `session:` field wins; file-name numbers only with mode "name" (reading-order
    prefixes like "01 - …" are not session numbers in the owner's archive)."""
    field_value = meta.get("session")
    if isinstance(field_value, int) and not isinstance(field_value, bool):
        return field_value if 1 <= field_value <= 999 else None
    if mode == "name":
        number = _FIRST_NUMBER.search(stem)
        if number and 1 <= int(number.group()) <= 999:
            return int(number.group())
    return None


def plan_import(
    vault: Path,
    sessions: Path | None = None,
    extra_lore: list[Path] | tuple[Path, ...] = (),
    session_numbers: str = "field",
    secret_headings: tuple[str, ...] | list[str] = SECRET_HEADINGS,
) -> ImportPlan:
    vault = Path(vault)
    plan = ImportPlan()
    used: dict[str, str] = {}
    files = sorted(
        p
        for p in vault.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(vault).parts)
    )
    # Notes in folders come first, so "Karakterler/Mira.md" keeps the plain title "Mira".
    files.sort(key=lambda p: (len(p.relative_to(vault).parts) == 1, str(p)))
    for path in files:
        rel = path.relative_to(vault)
        if path.suffix.lower() != ".md":
            plan.skipped.append((str(rel), f"not a note ({path.suffix or 'no extension'})"))
        elif path.name.endswith(".excalidraw.md"):
            plan.skipped.append((str(rel), "Excalidraw drawing"))
        else:
            _add_note(plan, path, rel, used, secret_headings=tuple(secret_headings))
    for folder in extra_lore:  # I8: archive summaries, top-level notes only
        folder = Path(folder)
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix.lower() == ".md" and not path.name.startswith("."):
                _add_note(
                    plan,
                    path,
                    Path(folder.name) / path.name,
                    used,
                    suffix="arşiv",
                    secret_headings=tuple(secret_headings),
                )
    if sessions is not None:
        for path in sorted(Path(sessions).iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in (".md", ".txt"):
                plan.skipped.append((f"sessions/{path.name}", "not a .md or .txt file"))
                continue
            meta, text = _split_frontmatter(_read(path))
            body = _clean(text)
            if not body:
                plan.skipped.append((f"sessions/{path.name}", "empty note"))
                continue
            header = _header(meta)
            full = "\n\n".join(x for x in (header, body) if x)
            session = _session_number(meta, path.stem, session_numbers)
            _add(plan, f"sessions/{path.name}", "journal", "note", _nfc(path.stem), full, session)
    return plan


def apply_import(hub, plan: ImportPlan, dm_username: str, campaign_name: str):
    """Write the plan into a campaign run by `dm_username` (created if needed)."""
    with hub.storage.app() as db:
        row = db.execute(
            "SELECT id, username FROM users WHERE username = ?", (dm_username,)
        ).fetchone()
    if row is None:
        raise ValueError(f"no hub user named {dm_username!r}; register in the web app first")
    from dungeonmind.hub.models import User

    dm = User(id=row["id"], username=row["username"])
    camp = next((c for c in hub.campaigns(dm) if c.name == campaign_name and c.role == "dm"), None)
    if camp is None:
        camp = hub.create_campaign(dm, campaign_name)
    elif hub.entries(dm, camp.id):
        raise ValueError(f"campaign {campaign_name!r} already has entries; import into a new one")
    for e in plan.entries:
        hub.create_entry(
            dm,
            camp.id,
            board=e.board,
            title=e.title,
            body=e.body,
            kind=e.kind,
            visibility=e.visibility,
            session=e.session,
        )
    return camp, len(plan.entries)
