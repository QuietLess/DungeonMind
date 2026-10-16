"""LangGraph orchestrator. Behaviour contract: docs/ARCHITECTURE.md › M4 contract.

The graph is a fixed DAG (route → one handler → verify; the encounter node may hand a
question on to the agent). The only loop is the agent's tool loop, which runs as a bounded
Python loop inside the `agent` node (max_steps turns, timeout_s wall clock), so no model
output can extend it. M5 contract (encounter node): docs/ARCHITECTURE.md › M5 contract.
M6 contract (campaign node): docs/ARCHITECTURE.md › M6 contract.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph

from dungeonmind.graph.state import (
    CAMPAIGN_INTENTS,
    LLM,
    TOOL_INTENTS,
    ChatState,
    CitationRef,
    ConversationStore,
    FinalAnswer,
    Intent,
    NoteSearch,
    StopReason,
    ToolCall,
    ToolRecord,
    TraceEvent,
)
from dungeonmind.graph.store import InMemoryStore
from dungeonmind.rag.answer import check_citations
from dungeonmind.rag.hybrid import Evidence
from dungeonmind.tools.encounter_builder import build_options, revise
from dungeonmind.tools.registry import ToolRegistry

# The graph has at most five node steps; this only guards against a wiring mistake.
RECURSION_LIMIT = 12

MAX_STEPS_LIMIT = 10  # upper bound for Deps.max_steps
MAX_TOOL_CALLS_PER_TURN = 8
MAX_QUERY_CHARS = 200  # search_rules query
MAX_TOOL_OUTPUT_CHARS = 20_000  # JSON of one tool output
MIN_CALL_TIMEOUT_S = 1.0  # floor for an agent turn's share of the remaining budget

SEARCH_RULES_TOOL: dict[str, Any] = {
    "name": "search_rules",
    "description": "Search the SRD 5.2.1 (2024) rules text. Returns the best-matching passages, "
    "each with an E# id you can cite. Use it for how a rule works; use the structured tools for "
    "spell/monster facts, odds, rolls and encounter budgets.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": f"What to look up (at most {MAX_QUERY_CHARS} characters)",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

NEEDS_HUB_ANSWER = (
    "Questions about your campaign need the campaign hub (`dungeonmind serve`), where your "
    "boards and journal live. Here DungeonMind answers rules, spell and monster lookups, odds "
    "and encounter questions from the 2024 SRD 5.2.1."
)
NO_NOTES_ANSWER = (
    "None of the campaign notes you can see cover that. Add it to your journal or character "
    "board, or ask your DM."
)
CAMPAIGN_NOTES_K = {"memory": 8, "prep": 12}
CAMPAIGN_SRD_K = 4
DECLINE_ANSWER = (
    "DungeonMind covers running D&D with the 2024 rules (SRD 5.2.1): rules questions, "
    "spell and monster lookups, odds and encounter budgets. That question is outside "
    "what it can help with."
)
ERROR_ANSWER = "Sorry, something went wrong while answering this question. Please try again."
OTHER_EDITION_NOTE = (
    "Note: DungeonMind covers the 2024 rules (SRD 5.2.1) only. Older editions differ; "
    "this answer describes the 2024 rules.\n\n"
)


@dataclass
class Deps:
    llm: LLM
    retrieve: Callable[[str], list[Evidence]]
    registry: ToolRegistry
    max_steps: int = 6
    timeout_s: float = 60.0
    clock: Callable[[], float] = field(default=time.monotonic)
    store: ConversationStore = field(default_factory=InMemoryStore)
    # M6: entries the asking user may read, already bound to (campaign, viewer) by the hub.
    search_notes: NoteSearch | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.max_steps <= MAX_STEPS_LIMIT:
            raise ValueError(f"max_steps must be 1–{MAX_STEPS_LIMIT}, got {self.max_steps}")


# --- helpers ----------------------------------------------------------------------------------


def _event(state: ChatState, node: str, detail: str, started: float) -> list[TraceEvent]:
    """The state's trace plus one event for `node`, timed from `started` (perf_counter)."""
    ms = (time.perf_counter() - started) * 1000
    return [*state.get("trace", []), TraceEvent(node=node, detail=detail, ms=ms)]


