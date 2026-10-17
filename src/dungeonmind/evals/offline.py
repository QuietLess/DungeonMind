"""Offline evaluation: retrieval, leak check and citation contract (M8 contract O1–O4).

Nothing here calls a model. O4 runs the real graph with a scripted LLM that cites everything
it was given, so the verify node and the hub's visibility binding are exercised end to end.
"""

from __future__ import annotations

from typing import Any

from dungeonmind.evals.suite import Case
from dungeonmind.graph.state import (
    AgentTurn,
    FinalAnswer,
    Intent,
    RouteDecision,
)

O4_CATEGORIES = frozenset(
    {"rules", "other_edition", "unanswerable", "campaign", "prep", "injection", "followup"}
)
LEAK_K = 50


def _ratio(found: int, total: int) -> float:
    return found / total if total else 0.0


def _ranked_metrics(rankings: list[tuple[list[str], set[str]]], k: int) -> dict[str, Any]:
    recalls, rrs = [], []
    for ranked, relevant in rankings:
        top = ranked[:k]
        recalls.append(_ratio(len(relevant & set(top)), len(relevant)))
        rrs.append(next((1 / i for i, t in enumerate(ranked, 1) if t in relevant), 0.0))
    n = len(rankings)
    return {
        f"recall@{k}": round(sum(recalls) / n, 4) if n else 0.0,
        "mrr": round(sum(rrs) / n, 4) if n else 0.0,
        "n": n,
    }


def srd_retrieval(cases: list[Case], retriever, modes, k: int) -> dict[str, Any]:
    """O1: R@k and MRR over cases with expected SRD sections, per retrieval mode."""
    with_srd = [c for c in cases if c.expect.get("srd")]
    report = {}
    for mode in modes:
        rankings = []
        for c in with_srd:
            hits = retriever.retrieve(c.question, k=k, mode=mode)
            rankings.append(([h.chunk.title for h in hits], set(c.expect["srd"])))
        report[str(getattr(mode, "value", mode))] = _ranked_metrics(rankings, k)
    return report


def _viewer(fixture, name: str):
    hub = fixture.hub
    user = fixture.users[name]
    return hub._viewer(user, hub._member(user, fixture.cid))


def _titles(fixture) -> dict[int, str]:
    with fixture.hub.storage.campaign(fixture.cid) as db:
        return {r["id"]: r["title"] for r in db.execute("SELECT id, title FROM entries")}


def notes_retrieval(cases: list[Case], fixture, k: int) -> dict[str, Any]:
    """O2: R@k and MRR over cases with expected fixture entries, searched as the case's viewer."""
    titles = _titles(fixture)
    rankings = []
    for c in cases:
        if c.expect.get("notes") and c.viewer:
            hits = fixture.hub._search(fixture.cid, _viewer(fixture, c.viewer), c.question, k)
            rankings.append(([titles[h.entry_id] for h in hits], set(c.expect["notes"])))
    return _ranked_metrics(rankings, k)


def leak_check(cases: list[Case], fixture) -> tuple[float, list[str]]:
    """O3: no hidden string reaches any search hit the case's viewer gets."""
    probes = [c for c in cases if c.expect.get("no_leak") and c.viewer]
    leaks = []
    for c in probes:
        hits = fixture.hub._search(fixture.cid, _viewer(fixture, c.viewer), c.question, LEAK_K)
        text = " ".join(f"{h.source} {h.text}" for h in hits).lower()
        if any(s.lower() in text for s in c.expect["no_leak"]):
            leaks.append(c.id)
    return _ratio(len(leaks), len(probes)), leaks


