"""LangGraph orchestrator. Behaviour contract: docs/ARCHITECTURE.md › M4 contract."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from dungeonmind.graph.state import LLM, ChatState
from dungeonmind.rag.hybrid import Evidence
from dungeonmind.tools.registry import ToolRegistry


@dataclass
class Deps:
    llm: LLM
    retrieve: Callable[[str], list[Evidence]]
    registry: ToolRegistry
    max_steps: int = 6
    timeout_s: float = 60.0
    clock: Callable[[], float] = field(default=time.monotonic)


def build_graph(deps: Deps) -> Any:
    raise NotImplementedError("M4: build the LangGraph state graph")


def run_chat(
    question: str,
    deps: Deps,
    conversation_id: str | None = None,
    campaign_id: str | None = None,
) -> ChatState:
    raise NotImplementedError("M4: run the graph and return the final state")
