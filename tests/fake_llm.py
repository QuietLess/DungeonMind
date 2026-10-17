"""Scripted, offline stand-ins for the orchestrator's ports (LLM + retrieve). Test-only."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Iterator
from typing import Any

from dungeonmind.graph.state import (
    AgentTurn,
    EncounterExtraction,
    FinalAnswer,
    NoteHit,
    RouteDecision,
    ToolCall,
    ToolRecord,
)
from dungeonmind.rag.hybrid import Evidence
from dungeonmind.schemas import Chunk, EntityType
from dungeonmind.tools.encounter_builder import EncounterRequest


class FakeLLM:
    """Implements the `LLM` protocol from a script and records every call it receives.

    - `route`: the RouteDecision to return (or an Exception to raise).
    - `final`: the FinalAnswer returned by `answer` (or an Exception to raise).
    - `turns`: AgentTurns (or Exceptions) handed out one per `agent_step` call, in order.
    - `on_step`: optional hook run at the start of each `agent_step` (e.g. to advance a clock).
    - `extraction`: what `extract_encounter` returns (or an Exception to raise).
    - `presentation`: the FinalAnswer `present_encounter` returns (or an Exception to raise).
    - `campaign`: the FinalAnswer `answer_campaign` returns (or an Exception to raise).
    - `standalone`: what `standalone_question` returns (or an Exception to raise).

    The keyword-only `timeout` each method receives is recorded in `timeouts` as
    (method name, timeout), in call order, so `calls` payloads stay unchanged. Likewise the
    keyword-only `revision` that `present_encounter` receives is recorded in `revisions`.
    """

    def __init__(
        self,
        route: RouteDecision | Exception,
        final: FinalAnswer | Exception | None = None,
        turns: Iterable[AgentTurn | Exception] = (),
        on_step: Any = None,
        extraction: EncounterExtraction | Exception | None = None,
        presentation: FinalAnswer | Exception | None = None,
        campaign: FinalAnswer | Exception | None = None,
        standalone: str | Exception | None = None,
    ) -> None:
        self._route = route
        self._final = final
        self._turns: Iterator[AgentTurn | Exception] = iter(turns)
        self._on_step = on_step
        self._extraction = extraction
        self._presentation = presentation
        self._campaign = campaign
        self._standalone = standalone
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.timeouts: list[tuple[str, float | None]] = []
        self.revisions: list[str | None] = []
        self.route_campaign: list[bool] = []  # the `campaign` flag of each route call

    # --- LLM protocol -------------------------------------------------------------------------

    def route(
        self, question: str, *, timeout: float | None = None, campaign: bool = False
    ) -> RouteDecision:
        self.calls.append(("route", {"question": question}))
        self.route_campaign.append(campaign)
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

    def extract_encounter(
        self,
        question: str,
        previous: EncounterRequest | None,
        *,
        timeout: float | None = None,
    ) -> EncounterExtraction:
        self.calls.append(
            ("extract_encounter", {"question": question, "previous": copy.deepcopy(previous)})
        )
        self.timeouts.append(("extract_encounter", timeout))
        if self._extraction is None:
            raise AssertionError("FakeLLM.extract_encounter called but nothing was scripted")
        if isinstance(self._extraction, Exception):
            raise self._extraction
        return self._extraction

    def present_encounter(
        self,
        question: str,
        request: EncounterRequest,
        record: ToolRecord,
        *,
        revision: str | None = None,
        timeout: float | None = None,
    ) -> FinalAnswer:
        self.revisions.append(revision)
        self.calls.append(
            (
                "present_encounter",
                {
                    "question": question,
                    "request": copy.deepcopy(request),
                    "record": copy.deepcopy(record),
                },
            )
        )
        self.timeouts.append(("present_encounter", timeout))
        if self._presentation is None:
            raise AssertionError("FakeLLM.present_encounter called but nothing was scripted")
        if isinstance(self._presentation, Exception):
            raise self._presentation
        return self._presentation

    def answer_campaign(
        self,
        question: str,
        notes: list[NoteHit],
        evidence: list[Evidence],
        *,
        mode: str,
        timeout: float | None = None,
    ) -> FinalAnswer:
        self.calls.append(
            (
                "answer_campaign",
                {
                    "question": question,
                    "notes": copy.deepcopy(list(notes)),
                    "evidence": list(evidence),
                    "mode": mode,
                },
            )
        )
        self.timeouts.append(("answer_campaign", timeout))
        if self._campaign is None:
            raise AssertionError("FakeLLM.answer_campaign called but nothing was scripted")
        if isinstance(self._campaign, Exception):
            raise self._campaign
        return self._campaign

    def standalone_question(
        self, question: str, history: list[dict[str, str]], *, timeout: float | None = None
    ) -> str:
        self.calls.append(
            ("standalone_question", {"question": question, "history": copy.deepcopy(history)})
        )
        self.timeouts.append(("standalone_question", timeout))
        if self._standalone is None:
            raise AssertionError("FakeLLM.standalone_question called but nothing was scripted")
        if isinstance(self._standalone, Exception):
            raise self._standalone
        return self._standalone

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


def note(entry_id: int, text: str = "", source: str | None = None, canon: bool = False) -> NoteHit:
    return NoteHit(
        entry_id=entry_id,
        source=source or f"Journal › Note {entry_id}",
        text=text or f"Campaign note {entry_id}.",
        canon=canon,
        score=1.0,
    )


class FakeNotes:
    """`search_notes` stub: scripted hits per query (default: none); records (query, k)."""

    def __init__(self, by_query: dict[str, list[NoteHit]] | None = None) -> None:
        self.by_query = by_query or {}
        self.calls: list[tuple[str, int]] = []

    def __call__(self, query: str, k: int) -> list[NoteHit]:
        self.calls.append((query, k))
        return list(self.by_query.get(query, []))[:k]


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
