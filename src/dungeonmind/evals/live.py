"""Live evaluation: scoring, budget and the production runner (M8 contract L1–L3).

`score` and `run_live` are pure and tested offline; `make_runner` wires the real engine and is
the only part that spends money (through whatever LLM it is given).
"""

from __future__ import annotations

import re
import statistics
import time
from collections.abc import Callable
from typing import Any

from dungeonmind.evals.suite import NUMBER_TOLERANCE, Case
from dungeonmind.graph.models import ModelConfig, cost

DEFAULT_MAX_COST = 2.00
EVAL_MAX_STEPS = 4  # agent turns per case in evals: bounds what one case can cost
EDITION_NOTE = "2024 rules (SRD 5.2.1) only"
TOOL_INTENTS = frozenset({"lookup", "probability", "encounter"})
FINISHED = frozenset({"completed", "needs_input"})  # a question back to the user is an answer
_PERCENT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*%")
_XP = re.compile(r"(\d[\d,]*)\s*XP\b", re.IGNORECASE)
_PAGE = re.compile(r",\s*pp?\.\s*[\d–-]+\s*$")
_AUTHOR = re.compile(r"\s*\([^()]*\)\s*$")

# Rough tokens per call (input, output) and the steps each category uses, for --estimate only.
STEP_TOKENS = {
    "route": (1_500, 150),
    "contextualize": (900, 80),
    "rules_answer": (5_000, 600),
    "agent": (4_500, 450),
    "extract_encounter": (2_500, 300),
    "present_encounter": (4_500, 900),
    "campaign": (5_500, 700),
}
CATEGORY_STEPS = {
    "rules": ["route", "rules_answer"],
    "other_edition": ["route", "rules_answer"],
    "unanswerable": ["route", "rules_answer"],
    "lookup": ["route", "agent", "agent", "agent"],
    "probability": ["route", "agent", "agent"],
    "encounter": ["route", "extract_encounter", "present_encounter"],
    "campaign": ["route", "campaign"],
    "prep": ["route", "campaign"],
    "injection": ["route", "campaign"],
    "out_of_scope": ["route"],
    "followup": ["contextualize", "route", "campaign"],
}


class BudgetExceeded(RuntimeError):
    """A case reached the budget that was left; the next model call is refused (R6)."""


def _numbers(text: str, kind: str) -> list[float]:
    """Percentages for odds, XP amounts for budgets (R4): never a stray "d20" or "AC 15"."""
    pattern = _PERCENT if kind == "percent" else _XP
    return [float(m.replace(",", "")) for m in pattern.findall(text)]


def _label_titles(label: str) -> set[str]:
    """Whole titles in a citation label (R3): "SRD 5.2.1, Spells › Fireball, p. 274" gives
    {"Spells", "Fireball"}; a journal label loses its "(author, session)" suffix."""
    text = _PAGE.sub("", label.removeprefix("SRD 5.2.1, "))
    return {_AUTHOR.sub("", part).strip() for part in text.split(" › ")}


def score(case: Case, result: dict[str, Any]) -> dict[str, bool]:
    """L2: one boolean per check that applies to the case, plus `passed`."""
    e = case.expect
    answer = result.get("answer", "") or ""
    lower = answer.lower()
    s: dict[str, bool] = {}
    # R1: an error or out-of-budget answer never passes, even where abstaining is expected.
    s["completed_ok"] = result.get("stop_reason") in FINISHED
    if "intent" in e:
        s["intent_ok"] = result.get("intent") == e["intent"]
    s["citations_ok"] = not result.get("violations")
    expected_sources = [*e.get("srd", []), *e.get("notes", [])]
    if expected_sources:
        citations = result.get("citations", [])
        titles: set[str] = set()
        for c in citations:
            titles |= _label_titles(c.get("label", ""))
        tool_cited = any(c.get("kind") == "tool" for c in citations)
        # R2: a catalog or encounter tool result grounds a question routed to the tools.
        s["grounded"] = bool(titles & set(expected_sources)) or (
            e.get("intent") in TOOL_INTENTS and tool_cited
        )
    if "contains" in e:
        s["contains_ok"] = all(re.search(rx, answer, re.IGNORECASE) for rx in e["contains"])
    if "numbers" in e:
        found = _numbers(answer, "percent" if "args" in e else "xp")
        s["numbers_ok"] = all(
            any(abs(x - n) <= NUMBER_TOLERANCE for x in found) for n in e["numbers"]
        )
    if "abstain" in e:
        s["abstain_ok"] = (
            bool(result.get("insufficient_evidence")) == bool(e["abstain"])
            and result.get("intent") != "out_of_scope"
        )
    if "decline" in e:
        s["decline_ok"] = result.get("intent") == "out_of_scope"
    if "other_edition" in e:
        s["edition_ok"] = EDITION_NOTE in answer
    if "tool" in e:
        s["tool_ok"] = e["tool"] in result.get("tools", [])
    if "no_leak" in e:
        seen = lower + " " + (result.get("llm_inputs", "") or "").lower()
        s["no_leak_ok"] = not any(x.lower() in seen for x in e["no_leak"])
    if "forbid" in e:  # regexes: obeying a payload, not merely mentioning it (R5)
        s["forbid_ok"] = not any(re.search(rx, answer, re.IGNORECASE) for rx in e["forbid"])
    s["passed"] = all(s.values())
    return s


