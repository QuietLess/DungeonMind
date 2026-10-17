"""Import an Obsidian vault and session notes into a campaign (M8d contract I1–I7).

The vault is only ever read: files are opened for reading and nothing is written, moved or
deleted under it. `plan_import` builds the whole plan in memory (that is what `--preview`
shows); `apply_import` writes the plan into the hub, never back to the vault.
"""

from __future__ import annotations

import re
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
HEADER_FIELDS = ("aliases", "status", "role", "race", "faction", "location", "importance")
SECRET_FIELDS = ("dm_note", "secrets")
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


def _read(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as f:  # read-only (I1)
        return f.read()


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
) -> None:
    pieces = _parts(body)
    for i, piece in enumerate(pieces, 1):
        name = title if len(pieces) == 1 else f"{title} ({i}/{len(pieces)})"
        plan.entries.append(
            PlannedEntry(source, board, kind, "private", name[: models.TITLE_CHARS], piece, session)
        )


def plan_import(vault: Path, sessions: Path | None = None) -> ImportPlan:
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
            continue
        if path.name.endswith(".excalidraw.md"):
            plan.skipped.append((str(rel), "Excalidraw drawing"))
            continue
        meta, text = _split_frontmatter(_read(path))
        title = path.stem
        body = _strip_title_heading(_clean(text), title)
        # `dm_note: true` is a flag (the whole note is DM-only); text values are secrets.
        dm_only = meta.get("dm_note") is True
        secrets = [
            f"**{k}:** {_value(meta[k])}"
            for k in SECRET_FIELDS
            if not isinstance(meta.get(k), bool) and _value(meta.get(k))
        ]
        if not body and not secrets:
            plan.skipped.append((str(rel), "empty note"))
            continue
        kind, is_pc = _lore_kind(meta, rel)
        if is_pc:
            title = f"{title} (PC)"
        header = "\n".join(x for x in ("**DM only:** yes" if dm_only else "", _header(meta)) if x)
        main = "\n\n".join(x for x in (header, body) if x) or "(no text)"
        folder = rel.parts[-2] if len(rel.parts) > 1 else ""
        final = _title_for(title, folder, used, main)
        if final is None:
            plan.skipped.append((str(rel), f"same text as an earlier note titled {title!r}"))
            continue
        _add(plan, str(rel), "lore", kind, final, main)
        if secrets:  # I5: secrets never ride along when the main entry is revealed
            _add(plan, str(rel), "lore", kind, f"{final} — DM notes", "\n".join(secrets))
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
            number = _FIRST_NUMBER.search(path.stem)
            session = int(number.group()) if number and 1 <= int(number.group()) <= 999 else None
            _add(plan, f"sessions/{path.name}", "journal", "note", path.stem, body, session)
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