def _describe(error: Exception) -> str:
    return f"error: {type(error).__name__}: {error}"


_KIND_BY_PREFIX = {"T": "tool", "N": "note"}


def _final_fields(final: FinalAnswer) -> ChatState:
    # Citations are carried unchecked and unlabelled until verify resolves them against the state.
    return {
        "answer": final.answer,
        "citations": [
            CitationRef(id=cid, kind=_KIND_BY_PREFIX.get(cid[:1], "srd"), label="")  # type: ignore
            for cid in final.citations
        ],
        "confidence": final.confidence,
        "insufficient_evidence": final.insufficient_evidence,
    }


def _fallback(answer: str, stop_reason: StopReason) -> ChatState:
    """A non-answer (decline, unsupported, error, out of budget): never presented as grounded."""
    return {
        "answer": answer,
        "citations": [],
        "confidence": "low",
        "insufficient_evidence": True,
        "stop_reason": stop_reason,
    }


def _error(error: Exception) -> ChatState:
    return {**_fallback(ERROR_ANSWER, "error"), "error_type": type(error).__name__}


def _node_error(state: ChatState, node: str, error: Exception, started: float) -> ChatState:
    return {**_error(error), "trace": _event(state, node, _describe(error), started)}


def _hits(evidence: list[Evidence], first_index: int) -> list[dict[str, Any]]:
    return [
        {"id": f"E{first_index + i}", "citation": ev.chunk.citation, "text": ev.chunk.text}
        for i, ev in enumerate(evidence, start=1)
    ]


# --- nodes ------------------------------------------------------------------------------------


def _route_node(state: ChatState, deps: Deps) -> ChatState:
    started = time.perf_counter()
    try:
        decision = deps.llm.route(state["question"], timeout=deps.timeout_s)
    except Exception as e:  # errors are data (contract behaviour 6)
        return _node_error(state, "route", e, started)
    return {
        "intent": decision.intent,
        "route_reason": decision.reason,
        "other_edition": decision.other_edition,
        "trace": _event(state, "route", f"{decision.intent.value}: {decision.reason}", started),
    }


def _after_route(state: ChatState) -> str:
    if state.get("stop_reason") == "error":
        return "verify"
    intent = state["intent"]
    if intent is Intent.RULES_QA:
        return "retrieve"
    if intent is Intent.ENCOUNTER:
        return "encounter"
    if intent in TOOL_INTENTS:
        return "agent"
    if intent in CAMPAIGN_INTENTS:
        return "campaign"
    return "decline"


def _retrieve_node(state: ChatState, deps: Deps) -> ChatState:
    started = time.perf_counter()
    try:
        evidence = deps.retrieve(state["question"])
    except Exception as e:
        return _node_error(state, "retrieve", e, started)
    return {
        "evidence": evidence,
        "trace": _event(state, "retrieve", f"{len(evidence)} chunks", started),
    }


def _after_retrieve(state: ChatState) -> str:
    return "verify" if state.get("stop_reason") == "error" else "answer"


def _answer_node(state: ChatState, deps: Deps) -> ChatState:
    started = time.perf_counter()
    evidence = state.get("evidence", [])
    try:
        final = deps.llm.answer(state["question"], evidence, timeout=deps.timeout_s)
    except Exception as e:
        return _node_error(state, "answer", e, started)
    return {
        **_final_fields(final),
        "stop_reason": "completed",
        "trace": _event(state, "answer", f"{len(final.citations)} citations", started),
    }


