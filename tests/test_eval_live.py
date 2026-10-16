"""M8 live scoring (L2), budget (L3), results (L4), compare (M5) and gates (G1–G2), offline."""

import json

import pytest

from dungeonmind.evals.live import estimate, run_live, score
from dungeonmind.evals.results import compare, gate, save
from dungeonmind.evals.suite import Case
from dungeonmind.graph.models import ModelConfig


def _case(category="rules", viewer=None, **expect) -> Case:
    return Case(id=f"{category}-01", category=category, question="q", expect=expect, viewer=viewer)


def _result(**kw):
    base = {"answer": "", "citations": [], "intent": "rules_qa", "stop_reason": "completed",
            "insufficient_evidence": False, "violations": [], "tools": [], "llm_inputs": ""}
    return {**base, **kw}


# --- L2 scoring -------------------------------------------------------------------------------


def test_l2_intent_contains_and_grounded():
    case = _case(intent="rules_qa", srd=["Grappled [Condition]"], contains=[r"escape|athletics"])
    ok = score(case, _result(
        answer="Use Athletics to escape [E1].",
        citations=[{"id": "E1", "kind": "srd", "label": "SRD 5.2.1, Rules Glossary › Grappled [Condition], p. 183"}],
    ))
    assert ok == {"intent_ok": True, "citations_ok": True, "grounded": True, "contains_ok": True,
                  "passed": True}


def test_l2_wrong_intent_ungrounded_and_missing_text_fail():
    case = _case(intent="rules_qa", srd=["Grappled [Condition]"], contains=[r"escape"])
    ok = score(case, _result(intent="lookup", answer="No idea.", citations=[]))
    assert not ok["intent_ok"] and not ok["grounded"] and not ok["contains_ok"]
    assert ok["passed"] is False


def test_l2_note_grounding_matches_entry_titles_in_labels():
    case = _case(category="campaign", viewer="player", notes=["Elara Voss"])
    ok = score(case, _result(intent="campaign_memory", citations=[
        {"id": "N1", "kind": "note", "label": "Lore › NPC › Elara Voss", "entry_id": 2}]))
    assert ok["grounded"] is True


@pytest.mark.parametrize(
    "answer, ok",
    [("You hit 75% of the time.", True), ("Roughly 74.6 %.", True), ("About 0.75.", False),
     ("You hit 70% of the time.", False), ("The budget is 4,400 XP.", None)],
)
def test_l2_numbers_allow_half_a_point_of_rounding(answer, ok):
    if ok is None:
        assert score(_case(numbers=[4400]), _result(answer=answer))["numbers_ok"] is True
    else:
        assert score(_case(numbers=[75.0]), _result(answer=answer))["numbers_ok"] is ok


def test_l2_abstain_decline_edition_and_tool():
    assert score(_case(abstain=True), _result(insufficient_evidence=True))["abstain_ok"]
    assert not score(_case(abstain=True), _result(insufficient_evidence=False))["abstain_ok"]
    assert score(_case(decline=True), _result(intent="out_of_scope"))["decline_ok"]
    note = "Note: DungeonMind covers the 2024 rules (SRD 5.2.1) only. Older editions differ"
    assert score(_case(other_edition=True), _result(answer=note + "..."))["edition_ok"]
    assert score(_case(tool="probability"), _result(tools=["probability"]))["tool_ok"]
    assert not score(_case(tool="probability"), _result(tools=["dice_roll"]))["tool_ok"]


def test_l2_no_leak_checks_the_answer_and_the_model_inputs():
    case = _case(category="campaign", viewer="player", no_leak=["ancient vampire"])
    assert score(case, _result(answer="Your notes don't say."))["no_leak_ok"]
    assert not score(case, _result(answer="He is an Ancient Vampire."))["no_leak_ok"]
    assert not score(case, _result(llm_inputs="...an ancient vampire who..."))["no_leak_ok"]


def test_l2_violations_fail_citations_ok():
    assert not score(_case(), _result(violations=["N9"]))["citations_ok"]


# --- L3 budget --------------------------------------------------------------------------------


def _runner(cost_each=0.30, answer="ok"):
    calls = []

    def run(case):
        calls.append(case.id)
        return _result(answer=answer, cost=cost_each, latency_ms=100.0,
                       usage={"route": {"cost": cost_each, "calls": 1}})

    return run, calls


def test_l3_stops_before_the_next_case_once_the_budget_is_spent():
    cases = [Case(id=f"rules-{i:02d}", category="rules", question="q", expect={}) for i in range(10)]
    run, calls = _runner(0.30)
    report = run_live(cases, run, max_cost=1.0)
    assert len(calls) == 4  # 0.9 < 1.0 so the 4th runs; 1.2 >= 1.0 stops before the 5th
    assert report["stopped_early"] is True
    assert report["not_run"] == [c.id for c in cases[4:]]
    assert report["cost"]["total"] == pytest.approx(1.2)


