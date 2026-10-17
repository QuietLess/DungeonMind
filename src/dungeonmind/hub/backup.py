"""Consistent, private copies of a hub's databases (M9a contract B1–B3)."""

from __future__ import annotations

import os
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
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
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


def backup(root: Path, out: Path, now: datetime | None = None) -> BackupResult:
    root, out = Path(root).resolve(), Path(out).resolve()
    if not (root / "app.db").is_file():
        raise BackupError(f"No hub found: {root / 'app.db'} doesn't exist.")
    if out == root or out.is_relative_to(root):
        raise BackupError("Put backups outside the hub's folder (--out must be outside --root).")
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    folder = out / f"dungeonmind-backup-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    try:
        folder.mkdir(mode=0o700)
    except FileExistsError:
        raise BackupError(f"{folder} already exists; not overwriting it.") from None
    os.chmod(folder, 0o700)  # mkdir's mode is reduced by the umask
    _copy(root / "app.db", folder / "app.db")
    campaigns = 0
    for path in sorted((root / "campaigns").glob("*/campaign.db")):
        cid = path.parent.name
        if not CAMPAIGN_ID.fullmatch(cid) or path.parent.is_symlink() or path.is_symlink():
            continue
        _copy(path, folder / "campaigns" / cid / "campaign.db")
        campaigns += 1
    for sub in [folder / "campaigns", *(folder / "campaigns").glob("*")]:
        if sub.is_dir():
            os.chmod(sub, 0o700)
    return BackupResult(folder, campaigns)