class _AgentRun:
    """Mutable bookkeeping for one agent loop; turned back into a state update at the end."""

    def __init__(self, state: ChatState, deps: Deps) -> None:
        self.deps = deps
        self.evidence: list[Evidence] = list(state.get("evidence", []))
        self.records: list[ToolRecord] = list(state.get("tool_calls", []))
        self.messages: list[dict[str, Any]] = [{"role": "user", "content": state["question"]}]
        self.trace: list[TraceEvent] = list(state.get("trace", []))
        self.steps = 0

    def log(self, detail: str, started: float) -> None:
        ms = (time.perf_counter() - started) * 1000
        self.trace.append(TraceEvent(node="agent", detail=detail, ms=ms))

    def run_turn_tools(self, calls: list[ToolCall]) -> list[dict[str, Any]]:
        """Run a turn's tool calls (at most MAX_TOOL_CALLS_PER_TURN) into tool_result blocks."""
        results = [self.run_tool(call) for call in calls[:MAX_TOOL_CALLS_PER_TURN]]
        refused = {"error": f"too many tool calls in one turn (max {MAX_TOOL_CALLS_PER_TURN})"}
        results += [self.record(call, refused) for call in calls[MAX_TOOL_CALLS_PER_TURN:]]
        return results

    def run_tool(self, call: ToolCall) -> dict[str, Any]:
        """Run one tool call, record it, and return its tool_result block."""
        found: list[Evidence] = []
        if call.name == SEARCH_RULES_TOOL["name"]:
            output, found = self._search_rules(call.arguments)
        else:
            try:
                output = self.deps.registry.call(call.name, call.arguments)
            except Exception as e:  # an unexpected tool crash is still data
                output = {"error": f"{call.name} failed: {type(e).__name__}"}
        size = len(json.dumps(output, ensure_ascii=False, default=str))
        if size > MAX_TOOL_OUTPUT_CHARS:
            output = {"error": "tool result too large"}
        elif found:
            # Only evidence the model actually received gets E-ids in the state.
            self.evidence.extend(found)
        return self.record(call, output)

    def record(self, call: ToolCall, output: dict[str, Any]) -> dict[str, Any]:
        tid = f"T{len(self.records) + 1}"
        is_error = "error" in output
        self.records.append(
            ToolRecord(
                id=tid, name=call.name, arguments=call.arguments, output=output, is_error=is_error
            )
        )
        return {
            "type": "tool_result",
            "tool_use_id": call.call_id,
            "content": json.dumps({"id": tid, **output}, ensure_ascii=False, default=str),
            "is_error": is_error,
        }

    def _search_rules(self, arguments: dict[str, Any]) -> tuple[dict[str, Any], list[Evidence]]:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"error": "search_rules needs a non-empty string 'query'."}, []
        if len(query) > MAX_QUERY_CHARS:
            return {
                "error": f"search_rules query must be at most {MAX_QUERY_CHARS} characters."
            }, []
        try:
            found = self.deps.retrieve(query)
        except Exception as e:
            return {"error": f"search_rules failed: {type(e).__name__}"}, []
        return {"result": _hits(found, len(self.evidence))}, found

    def update(self, outcome: ChatState) -> ChatState:
        return {
            **outcome,
            "evidence": self.evidence,
            "tool_calls": self.records,
            "messages": self.messages,
            "steps": self.steps,
            "trace": self.trace,
        }


