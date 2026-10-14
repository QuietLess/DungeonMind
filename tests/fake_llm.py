"""Scripted, offline stand-ins for the orchestrator's ports (LLM + retrieve). Test-only."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Iterator
from typing import Any

from dungeonmind.graph.state import AgentTurn, FinalAnswer, RouteDecision, ToolCall
from dungeonmind.rag.hybrid import Evidence
from dungeonmind.schemas import Chunk, EntityType


class FakeLLM:
    """Implements the `LLM` protocol from a script and records every call it receives.

    - `route`: the RouteDecision to return (or an Exception to raise).
    - `final`: the FinalAnswer returned by `answer` (or an Exception to raise).
    - `turns`: AgentTurns (or Exceptions) handed out one per `agent_step` call, in order.
    - `on_step`: optional hook run at the start of each `agent_step` (e.g. to advance a clock).

    The keyword-only `timeout` each method receives is recorded in `timeouts` as
    (method name, timeout), in call order, so `calls` payloads stay unchanged.
    """

    def __init__(
        self,
        route: RouteDecision | Exception,
        final: FinalAnswer | Exception | None = None,
        turns: Iterable[AgentTurn | Exception] = (),
        on_step: Any = None,
    ) -> None:
        self._route = route
        self._final = final
        self._turns: Iterator[AgentTurn | Exception] = iter(turns)
        self._on_step = on_step
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.timeouts: list[tuple[str, float | None]] = []

    # --- LLM protocol -------------------------------------------------------------------------

    def route(self, question: str, *, timeout: float | None = None) -> RouteDecision:
        self.calls.append(("route", {"question": question}))
        self.timeouts.append(("route", timeout))
        if isinstance(self._route, Exception):
            raise self._route
        return self._route

    def answer(
        self, question: str, evidence: list[Evidence], *, timeout: float | None = None
    ) -> FinalAnswer:
        self.calls.append(("answer", {"question": question, "evidence": list(evidence)}))
        self.timeouts.append(("answer", timeout))
        if self._final is None:
            raise AssertionError("FakeLLM.answer called but no final answer was scripted")
        if isinstance(self._final, Exception):
            raise self._final
        return self._final

    def agent_step(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout: float | None = None,
    ) -> AgentTurn:
        self.timeouts.append(("agent_step", timeout))
        # Deep-copy: the graph may keep mutating the same list after this call returns.
        self.calls.append(
            ("agent_step", {"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        )
        if self._on_step is not None:
            self._on_step()
        try:
            turn = next(self._turns)
        except StopIteration:
            raise AssertionError("FakeLLM.agent_step called more often than scripted") from None
        if isinstance(turn, Exception):
            raise turn
        return turn

    # --- helpers for assertions ---------------------------------------------------------------

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def step_calls(self) -> list[dict[str, Any]]:
        return [payload for name, payload in self.calls if name == "agent_step"]


def tool_turn(*calls: tuple[str, str, dict[str, Any]]) -> AgentTurn:
    """An agent turn requesting tools: each call is (call_id, name, arguments)."""
    tool_calls = [ToolCall(call_id=cid, name=name, arguments=args) for cid, name, args in calls]
    raw = [
        {"type": "tool_use", "id": cid, "name": name, "input": args} for cid, name, args in calls
    ]
    return AgentTurn(tool_calls=tool_calls, raw_content=raw)


def final_turn(answer: FinalAnswer) -> AgentTurn:
    return AgentTurn(final=answer, raw_content=[{"type": "text", "text": answer.answer}])


def final(
    answer: str,
    citations: list[str] | None = None,
    confidence: str = "high",
    insufficient: bool = False,
) -> FinalAnswer:
    return FinalAnswer(
        answer=answer,
        citations=list(citations or []),
        confidence=confidence,  # type: ignore[arg-type]
        insufficient_evidence=insufficient,
    )


def evidence(slug: str, text: str | None = None, page: int = 100) -> Evidence:
    return Evidence(
        chunk=Chunk(
            chunk_id=f"srd521:test/{slug}",
            chapter="Rules Glossary",
            heading_path=["Rules Glossary", slug.title()],
            entity_type=EntityType.GLOSSARY,
            page_start=page,
            page_end=page,
            text=text if text is not None else f"Rule text for {slug}.",
        ),
        score=1.0,
        rank=1,
    )


class FakeRetriever:
    """`retrieve` stub: returns scripted evidence per query (default: empty) and records queries."""

    def __init__(self, by_query: dict[str, list[Evidence]] | None = None) -> None:
        self.by_query = by_query or {}
        self.queries: list[str] = []

    def __call__(self, query: str) -> list[Evidence]:
        self.queries.append(query)
        return list(self.by_query.get(query, []))