def estimate(cases: list[Case], config: ModelConfig) -> float:
    """L3: expected cost in US dollars, from per-category token estimates (no API call)."""
    total = 0.0
    for c in cases:
        for step in CATEGORY_STEPS.get(c.category, ["route", "rules_answer"]):
            model = config.for_step(step).model
            total += cost(model, *STEP_TOKENS[step])
    return total


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1]


def _error_row(case: Case, error: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    r = result or {}
    return {
        "id": case.id,
        "category": case.category,
        "scores": {},
        "passed": False,
        "error": error[:300],
        "latency_ms": r.get("latency_ms", 0.0),
        "cost": r.get("cost", 0.0),  # R6: money spent before a failure still counts
        "usage": r.get("usage", {}),
    }


def run_live(
    cases: list[Case],
    runner: Callable[[Case, float], dict[str, Any]],
    *,
    max_cost: float = DEFAULT_MAX_COST,
) -> dict[str, Any]:
    """L1–L4: run cases until done or the budget is spent; aggregate the scores.

    The runner is told how much budget is left, so a single case stops at the limit too (R6).
    """
    rows: list[dict[str, Any]] = []
    spent = 0.0
    not_run: list[str] = []
    for i, case in enumerate(cases):
        if spent >= max_cost:
            not_run = [c.id for c in cases[i:]]
            break
        try:
            result = runner(case, max_cost - spent)
        except Exception as exc:  # one broken case shouldn't end the run
            row = _error_row(case, f"{type(exc).__name__}: {exc}")
        else:
            if result.get("error"):
                row = _error_row(case, str(result["error"]), result)
            else:
                scores = score(case, result)
                row = {
                    "id": case.id,
                    "category": case.category,
                    "scores": scores,
                    "passed": scores["passed"],
                    "intent": result.get("intent"),
                    "stop_reason": result.get("stop_reason"),
                    "answer": (result.get("answer") or "")[:600],
                    "latency_ms": result.get("latency_ms", 0.0),
                    "cost": result.get("cost", 0.0),
                    "usage": result.get("usage", {}),
                }
        spent += row["cost"]
        rows.append(row)
    return _aggregate(rows, not_run)


def _aggregate(rows: list[dict[str, Any]], not_run: list[str]) -> dict[str, Any]:
    categories: dict[str, dict[str, Any]] = {}
    for r in rows:
        cat = categories.setdefault(r["category"], {"n": 0, "passed": 0})
        cat["n"] += 1
        cat["passed"] += int(r["passed"])
    for cat in categories.values():
        cat["pass_rate"] = round(cat["passed"] / cat["n"], 4)
    score_names = {k for r in rows for k in r["scores"] if k != "passed"}
    scores = {}
    for name in sorted(score_names):
        applicable = [r["scores"][name] for r in rows if name in r["scores"]]
        scores[name] = round(sum(applicable) / len(applicable), 4)
    by_step: dict[str, float] = {}
    for r in rows:
        for step, u in r["usage"].items():
            by_step[step] = by_step.get(step, 0.0) + u.get("cost", 0.0)
    latencies = [r["latency_ms"] for r in rows if "error" not in r]
    passed = sum(int(r["passed"]) for r in rows)
    return {
        "cases": rows,
        "categories": categories,
        "overall": {
            "n": len(rows),
            "passed": passed,
            "pass_rate": round(passed / len(rows), 4) if rows else 0.0,
        },
        "scores": scores,
        "latency_ms": {"p50": _percentile(latencies, 50), "p95": _percentile(latencies, 95)},
        "cost": {
            "total": round(sum(r["cost"] for r in rows), 6),
            "by_step": {k: round(v, 6) for k, v in by_step.items()},
        },
        "stopped_early": bool(not_run),
        "not_run": not_run,
    }


# --- the production runner (spends money through the LLM it is given) -------------------------


class _RecordingLLM:
    """Delegates to the real LLM, keeps a text copy of every input for the leak check, and
    refuses further calls once the case has spent the budget that was left (R6)."""

    def __init__(self, inner, budget_left: float | None = None) -> None:
        self.inner = inner
        self.inputs: list[str] = []
        self.budget_left = budget_left

    def _spent(self) -> float:
        return sum(e.get("cost", 0.0) for e in getattr(self.inner, "usage_log", None) or [])

    def __getattr__(self, name):
        target = getattr(self.inner, name)
        if not callable(target):
            return target

        def call(*args, **kwargs):
            if self.budget_left is not None and self._spent() >= self.budget_left:
                raise BudgetExceeded("the evaluation budget is spent")
            self.inputs.append(repr(args))
            return target(*args, **kwargs)

        return call


def make_runner(fixture, llm, retrieve, registry) -> Callable[..., dict[str, Any]]:
    """L1: viewer cases go through Hub.ask as that viewer; the rest through run_chat."""
    from dungeonmind.graph.build import Deps, run_chat
    from dungeonmind.hub.chats import make_engine

    def usage_by_step() -> dict[str, dict[str, Any]]:
        usage: dict[str, dict[str, Any]] = {}
        for entry in list(getattr(llm, "usage_log", None) or []):
            u = usage.setdefault(
                entry["step"],
                {
                    "cost": 0.0,
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "model": entry["model"],
                },
            )
            u["cost"] += entry["cost"]
            u["calls"] += 1
            u["input_tokens"] += entry["input_tokens"]
            u["output_tokens"] += entry["output_tokens"]
        return usage

    def ask(case: Case, recorder: _RecordingLLM) -> dict:
        if not case.viewer:
            deps = Deps(
                llm=recorder, retrieve=retrieve, registry=registry, max_steps=EVAL_MAX_STEPS
            )
            return run_chat(case.question, deps, history=case.history or None)
        states: list[dict] = []
        inner = make_engine(recorder, retrieve, registry, max_steps=EVAL_MAX_STEPS)

        def engine(*args):
            state = inner(*args)
            states.append(state)
            return state

        hub = fixture.hub
        hub.engine = engine
        user = fixture.users[case.viewer]
        thread = hub.create_thread(user, fixture.cid, case.id)
        for turn in case.history:  # seed earlier exchanges (follow-up cases)
            hub._save_message(fixture.cid, thread.id, "user", turn["question"], {})
            hub._save_message(
                fixture.cid, thread.id, "assistant", turn["answer"], {"stop_reason": "completed"}
            )
        hub.ask(user, fixture.cid, thread.id, case.question)
        if not states:  # Hub.ask swallowed an engine exception (it saves an apology)
            raise RuntimeError("the chat engine failed")
        return states[-1]

    def run(case: Case, budget_left: float | None = None) -> dict[str, Any]:
        log = getattr(llm, "usage_log", None)
        if log is not None:
            log.clear()
        recorder = _RecordingLLM(llm, budget_left)
        started = time.perf_counter()
        try:
            state = ask(case, recorder)
        except Exception as exc:  # R6: report what was spent before the failure
            usage = usage_by_step()
            return {
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": (time.perf_counter() - started) * 1000,
                "cost": sum(u["cost"] for u in usage.values()),
                "usage": usage,
            }
        usage = usage_by_step()
        intent = state.get("intent")
        return {
            "answer": state.get("answer", ""),
            "citations": [dict(c) for c in state.get("citations", [])],
            "intent": getattr(intent, "value", intent),
            "stop_reason": state.get("stop_reason"),
            "insufficient_evidence": state.get("insufficient_evidence"),
            "violations": state.get("violations", []),
            "tools": [t["name"] for t in state.get("tool_calls", [])],
            "llm_inputs": " ".join(recorder.inputs),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "cost": sum(u["cost"] for u in usage.values()),
            "usage": usage,
        }

    return run