def _agent_node(state: ChatState, deps: Deps) -> ChatState:
    run = _AgentRun(state, deps)
    started = time.perf_counter()
    try:
        tools = [*deps.registry.definitions(), SEARCH_RULES_TOOL]
    except Exception as e:
        run.log(f"tool list: {_describe(e)}", started)
        return run.update(_error(e))
    deadline = deps.clock() + deps.timeout_s

    while run.steps < deps.max_steps:
        started = time.perf_counter()
        remaining = deadline - deps.clock()
        if remaining < 0:
            run.log(f"timeout after {run.steps} turns", started)
            return run.update(_fallback(_timeout_answer(deps), "timeout"))
        run.steps += 1
        try:
            turn = deps.llm.agent_step(
                run.messages, tools, timeout=max(remaining, MIN_CALL_TIMEOUT_S)
            )
            if turn.final is None and not turn.tool_calls:
                raise ValueError("model turn had neither tool calls nor a final answer")
        except Exception as e:
            run.log(f"turn {run.steps}: {_describe(e)}", started)
            return run.update(_error(e))
        usage = f" ({_usage(turn.usage)})" if turn.usage else ""

        if turn.final is not None:
            run.log(f"turn {run.steps}: final answer{usage}", started)
            return run.update({**_final_fields(turn.final), "stop_reason": "completed"})

        results = run.run_turn_tools(turn.tool_calls)
        run.messages.append({"role": "assistant", "content": turn.raw_content})
        run.messages.append({"role": "user", "content": results})
        names = ", ".join(call.name for call in turn.tool_calls)
        run.log(f"turn {run.steps}: {names}{usage}", started)

    run.log(f"stopped at max_steps={deps.max_steps}", time.perf_counter())
    return run.update(_fallback(_max_steps_answer(deps), "max_steps"))


def _usage(usage: dict[str, int]) -> str:
    return f"{usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out tokens"


def _timeout_answer(deps: Deps) -> str:
    return (
        f"I couldn't finish working this out within the time limit ({deps.timeout_s:g} s), "
        "so I don't have a reliable answer. Try again or ask a narrower question."
    )


def _max_steps_answer(deps: Deps) -> str:
    return (
        f"I couldn't finish working this out within {deps.max_steps} tool steps, "
        "so I don't have a reliable answer. Try asking a narrower question."
    )


NO_PREVIOUS_ANSWER = (
    "There's no earlier encounter in this conversation to change. Tell me about the party "
    "(how many characters and their levels) and the kind of encounter you want, and I'll "
    "build one."
)
NO_DIRECTION_ANSWER = "Should the encounter be easier or harder?"
_MISSING_LABELS = {
    "party_levels": "the party: how many characters and their levels "
    "(for example, four level-5 characters)",
    "difficulty": "the difficulty: low, moderate or high",
    "creature_types": "which kinds of creatures to use",
}


def _needs_input_answer(missing: list[str]) -> str:
    # `missing` comes from the model: map known fields, keep the rest short.
    items = [_MISSING_LABELS.get(m, m.replace("_", " ")[:80]) for m in missing[:5]]
    items = items or [_MISSING_LABELS["party_levels"]]
    return "To build this encounter I need " + "; ".join(items) + "."


def _needs_input(state: ChatState, answer: str, detail: str, started: float) -> ChatState:
    """A question back to the user: not an answer, so it carries no citations."""
    return {
        "answer": answer,
        "citations": [],
        "confidence": "low",
        "insufficient_evidence": False,
        "stop_reason": "needs_input",
        "trace": _event(state, "encounter", detail, started),
    }


