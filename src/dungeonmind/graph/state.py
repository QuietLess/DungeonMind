"""Orchestrator contract: state, routing types and the LLM port.

This file is the interface between the graph (graph/build.py), the real
Claude adapter (graph/llm.py) and the tests (which use a scripted fake LLM).
Change it deliberately: all three depend on it.

Flow (bounded; no free-running agent loop):

    START → route ─┬─ rules_qa ─────────→ retrieve → answer ─┐
                   ├─ lookup/probability/encounter → agent ──┤→ verify → END
                   ├─ campaign_memory/session_prep → campaign ┤
                   └─ out_of_scope ─────→ decline ───────────┘

Evidence ids are E1, E2, … (retrieved SRD chunks, numbered in order of
retrieval across the whole run). Tool-result ids are T1, T2, … (every tool
call, in order). Campaign entries are N1, N2, … (M6: only entries the asking
user may read ever reach the graph). A final answer may cite only ids that
exist in the state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, NotRequired, Protocol, TypedDict

from dungeonmind.rag.hybrid import Evidence
from dungeonmind.tools.encounter_builder import EncounterRequest


class Intent(StrEnum):
    RULES_QA = "rules_qa"  # how a rule works → retrieve + cited answer
    LOOKUP = "lookup"  # spells/monsters by filters or name → tools
    PROBABILITY = "probability"  # odds, dice → tools
    ENCOUNTER = "encounter"  # build or revise encounters → encounter node (M5)
    CAMPAIGN_MEMORY = "campaign_memory"  # the campaign's boards and journal → campaign node (M6)
    SESSION_PREP = "session_prep"  # planning a session from campaign entries → campaign node
    OUT_OF_SCOPE = "out_of_scope"  # not about running D&D → decline


TOOL_INTENTS = frozenset({Intent.LOOKUP, Intent.PROBABILITY})
CAMPAIGN_INTENTS = frozenset({Intent.CAMPAIGN_MEMORY, Intent.SESSION_PREP})

StopReason = Literal["completed", "needs_input", "max_steps", "timeout", "error"]


@dataclass
class RouteDecision:
    intent: Intent
    reason: str
    # True when the user asks about 2014 / 5e "legacy" / another edition's rules.
    other_edition: bool = False


@dataclass
class NoteHit:
    """A campaign entry as the graph sees it (M6). The hub builds these already filtered to
    what the asking user may read; the graph never sees campaign or user ids."""

    entry_id: int
    source: str  # e.g. "Lore › NPC › Elara"; the citation label
    text: str  # the best-matching chunk of the entry
    canon: bool = False  # True for lore-board entries
    score: float = 0.0


# search_notes(query, k): the visible entries best matching `query`, best first.
NoteSearch = Callable[[str, int], list[NoteHit]]


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


@dataclass
class EncounterExtraction:
    """What the model understood from an encounter request (M5)."""

    kind: Literal["build", "revise", "other"]
    request: EncounterRequest | None = None  # build: None if load-bearing info is missing
    direction: Literal["easier", "harder"] | None = None  # revise only
    missing: list[str] = field(default_factory=list)  # e.g. ["party_levels"]


class ConversationStore(Protocol):
    """Remembers the last encounter request per conversation, for "make it harder"."""

    def get(self, conversation_id: str) -> EncounterRequest | None: ...

    def put(self, conversation_id: str, request: EncounterRequest) -> None: ...


class LLM(Protocol):
    """Everything the graph needs from a model. ClaudeLLM implements it; tests fake it."""

    # `timeout` is the wall-clock budget in seconds for this one call (None = adapter default).

    def route(
        self, question: str, *, timeout: float | None = None, campaign: bool = False
    ) -> RouteDecision:
        """`campaign` is True inside the campaign hub (M8 finding H1)."""
        ...

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

    def extract_encounter(
        self,
        question: str,
        previous: EncounterRequest | None,
        *,
        timeout: float | None = None,
    ) -> EncounterExtraction:
        """Turn an encounter request (or a revision of `previous`) into structured form."""
        ...

    def present_encounter(
        self,
        question: str,
        request: EncounterRequest,
        record: ToolRecord,
        *,
        revision: Literal["easier", "harder"] | None = None,
        timeout: float | None = None,
    ) -> FinalAnswer:
        """Write up the builder's options (tool record T1) with citations.

        `revision` is set when the request is the previous one made easier or harder.
        """
        ...

    def english_query(self, question: str, *, timeout: float | None = None) -> str:
        """An English SRD search query for a non-English question (M8d)."""
        ...

    def standalone_question(
        self, question: str, history: list[dict[str, str]], *, timeout: float | None = None
    ) -> str:
        """Rewrite a follow-up as a question that stands on its own, given earlier turns (M7)."""
        ...

    def answer_campaign(
        self,
        question: str,
        notes: list[NoteHit],
        evidence: list[Evidence],
        *,
        mode: Literal["memory", "prep"],
        timeout: float | None = None,
    ) -> FinalAnswer:
        """Answer from campaign entries (N1…, in list order) and SRD evidence (E1…)."""
        ...


class ToolRecord(TypedDict):
    id: str  # T1, T2, …
    name: str
    arguments: dict[str, Any]
    output: dict[str, Any]  # {"result": …} or {"error": …} from ToolRegistry.call
    is_error: bool


class CitationRef(TypedDict):
    id: str  # E#, T# or N#
    kind: Literal["srd", "tool", "note"]
    label: str  # human-readable source, e.g. "SRD 5.2.1, … p. 182" or "probability tool"
    entry_id: NotRequired[int]  # notes only: the campaign entry, so a UI can link to it


class TraceEvent(TypedDict):
    node: str
    detail: str
    ms: float


class ChatState(TypedDict, total=False):
    # Inputs
    question: str  # after `contextualize`: the standalone question every later node uses
    original_question: str  # what the user typed, when a follow-up was rewritten (M7)
    history: list[dict[str, str]]  # earlier exchanges of this thread: {"question", "answer"}
    conversation_id: str
    campaign_id: str | None
    # Routing
    intent: Intent
    route_reason: str
    other_edition: bool
    # Gathered material
    evidence: list[Evidence]  # index i ↔ id f"E{i + 1}"
    notes: list[NoteHit]  # index i ↔ id f"N{i + 1}" (M6)
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
