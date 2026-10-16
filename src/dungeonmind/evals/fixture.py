"""The fixture campaign as a real hub on a throwaway folder (M8 offline and live runs)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dungeonmind.hub.models import User
from dungeonmind.hub.service import Hub

FIXTURE = Path(__file__).parent / "datasets" / "notes_v0.json"


@dataclass
class Fixture:
    hub: Hub
    cid: str
    users: dict[str, User]  # "dm", "player" (Thorn's player), "isolde"


def build_fixture(root: Path, embedder, engine=None, path: Path = FIXTURE) -> Fixture:
    raise NotImplementedError


def note_titles(path: Path = FIXTURE) -> set[str]:
    raise NotImplementedError