def _encounter_node(state: ChatState, deps: Deps) -> ChatState:
    started = time.perf_counter()
    conversation_id = state.get("conversation_id")
    try:
        previous = deps.store.get(conversation_id) if conversation_id else None
        problem = getattr(deps.store, "last_problem", None) if conversation_id else None
        if problem:  # S2: a damaged store counts as no earlier encounter, with a warning
            state = {**state, "trace": _event(state, "encounter", f"warning: {problem}", started)}
        extraction = deps.llm.extract_encounter(state["question"], previous, timeout=deps.timeout_s)
    except Exception as e:
        return _node_error(state, "encounter", e, started)

    if extraction.kind == "other":
        # Not a build or revise request (e.g. "what's the XP budget for…?"): the agent's tools.
        return {"trace": _event(state, "encounter", "other: handed to the agent", started)}
    if extraction.kind == "revise":
        if previous is None:
            return _needs_input(state, NO_PREVIOUS_ANSWER, "revise: no earlier request", started)
        if extraction.direction is None:
            return _needs_input(state, NO_DIRECTION_ANSWER, "revise: no direction", started)
        try:
            request = revise(previous, extraction.direction)
        except ValueError as e:  # past Low or High: the SRD defines nothing further
            return {
                **_fallback(str(e), "completed"),
                "trace": _event(state, "encounter", f"revise: {e}", started),
            }
    elif extraction.request is None or extraction.missing:
        answer = _needs_input_answer(extraction.missing)
        return _needs_input(state, answer, f"build: missing {extraction.missing}", started)
    else:
        request = extraction.request

    record: ToolRecord | None = None
    try:
        result = build_options(request, deps.registry.catalog)
        record = ToolRecord(
            id="T1",
            name="encounter_builder",
            arguments=request.model_dump(mode="json"),
            output={"result": asdict(result)},
            is_error=False,
        )
        revision = extraction.direction if extraction.kind == "revise" else None
        final = deps.llm.present_encounter(
            state["question"], request, record, revision=revision, timeout=deps.timeout_s
        )
    except Exception as e:
        return {
            **_node_error(state, "encounter", e, started),
            "tool_calls": [record] if record else [],
        }
    detail = f"{extraction.kind}: {len(result.options)} options"
    if conversation_id:
        # R3: only a request the user saw becomes the one "make it harder" revises. The answer
        # is already written, so a failed save is a warning, not an error.
        try:
            deps.store.put(conversation_id, request)
        except Exception as e:
            detail += f"; warning: not saved ({_describe(e)})"
    return {
        **_final_fields(final),
        "stop_reason": "completed",
        "tool_calls": [record],
        "trace": _event(state, "encounter", detail, started),
    }


def _after_encounter(state: ChatState) -> str:
    # The node sets stop_reason whenever it finished the turn itself.
    return "verify" if "stop_reason" in state else "agent"


def _campaign_node(state: ChatState, deps: Deps) -> ChatState:
    """Answer from the campaign entries the asking user may read (M6 behaviour 14).

    `deps.search_notes` is already bound to one campaign and viewer, so nothing here can widen
    what is visible; with no visible entries the model isn't called at all.
    """
    started = time.perf_counter()
    mode = "prep" if state["intent"] is Intent.SESSION_PREP else "memory"
    if deps.search_notes is None:
        return {
            **_fallback(NEEDS_HUB_ANSWER, "completed"),
            "trace": _event(state, "campaign", "no campaign hub", started),
        }
    question = state["question"]
    try:
        notes = deps.search_notes(question, CAMPAIGN_NOTES_K[mode])
    except Exception as e:
        return _node_error(state, "campaign", e, started)
    if not notes:
        return {
            **_fallback(NO_NOTES_ANSWER, "completed"),
            "notes": [],
            "trace": _event(state, "campaign", f"{mode}: 0 visible notes", started),
        }
    detail = f"{mode}: {len(notes)} visible notes"
    try:
        evidence = deps.retrieve(question)[:CAMPAIGN_SRD_K]
    except Exception as e:  # the notes can still answer; rules claims just can't be cited
        evidence = []
        detail += f"; warning: SRD search failed ({_describe(e)})"
    try:
        final = deps.llm.answer_campaign(
            question, notes, evidence, mode=mode, timeout=deps.timeout_s
        )
    except Exception as e:
        return {
            **_node_error(state, "campaign", e, started),
            "notes": notes,
            "evidence": evidence,
        }
    return {
        **_final_fields(final),
        "notes": notes,
        "evidence": evidence,
        "stop_reason": "completed",
        "trace": _event(state, "campaign", f"{detail}, {len(evidence)} SRD chunks", started),
    }


def _decline_node(state: ChatState) -> ChatState:
    started = time.perf_counter()
    return {
        **_fallback(DECLINE_ANSWER, "completed"),
        "trace": _event(state, "decline", "out of scope", started),
    }


