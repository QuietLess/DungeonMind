"""FastAPI app for the campaign hub. Routes stay thin: every rule lives in service.Hub."""

from __future__ import annotations

from fastapi import FastAPI

from dungeonmind.hub.service import Hub

SESSION_COOKIE = "dm_session"
CSRF_HEADER = "X-DungeonMind"
MAX_BODY_BYTES = 64 * 1024


def create_app(hub: Hub) -> FastAPI:
    app = FastAPI(title="DungeonMind")
    app.state.hub = hub
    return app
