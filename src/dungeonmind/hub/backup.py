"""Consistent, private copies of a hub's databases (M9a contract B1–B3)."""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dungeonmind.hub.db import CAMPAIGN_ID


class BackupError(Exception):
    """A backup that can't be made; the message is meant for the user."""


@dataclass(frozen=True)
class BackupResult:
    folder: Path
    campaigns: int


def _copy(source: Path, target: Path) -> None:
    """SQLite's online backup: a consistent snapshot even while the server writes."""
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    # as_uri() percent-encodes the path: a '?', '#' or '%' in it can't change the URI (R1).
    src = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode = DELETE")  # one self-contained file, no -wal
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError(f"The copy of {source.name} failed its integrity check.")
        finally:
            dst.close()
    finally:
        src.close()
    os.chmod(target, 0o600)


def _inside(path: Path, root: Path) -> bool:
    """Whether `path` is `root` or below it, also on case-insensitive file systems (R4)."""
    for ancestor in (path, *path.parents):
        if ancestor.exists() and os.path.samefile(ancestor, root):
            return True
    return path.is_relative_to(root)


def _describe(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    return "app.db" if rel.parts == ("app.db",) else f"campaign {rel.parts[1]}"


def backup(root: Path, out: Path, now: datetime | None = None) -> BackupResult:
    root, out = Path(root).resolve(), Path(out).resolve()
    if not (root / "app.db").is_file():
        raise BackupError(f"No hub found: {root / 'app.db'} doesn't exist.")
    if _inside(out, root):
        raise BackupError("Put backups outside the hub's folder (--out must be outside --root).")
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    folder = out / f"dungeonmind-backup-{stamp}"
    partial = out / f".{folder.name}.partial"
    out.mkdir(parents=True, exist_ok=True)
    if folder.exists():
        raise BackupError(f"{folder} already exists; not overwriting it.")
    try:
        partial.mkdir(mode=0o700)
    except FileExistsError:
        raise BackupError(f"Another backup is writing {partial}; try again later.") from None
    # R3: everything goes into a hidden .partial folder, renamed only once complete, so a
    # failed run never leaves something that looks like a whole backup.
    try:
        os.chmod(partial, 0o700)  # mkdir's mode is reduced by the umask
        sources = [root / "app.db"]
        for path in sorted((root / "campaigns").glob("*/campaign.db")):
            cid = path.parent.name
            if (
                CAMPAIGN_ID.fullmatch(cid)
                and not path.parent.is_symlink()
                and not path.is_symlink()
            ):
                sources.append(path)
        for source in sources:
            try:
                _copy(source, partial / source.relative_to(root))
            except sqlite3.Error as e:
                raise BackupError(f"Couldn't copy {_describe(source, root)}: {e}") from e
        for sub in [partial / "campaigns", *(partial / "campaigns").glob("*")]:
            if sub.is_dir():
                os.chmod(sub, 0o700)
        partial.rename(folder)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return BackupResult(folder, len(sources) - 1)