def _is_answer(state: ChatState) -> bool:
    """A completed answer to a question, subject to the no-citations check and the note."""
    if state.get("stop_reason") != "completed":
        return False
    intent = state.get("intent")
    if intent is Intent.RULES_QA or intent in TOOL_INTENTS:
        return True
    if intent in CAMPAIGN_INTENTS:  # a model answer from visible notes, not a fallback
        return bool(state.get("notes"))
    # Encounter: the builder's write-up or the agent's answer, not a revise-limit message.
    return intent is Intent.ENCOUNTER and ("steps" in state or bool(state.get("tool_calls")))


def _verify_node(state: ChatState) -> ChatState:
    """Enforce the citation contract (behaviour 9, C1, C2) and add the other-edition note (10)."""
    started = time.perf_counter()
    evidence = state.get("evidence", [])
    records = {r["id"]: r for r in state.get("tool_calls", [])}
    refs: dict[str, CitationRef] = {
        f"E{i}": CitationRef(id=f"E{i}", kind="srd", label=ev.chunk.citation)
        for i, ev in enumerate(evidence, start=1)
    }
    refs |= {
        tid: CitationRef(id=tid, kind="tool", label=f"{r['name']} tool")
        for tid, r in records.items()
    }
    refs |= {
        f"N{i}": CitationRef(id=f"N{i}", kind="note", label=n.source, entry_id=n.entry_id)
        for i, n in enumerate(state.get("notes", []), start=1)
    }

    def problem(cid: str) -> str | None:
        if cid not in refs:
            return cid
        if cid in records and records[cid]["is_error"]:
            return f"cited-error-{cid}"  # a failed tool call supports nothing
        return None

    listed = [c["id"] for c in state.get("citations", [])]
    answer, cited, violations = check_citations(state.get("answer", ""), listed, problem)
    citations = [refs[cid] for cid in cited]

    confidence = state.get("confidence", "low")
    answered = _is_answer(state)
    if answered and not citations and not state.get("insufficient_evidence", True):
        # An uncited answer breaks the contract: downgrade rather than present it as grounded.
        violations.append("no-citations")
        confidence = "low"
    if answered and state.get("other_edition") and state.get("intent") not in CAMPAIGN_INTENTS:
        answer = OTHER_EDITION_NOTE + answer

    detail = f"{len(citations)} citations, {len(violations)} violations"
    return {
        "answer": answer,
        "citations": citations,
        "confidence": confidence,
        "violations": violations,
        "trace": _event(state, "verify", detail, started),
    }


# --- public API -------------------------------------------------------------------------------


def build_graph(deps: Deps) -> Any:
    graph = StateGraph(ChatState)
    graph.add_node("route", lambda s: _route_node(s, deps))
    graph.add_node("retrieve", lambda s: _retrieve_node(s, deps))
    graph.add_node("answer", lambda s: _answer_node(s, deps))
    graph.add_node("agent", lambda s: _agent_node(s, deps))
    graph.add_node("encounter", lambda s: _encounter_node(s, deps))
    graph.add_node("campaign", lambda s: _campaign_node(s, deps))
    graph.add_node("decline", _decline_node)
    graph.add_node("verify", _verify_node)

    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        _after_route,
        ["retrieve", "agent", "encounter", "campaign", "decline", "verify"],
    )
    graph.add_conditional_edges("encounter", _after_encounter, ["agent", "verify"])
    graph.add_conditional_edges("retrieve", _after_retrieve, ["answer", "verify"])
    for node in ("answer", "agent", "campaign", "decline"):
        graph.add_edge(node, "verify")
    graph.add_edge("verify", END)
    return graph.compile()


def run_chat(
    question: str,
    deps: Deps,
    conversation_id: str | None = None,
    campaign_id: str | None = None,
) -> ChatState:
    initial: ChatState = {
        "question": question,
        "campaign_id": campaign_id,
        "error_type": None,
        "trace": [],
    }
    if conversation_id:  # only a given id reaches the conversation store
        initial["conversation_id"] = conversation_id
    return build_graph(deps).invoke(initial, config={"recursion_limit": RECURSION_LIMIT})