def test_l3_runner_exceptions_count_as_failed_cases_not_crashes():
    def boom(case):
        raise RuntimeError("network down")

    report = run_live([_case()], boom, max_cost=1.0)
    [row] = report["cases"]
    assert row["passed"] is False and "network down" in row["error"]


def test_l3_estimate_scales_with_cases_and_model_price():
    cases = [_case(category=c) for c in ("rules", "lookup", "campaign", "out_of_scope")]
    opus = estimate(cases, ModelConfig.default())
    haiku = estimate(cases, ModelConfig.parse("all=claude-haiku-5-5"))
    assert opus > 0 and haiku < opus / 10
    assert estimate(cases * 2, ModelConfig.default()) == pytest.approx(2 * opus)


# --- L4 results and aggregates ----------------------------------------------------------------


def test_l4_report_aggregates_by_category_with_latency_and_cost():
    cases = [_case(category="rules", contains=["ok"]), _case(category="lookup", contains=["no"])]
    run, _ = _runner(0.1)
    report = run_live(cases, run, max_cost=5)
    assert report["categories"]["rules"]["pass_rate"] == 1.0
    assert report["categories"]["lookup"]["pass_rate"] == 0.0
    assert report["overall"]["pass_rate"] == 0.5
    assert report["latency_ms"]["p50"] == 100.0
    assert report["cost"]["by_step"]["route"] == pytest.approx(0.2)


def test_l4_save_records_version_metadata(tmp_path):
    path = save({"overall": {"pass_rate": 1.0}}, "baseline", root=tmp_path)
    data = json.loads(path.read_text())
    assert path.name.startswith("baseline")
    for key in ("git_commit", "git_dirty", "dataset", "created_at"):
        assert key in data["meta"]
    assert data["overall"]["pass_rate"] == 1.0


@pytest.mark.parametrize("label", ["../escape", "a/b", "", "x" * 81])
def test_l4_labels_are_safe_file_names(tmp_path, label):
    with pytest.raises(ValueError):
        save({}, label, root=tmp_path)


# --- M5 compare -------------------------------------------------------------------------------


def test_m5_compare_shows_pass_rate_cost_and_latency_deltas():
    a = {"meta": {"label": "opus"}, "categories": {"rules": {"pass_rate": 0.9, "n": 10}},
         "overall": {"pass_rate": 0.9}, "cost": {"total": 4.0}, "latency_ms": {"p50": 3000.0}}
    b = {"meta": {"label": "haiku"}, "categories": {"rules": {"pass_rate": 0.8, "n": 10}},
         "overall": {"pass_rate": 0.8}, "cost": {"total": 0.2}, "latency_ms": {"p50": 900.0}}
    text = "\n".join(compare(a, b))
    assert "rules" in text and "-10.0" in text
    assert "4.00" in text and "0.20" in text
    assert "3000" in text and "900" in text


# --- G1/G2 gates ------------------------------------------------------------------------------

GATES = {"srd_recall@5": 0.85, "notes_recall@5": 0.90, "leak_rate": 0.0, "contract_violations": 0,
         "live_pass_rate": 0.85, "live_no_leak": 1.0, "live_citations_ok": 0.95}


def _offline(**kw):
    base = {"valid": True, "srd": {"dense": {"recall@5": 0.9}}, "notes": {"recall@5": 0.95},
            "leak_rate": 0.0, "contract": {"violations": []}}
    return {**base, **kw}


def test_g1_clean_offline_report_passes():
    assert gate(_offline(), GATES) == []


@pytest.mark.parametrize(
    "override, needle",
    [
        ({"valid": False}, "dataset"),
        ({"srd": {"dense": {"recall@5": 0.5}}}, "srd"),
        ({"notes": {"recall@5": 0.5}}, "notes"),
        ({"leak_rate": 0.01}, "leak"),
        ({"contract": {"violations": ["rules-01: N9"]}}, "contract"),
    ],
)
def test_g1_each_breach_is_reported(override, needle):
    breaches = gate(_offline(**override), GATES)
    assert breaches and any(needle in b for b in breaches)


def test_g2_live_thresholds():
    live = {"overall": {"pass_rate": 0.9}, "scores": {"no_leak_ok": 1.0, "citations_ok": 0.97}}
    assert gate(_offline(), GATES, live) == []
    bad = {"overall": {"pass_rate": 0.7}, "scores": {"no_leak_ok": 0.99, "citations_ok": 0.9}}
    breaches = gate(_offline(), GATES, bad)
    assert len(breaches) == 3
