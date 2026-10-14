"""Orchestrator contract: state, routing types and the LLM port.

This file is the interface between the graph (graph/build.py), the real
Claude adapter (graph/llm.py) and the tests (which use a scripted fake LLM).
Change it deliberately: all three depend on it.

Flow (bounded; no free-running agent loop):

    START → route ─┬─ rules_qa ─────────→ retrieve → answer ─┐
                   ├─ lookup/probability/encounter → agent ──┤→ verify → END
                   ├─ campaign_memory/session_prep → unsupported ┤
                   └─ out_of_scope ─────→ decline ───────────┘

Evidence ids are E1, E2, … (retrieved SRD chunks, numbered in order of
retrieval across the whole run). Tool-result ids are T1, T2, … (every tool
call, in order). A final answer may cite only ids that exist in the state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol, TypedDict

from dungeonmind.rag.hybrid import Evidence


class Intent(StrEnum):
    RULES_QA = "rules_qa"  # how a rule works → retrieve + cited answer
    LOOKUP = "lookup"  # spells/monsters by filters or name → tools
    PROBABILITY = "probability"  # odds, dice → tools
    ENCOUNTER = "encounter"  # budgets, building encounters → tools (full builder is M5)
    CAMPAIGN_MEMORY = "campaign_memory"  # the user's own campaign notes → M6, unsupported now
    SESSION_PREP = "session_prep"  # planning from notes → M6, unsupported now
    OUT_OF_SCOPE = "out_of_scope"  # not about running D&D → decline


TOOL_INTENTS = frozenset({Intent.LOOKUP, Intent.PROBABILITY, Intent.ENCOUNTER})
UNSUPPORTED_INTENTS = frozenset({Intent.CAMPAIGN_MEMORY, Intent.SESSION_PREP})

StopReason = Literal["completed", "max_steps", "timeout", "error"]


@dataclass
class RouteDecision:
    intent: Intent
    reason: str
    # True when the user asks about 2014 / 5e "legacy" / another edition's rules.
    other_edition: bool = False


@dataclass
class ToolCall:
    """A tool call requested by the model in one agent turn."""

    call_id: str  # the provider's tool_use id, echoed back with the result
    name: str
    arguments: dict[str, Any]


@dataclass
class FinalAnswer:
    """The model's final structured answer. `citations` lists E#/T# ids."""

    answer: str
    citations: list[str]
    confidence: Literal["high", "medium", "low"]
    insufficient_evidence: bool


@dataclass
class AgentTurn:
    """One model turn in the agent loop: tool calls OR a final answer, never both."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    final: FinalAnswer | None = None
    # Provider content blocks for this turn, appended to `messages` verbatim so the
    # next request carries the assistant turn exactly as returned.
    raw_content: list[Any] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class LLM(Protocol):
    """Everything the graph needs from a model. ClaudeLLM implements it; tests fake it."""

    # `timeout` is the wall-clock budget in seconds for this one call (None = adapter default).

    def route(self, question: str, *, timeout: float | None = None) -> RouteDecision: ...

    def answer(
        self, question: str, evidence: list[Evidence], *, timeout: float | None = None
    ) -> FinalAnswer:
        """Answer from numbered evidence (E1…E{n} in list order)."""
        ...

    def agent_step(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout: float | None = None,
    ) -> AgentTurn:
        """One turn of the bounded tool loop over Anthropic-format `messages`."""
        ...


class ToolRecord(TypedDict):
    id: str  # T1, T2, …
    name: str
    arguments: dict[str, Any]
    output: dict[str, Any]  # {"result": …} or {"error": …} from ToolRegistry.call
    is_error: bool


class CitationRef(TypedDict):
    id: str  # E# or T#
    kind: Literal["srd", "tool"]
    label: str  # human-readable source, e.g. "SRD 5.2.1, … p. 182" or "probability tool"


class TraceEvent(TypedDict):
    node: str
    detail: str
    ms: float


class ChatState(TypedDict, total=False):
    # Inputs
    question: str
    conversation_id: str
    campaign_id: str | None
    # Routing
    intent: Intent
    route_reason: str
    other_edition: bool
    # Gathered material
    evidence: list[Evidence]  # index i ↔ id f"E{i + 1}"
    tool_calls: list[ToolRecord]
    messages: list[dict[str, Any]]  # agent conversation (Anthropic format)
    steps: int  # agent turns taken
    # Output
    answer: str
    citations: list[CitationRef]
    confidence: Literal["high", "medium", "low"]
    insufficient_evidence: bool
    violations: list[str]  # citation-contract breaches found by verify
    stop_reason: StopReason
    error_type: str | None  # exception class name when stop_reason == "error"
    trace: list[TraceEvent]
