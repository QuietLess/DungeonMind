"""Shared set-up for hub tests: a Hub on a temp folder with fast hashing and a fake embedder."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fake_embedder import HashEmbedder

from dungeonmind.hub.service import Hub

PASSWORD = "correct horse battery"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def make_hub(root, engine=None, embedder=None, clock=None, **kw) -> Hub:
    return Hub(
        root,
        embedder=embedder or HashEmbedder(),
        engine=engine,
        clock=clock or Clock(),
        scrypt_n=2**4,
        **kw,
    )


def party(hub: Hub):
    """A campaign with a DM, two players (p1, p2) and an ex-member (p3); plus an outsider."""
    dm = hub.register("dm_dana", PASSWORD)
    p1 = hub.register("player_one", PASSWORD)
    p2 = hub.register("player_two", PASSWORD)
    p3 = hub.register("player_gone", PASSWORD)
    outsider = hub.register("outsider", PASSWORD)
    camp = hub.create_campaign(dm, "The Ashvale Saga")
    for p in (p1, p2, p3):
        hub.accept_invite(p, hub.create_invite(dm, camp.id).code)
    return camp, dm, p1, p2, p3, outsider
