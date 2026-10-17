"""Import an Obsidian vault and session notes into a campaign (M8d contract I1–I7). Stub (RED)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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


def plan_import(vault: Path, sessions: Path | None = None) -> ImportPlan:
    raise NotImplementedError


def apply_import(hub, plan: ImportPlan, dm_username: str, campaign_name: str):
    raise NotImplementedError
