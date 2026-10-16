"""Chat threads and the engine that runs a question through the graph for the hub."""

from __future__ import annotations

from collections.abc import Callable

from dungeonmind.graph.state import ChatState, ConversationStore, NoteSearch

# engine(question, search_notes, store, conversation_id) -> final ChatState
ChatEngine = Callable[[str, NoteSearch, ConversationStore, str], ChatState]


def make_engine(llm, retrieve, registry, **deps_kw) -> ChatEngine:
    raise NotImplementedError
