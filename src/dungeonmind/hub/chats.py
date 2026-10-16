"""Chat threads and the engine that runs a question through the graph for the hub.

The engine gets a `search_notes` already bound to one campaign and viewer, and a store
already bound to one thread, so the graph can't reach another campaign, user or thread.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from dungeonmind.graph.state import ChatState, ConversationStore, NoteSearch
from dungeonmind.hub.models import Message, Thread
from dungeonmind.tools.encounter_builder import EncounterRequest

# engine(question, search_notes, store, conversation_id) -> final ChatState
ChatEngine = Callable[[str, NoteSearch, ConversationStore, str], ChatState]

APOLOGY = "Sorry, something went wrong while answering this question. Please try again."


def make_engine(llm, retrieve, registry, **deps_kw: Any) -> ChatEngine:
    """The production engine: the M4–M6 graph with the hub's per-request bindings."""
    from dungeonmind.graph.build import Deps, run_chat

    def engine(
        question: str, search_notes: NoteSearch, store: ConversationStore, conversation_id: str
    ) -> ChatState:
        deps = Deps(
            llm=llm,
            retrieve=retrieve,
            registry=registry,
            store=store,
            search_notes=search_notes,
            **deps_kw,
        )
        return run_chat(question, deps, conversation_id=conversation_id)

    return engine


class ThreadEncounterStore:
    """The "make it harder" request for one thread, kept in its campaign.db (closes S4)."""

    def __init__(self, open_db: Callable[..., Any], thread_id: int) -> None:
        self._open = open_db
        self.thread_id = thread_id
        self.last_problem: str | None = None

    def get(self, conversation_id: str) -> EncounterRequest | None:
        self.last_problem = None
        with self._open() as conn:
            row = conn.execute(
                "SELECT request FROM encounter_state WHERE thread_id = ?", (self.thread_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return EncounterRequest.model_validate_json(row["request"])
        except ValidationError:
            self.last_problem = "this thread's saved encounter isn't valid; ignored it"
            return None

    def put(self, conversation_id: str, request: EncounterRequest) -> None:
        with self._open(immediate=True) as conn:
            conn.execute(
                "INSERT INTO encounter_state (thread_id, request) VALUES (?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET request = excluded.request",
                (self.thread_id, request.model_dump_json()),
            )


def reply_fields(state: ChatState) -> dict[str, Any]:
    """What is saved and shown of a run: the answer and its citations, never trace text."""
    stop_reason = state.get("stop_reason", "error")
    if stop_reason == "error":
        return {
            "text": APOLOGY,
            "citations": [],
            "confidence": "low",
            "insufficient_evidence": True,
            "stop_reason": "error",
        }
    return {
        "text": state.get("answer", ""),
        "citations": [dict(c) for c in state.get("citations", [])],
        "confidence": state.get("confidence", "low"),
        "insufficient_evidence": bool(state.get("insufficient_evidence", True)),
        "stop_reason": stop_reason,
    }


def thread_from_row(row: sqlite3.Row) -> Thread:
    from dungeonmind.hub.entries import _dt

    return Thread(id=row["id"], title=row["title"], created_at=_dt(row["created_at"]))


def message_from_row(row: sqlite3.Row) -> Message:
    from dungeonmind.hub.entries import _dt

    meta = json.loads(row["meta"])
    return Message(
        id=row["id"],
        role=row["role"],
        text=row["text"],
        created_at=_dt(row["created_at"]),
        citations=meta.get("citations", []),
        confidence=meta.get("confidence"),
        insufficient_evidence=meta.get("insufficient_evidence"),
        stop_reason=meta.get("stop_reason"),
    )