class CiteEverythingLLM:
    """Scripted LLM for O4: routes as the case expects and cites every id it was given."""

    def __init__(self, case: Case) -> None:
        self.case = case
        self.inputs: list[str] = []

    def _intent(self) -> Intent:
        expected = self.case.expect.get("intent")
        if expected in ("rules_qa", "campaign_memory", "session_prep"):
            return Intent(expected)
        # Tool intents aren't scripted here; contract-check them as the question they ask.
        return Intent.CAMPAIGN_MEMORY if self.case.viewer else Intent.RULES_QA

    def route(self, question, *, timeout=None, campaign=False):
        return RouteDecision(
            intent=self._intent(),
            reason="scripted",
            other_edition=bool(self.case.expect.get("other_edition")),
        )

    def standalone_question(self, question, history, *, timeout=None):
        return question

    def answer(self, question, evidence, *, timeout=None):
        self.inputs.append(repr(evidence))
        ids = [f"E{i}" for i in range(1, len(evidence) + 1)]
        return FinalAnswer(
            " ".join(f"Fact [{i}]." for i in ids) or "Not covered.",
            ids,
            "high" if ids else "low",
            not ids,
        )

    def answer_campaign(self, question, notes, evidence, *, mode, timeout=None):
        self.inputs.append(repr(notes) + repr(evidence))
        ids = [f"N{i}" for i in range(1, len(notes) + 1)]
        ids += [f"E{i}" for i in range(1, len(evidence) + 1)]
        return FinalAnswer(" ".join(f"Fact [{i}]." for i in ids), ids, "high", False)

    def agent_step(self, messages, tools, *, timeout=None):  # not used by O4 categories
        return AgentTurn(final=FinalAnswer("n/a", [], "low", True))

    def extract_encounter(self, *a, **k):  # pragma: no cover - not used by O4 categories
        raise AssertionError("encounter cases aren't part of O4")

    def present_encounter(self, *a, **k):  # pragma: no cover
        raise AssertionError("encounter cases aren't part of O4")


def contract_check(cases: list[Case], fixture, retrieve, registry) -> dict[str, Any]:
    """O4: verify leaves only valid citations; campaign inputs hold only the viewer's entries."""
    from dungeonmind.graph.build import Deps, run_chat
    from dungeonmind.hub.chats import make_engine

    violations: list[str] = []
    selected = [c for c in cases if c.category in O4_CATEGORIES]
    hub = fixture.hub
    saved_engine = hub.engine
    try:
        for c in selected:
            llm = CiteEverythingLLM(c)
            if c.viewer:
                states: list[dict] = []
                inner = make_engine(llm, retrieve, registry)

                def engine(*args, _inner=inner, _states=states):
                    state = _inner(*args)
                    _states.append(state)
                    return state

                hub.engine = engine
                user = fixture.users[c.viewer]
                thread = hub.create_thread(user, fixture.cid, c.id)
                for turn in c.history:  # follow-ups: earlier exchanges go through the hub
                    hub._save_message(fixture.cid, thread.id, "user", turn["question"], {})
                    hub._save_message(
                        fixture.cid,
                        thread.id,
                        "assistant",
                        turn["answer"],
                        {"stop_reason": "completed"},
                    )
                hub.ask(user, fixture.cid, thread.id, c.question)
                state = states[-1] if states else {"violations": ["engine not called"]}
            else:
                deps = Deps(llm=llm, retrieve=retrieve, registry=registry)
                state = run_chat(c.question, deps, history=c.history or None)
            if state.get("violations"):
                violations.append(f"{c.id}: {state['violations']}")
            seen = " ".join(llm.inputs).lower()
            for hidden in c.expect.get("no_leak", []) if c.viewer else []:
                if hidden.lower() in seen:
                    violations.append(f"{c.id}: model input contained hidden text {hidden!r}")
    finally:
        hub.engine = saved_engine
    return {"n": len(selected), "violations": violations}


def run_offline(
    cases: list[Case], fixture, retriever=None, modes=(), registry=None, k: int = 5
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    if retriever is not None and modes:
        report["srd"] = srd_retrieval(cases, retriever, modes, k)
    report["notes"] = notes_retrieval(cases, fixture, k)
    report["leak_rate"], report["leaks"] = leak_check(cases, fixture)
    if registry is not None:

        def retrieve(query: str):
            if retriever is None:
                return []
            mode = modes[0] if modes else "dense"
            return retriever.retrieve(query, k=6, mode=mode)[:6]

        report["contract"] = contract_check(cases, fixture, retrieve, registry)
    else:
        report["contract"] = {"n": 0, "violations": []}
    return report
