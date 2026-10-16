"""SQLite storage for the hub: app.db (accounts, campaigns) and one campaign.db per campaign.

Each campaign lives in its own folder, `<root>/campaigns/<campaign_id>/campaign.db`, so no
query, index or cache can span two campaigns. Campaign ids are server-generated hex and are
checked before any path is built; symlinked campaign folders or files are refused.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from dungeonmind.hub.errors import HubError, NotFound

CAMPAIGN_ID = re.compile(r"[0-9a-f]{32}")
BUSY_TIMEOUT_MS = 5_000

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    dm_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('dm', 'player')),
    joined_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, user_id)
);
CREATE TABLE IF NOT EXISTS invites (
    code_hash TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    user_id TEXT NOT NULL,
    day TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (user_id, day)
);
"""

# AUTOINCREMENT: ids are never reused, so a saved citation's entry_id can't later point at a
# different entry.
CAMPAIGN_SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    board TEXT NOT NULL CHECK (board IN ('lore', 'character', 'journal')),
    character_id INTEGER REFERENCES characters(id),
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('private', 'dm', 'party')),
    session INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    text TEXT NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (entry_id, seq)
);
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text TEXT NOT NULL,
    meta TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS encounter_state (
    thread_id INTEGER PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
    request TEXT NOT NULL
);
"""


class StorageError(HubError):
    """The hub's files are in a state it refuses to use (e.g. a symlinked campaign folder)."""

    status = 500


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


@contextmanager
def transaction(path: Path, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    """One connection, one transaction: committed on success, rolled back on error, closed."""
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db(path: Path, schema: str) -> None:
    conn = _connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(schema)
    finally:
        conn.close()


class Storage:
    """Paths and schema for one hub root."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.campaigns_root = self.root / "campaigns"
        self.root.mkdir(parents=True, exist_ok=True)
        self.campaigns_root.mkdir(exist_ok=True)
        self.app_db = self.root / "app.db"
        init_db(self.app_db, APP_SCHEMA)

    def app(self, immediate: bool = False):
        return transaction(self.app_db, immediate)

    def campaign_folder(self, cid: str) -> Path:
        """The folder for a well-formed campaign id, refusing anything that escapes the root."""
        if not isinstance(cid, str) or not CAMPAIGN_ID.fullmatch(cid):
            raise NotFound("Campaign not found.")
        folder = self.campaigns_root / cid
        if folder.is_symlink() or (
            folder.exists() and folder.resolve().parent != self.campaigns_root.resolve()
        ):
            raise StorageError("This campaign's storage can't be used.")
        return folder

    def campaign_db(self, cid: str) -> Path:
        folder = self.campaign_folder(cid)
        path = folder / "campaign.db"
        if not folder.is_dir() or path.is_symlink() or not path.is_file():
            raise StorageError("This campaign's storage can't be used.")
        return path

    def create_campaign_db(self, cid: str) -> None:
        folder = self.campaign_folder(cid)
        folder.mkdir(mode=0o700)
        init_db(folder / "campaign.db", CAMPAIGN_SCHEMA)

    def campaign(self, cid: str, immediate: bool = False):
        return transaction(self.campaign_db(cid), immediate)
