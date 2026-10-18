"""Hub records, board rules and limits (docs/ARCHITECTURE.md › M6 contract)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Role = Literal["dm", "player"]
Visibility = Literal["private", "dm", "party"]
Board = Literal["lore", "character", "journal"]

VISIBILITIES: tuple[Visibility, ...] = ("private", "dm", "party")
BOARD_KINDS: dict[str, tuple[str, ...]] = {
    "lore": ("npc", "location", "faction", "item", "event", "plot", "other"),
    "character": ("backstory", "goal", "bond", "secret", "relationship", "other"),
    "journal": ("note",),
}
DEFAULT_KIND = {"lore": "other", "character": "other", "journal": "note"}
DEFAULT_VISIBILITY: dict[str, Visibility] = {
    "lore": "private",
    "character": "dm",
    "journal": "party",
}

USERNAME = r"[a-z0-9_]{3,32}"
PASSWORD_CHARS = (10, 200)
CAMPAIGN_NAME_CHARS = 100
CHARACTER_NAME_CHARS = 60
CHARACTER_SUMMARY_CHARS = 500
TITLE_CHARS = 120
BODY_CHARS = 10_000
SESSION_RANGE = (1, 999)
QUESTION_CHARS = 2_000
THREAD_TITLE_CHARS = 120
MAX_ENTRIES = 5_000
MAX_MEMBERS = 200
MAX_CAMPAIGNS_PER_USER = 20  # campaigns a user runs as DM


@dataclass(frozen=True)
class User:
    id: str
    username: str


@dataclass
class Campaign:
    id: str
    name: str
    dm_id: str
    role: Role  # the viewer's role in it
    created_at: datetime


@dataclass
class Member:
    user_id: str
    username: str
    role: Role


@dataclass
class Invite:
    code: str
    expires_at: datetime


@dataclass
class Character:
    id: int
    owner_id: str
    name: str
    summary: str
    is_npc: bool = False


@dataclass
class Entry:
    id: int
    board: Board
    character_id: int | None
    author_id: str
    kind: str
    title: str
    body: str
    visibility: Visibility
    session: int | None
    created_at: datetime
    updated_at: datetime


@dataclass
class Thread:
    id: int
    title: str
    created_at: datetime


@dataclass
class Message:
    id: int
    role: Literal["user", "assistant"]
    text: str
    created_at: datetime
    citations: list[dict[str, Any]] = field(default_factory=list)
    confidence: str | None = None
    insufficient_evidence: bool | None = None
    stop_reason: str | None = None
