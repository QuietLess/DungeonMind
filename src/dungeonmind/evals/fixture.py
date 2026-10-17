"""The fixture campaign as a real hub on a throwaway folder (M8 offline and live runs)."""

from __future__ import annotations

import json
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


PASSWORD = "fixture password"
OWNERS = {"Thorn": "player", "Isolde": "isolde"}  # character → fixture user


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_fixture(root: Path, embedder, engine=None, path: Path = FIXTURE) -> Fixture:
    """A hub with the fixture campaign: the DM wrote the lore, each player their character's
    board, journal entries are by their stated author, and every entry keeps its visibility."""
    data = _load(path)
    hub = Hub(root, embedder=embedder, engine=engine, scrypt_n=2**4, daily_message_limit=100_000)
    users = {name: hub.register(f"fixture_{name}", PASSWORD) for name in ("dm", "player", "isolde")}
    camp = hub.create_campaign(users["dm"], data["campaign"])
    for name in ("player", "isolde"):
        hub.accept_invite(users[name], hub.create_invite(users["dm"], camp.id).code)
    characters = {}
    for char in data["characters"]:
        owner = users[OWNERS[char["name"]]]
        characters[char["name"]] = (owner, hub.create_character(owner, camp.id, **char).id)
    for raw in data["entries"]:
        e = dict(raw)
        author_name = e.pop("author", None)
        character = e.pop("character", None)
        author, character_id = users["dm"], None
        if character:
            author, character_id = characters[character]
        elif author_name:
            author = users[OWNERS[author_name]]
        hub.create_entry(author, camp.id, character_id=character_id, **e)
    return Fixture(hub=hub, cid=camp.id, users=users)


def note_titles(path: Path = FIXTURE) -> set[str]:
    return {e["title"] for e in _load(path)["entries"]}
