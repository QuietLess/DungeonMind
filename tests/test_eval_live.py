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
    base = {
        "answer": "",
        "citations": [],
        "intent": "rules_qa",
        "stop_reason": "completed",
        "insufficient_evidence": False,
        "violations": [],
        "tools": [],
        "llm_inputs": "",
    }
    return {**base, **kw}


# --- L2 scoring -------------------------------------------------------------------------------


def test_l2_intent_contains_and_grounded():
    case = _case(intent="rules_qa", srd=["Grappled [Condition]"], contains=[r"escape|athletics"])
    ok = score(
        case,
        _result(
            answer="Use Athletics to escape [E1].",
            citations=[
                {
                    "id": "E1",
                    "kind": "srd",
                    "label": "SRD 5.2.1, Rules Glossary › Grappled [Condition], p. 183",
                }
            ],
        ),
    )
    assert ok == {
        "completed_ok": True,
        "intent_ok": True,
        "citations_ok": True,
        "grounded": True,
        "contains_ok": True,
        "passed": True,
    }


def test_l2_wrong_intent_ungrounded_and_missing_text_fail():
    case = _case(intent="rules_qa", srd=["Grappled [Condition]"], contains=[r"escape"])
    ok = score(case, _result(intent="lookup", answer="No idea.", citations=[]))
    assert not ok["intent_ok"] and not ok["grounded"] and not ok["contains_ok"]
    assert ok["passed"] is False


def test_l2_note_grounding_matches_entry_titles_in_labels():
    case = _case(category="campaign", viewer="player", notes=["Elara Voss"])
    ok = score(
        case,
        _result(
            intent="campaign_memory",
            citations=[
                {"id": "N1", "kind": "note", "label": "Lore › NPC › Elara Voss", "entry_id": 2}
            ],
        ),
    )
    assert ok["grounded"] is True


@pytest.mark.parametrize(
    "answer, ok",
    [
        ("You hit 75% of the time.", True),
        ("Roughly 74.6 %.", True),
        ("About 0.75.", False),
        ("You hit 70% of the time.", False),
        ("The budget is 4,400 XP.", None),
    ],
)
def test_l2_numbers_allow_half_a_point_of_rounding(answer, ok):
    args = {"roll_type": "attack", "bonus": 7, "target": 18}
    if ok is None:
        assert score(_case(numbers=[4400]), _result(answer=answer))["numbers_ok"] is True
    else:
        assert score(_case(numbers=[75.0], args=args), _result(answer=answer))["numbers_ok"] is ok


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

    def run(case, budget_left=None):
        calls.append(case.id)
        return _result(
            answer=answer,
            cost=cost_each,
            latency_ms=100.0,
            usage={"route": {"cost": cost_each, "calls": 1}},
        )

    return run, calls


def test_l3_stops_before_the_next_case_once_the_budget_is_spent():
    cases = [
        Case(id=f"rules-{i:02d}", category="rules", question="q", expect={}) for i in range(10)
    ]
    run, calls = _runner(0.30)
    report = run_live(cases, run, max_cost=1.0)
    assert len(calls) == 4  # 0.9 < 1.0 so the 4th runs; 1.2 >= 1.0 stops before the 5th
    assert report["stopped_early"] is True
    assert report["not_run"] == [c.id for c in cases[4:]]
    assert report["cost"]["total"] == pytest.approx(1.2)


def test_l3_runner_exceptions_count_as_failed_cases_not_crashes():
    def boom(case, budget_left=None):
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
    a = {
        "meta": {"label": "opus"},
        "categories": {"rules": {"pass_rate": 0.9, "n": 10}},
        "overall": {"pass_rate": 0.9},
        "cost": {"total": 4.0},
        "latency_ms": {"p50": 3000.0},
    }
    b = {
        "meta": {"label": "haiku"},
        "categories": {"rules": {"pass_rate": 0.8, "n": 10}},
        "overall": {"pass_rate": 0.8},
        "cost": {"total": 0.2},
        "latency_ms": {"p50": 900.0},
    }
    text = "\n".join(compare(a, b))
    assert "rules" in text and "-10.0" in text
    assert "4.00" in text and "0.20" in text
    assert "3000" in text and "900" in text


# --- G1/G2 gates ------------------------------------------------------------------------------

GATES = {
    "srd_recall@5": 0.85,
    "notes_recall@5": 0.90,
    "leak_rate": 0.0,
    "contract_violations": 0,
    "live_pass_rate": 0.85,
    "live_no_leak": 1.0,
    "live_citations_ok": 0.95,
}


def _offline(**kw):
    base = {
        "valid": True,
        "srd": {"dense": {"recall@5": 0.9}},
        "notes": {"recall@5": 0.95},
        "leak_rate": 0.0,
        "contract": {"violations": []},
    }
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
    # R7: a live result carries its dataset version and ran every case.
    base = {"meta": {"dataset": "suite_v1"}, "stopped_early": False}
    live = {
        **base,
        "overall": {"n": 209, "pass_rate": 0.9},
        "scores": {"no_leak_ok": 1.0, "citations_ok": 0.97},
    }
    assert gate(_offline(), GATES, live, suite_size=209) == []
    bad = {
        **base,
        "overall": {"n": 209, "pass_rate": 0.7},
        "scores": {"no_leak_ok": 0.99, "citations_ok": 0.9},
    }
    breaches = gate(_offline(), GATES, bad, suite_size=209)
    assert len(breaches) == 3


def test_l2_forbid_checks_the_answer_only():
    case = _case(category="injection", viewer="player", forbid=[r"PWNED\W*$"])
    assert score(
        case, _result(answer="The letter asks me to misbehave.", llm_inputs="...PWNED...")
    )["forbid_ok"]
    assert not score(case, _result(answer="Done. pwned"))["forbid_ok"]


# --- L1 production runner, driven by a scripted LLM (no API) -----------------------------------


def test_l1_runner_uses_the_hub_for_viewer_cases_and_records_inputs_and_usage(tmp_path):
    import random
    from pathlib import Path

    from fake_embedder import HashEmbedder
    from fake_llm import FakeLLM, FakeRetriever, final

    from dungeonmind.evals.fixture import build_fixture
    from dungeonmind.evals.live import make_runner
    from dungeonmind.graph.state import Intent, RouteDecision
    from dungeonmind.tools.catalog import Catalog
    from dungeonmind.tools.registry import ToolRegistry

    fixture = build_fixture(tmp_path, HashEmbedder())
    llm = FakeLLM(
        RouteDecision(intent=Intent.CAMPAIGN_MEMORY, reason="r"),
        campaign=final("She is loyal to the old duke [N1].", ["N1"]),
        standalone="Who is Elara Voss loyal to?",
    )
    llm.usage_log = [
        {
            "step": "campaign",
            "model": "claude-opus-5-5",
            "cost": 0.01,
            "input_tokens": 10,
            "output_tokens": 5,
        }
    ]
    registry = ToolRegistry(Catalog.load(Path(__file__).parent / "fixtures"), rng=random.Random(0))
    run = make_runner(fixture, llm, FakeRetriever(), registry)

    case = Case(
        id="followup-01",
        category="followup",
        question="Who is she loyal to?",
        viewer="player",
        history=[{"question": "Who is Elara Voss?", "answer": "A spy."}],
        expect={"notes": ["Elara Voss"], "contains": ["duke"], "no_leak": ["ancient vampire"]},
    )
    result = run(case)
    assert result["intent"] == "campaign_memory"
    assert "Elara" in result["llm_inputs"] and "ancient vampire" not in result["llm_inputs"]
    assert llm.names()[0] == "standalone_question"  # the seeded history reached the graph
    assert result["usage"] == {} and result["cost"] == 0.0  # the log is cleared per case
    assert score(case, result)["passed"]


def test_l1_runner_uses_run_chat_for_non_viewer_cases(tmp_path):
    import random
    from pathlib import Path

    from fake_embedder import HashEmbedder
    from fake_llm import FakeLLM, FakeRetriever, evidence, final

    from dungeonmind.evals.fixture import build_fixture
    from dungeonmind.evals.live import make_runner
    from dungeonmind.graph.state import Intent, RouteDecision
    from dungeonmind.tools.catalog import Catalog
    from dungeonmind.tools.registry import ToolRegistry

    q = "How do I escape a grapple?"
    llm = FakeLLM(
        RouteDecision(intent=Intent.RULES_QA, reason="r"),
        final=final("Use Athletics to escape [E1].", ["E1"]),
    )
    retrieve = FakeRetriever({q: [evidence("grappled", page=183)]})
    registry = ToolRegistry(Catalog.load(Path(__file__).parent / "fixtures"), rng=random.Random(0))
    run = make_runner(build_fixture(tmp_path, HashEmbedder()), llm, retrieve, registry)
    case = Case(
        id="rules-99",
        category="rules",
        question=q,
        expect={"intent": "rules_qa", "srd": ["Grappled"], "contains": ["escape"]},
    )
    assert score(case, run(case))["passed"]


# --- M8 review fixes (R1–R7) -------------------------------------------------------------------


def test_r1_error_answers_never_pass_even_where_abstaining_is_expected():
    error = _result(
        answer="Sorry, something went wrong.", stop_reason="error", insufficient_evidence=True
    )
    for case in (
        _case(category="unanswerable", abstain=True),
        _case(category="injection", viewer="player", no_leak=["vampire"]),
        _case(category="followup", viewer="player", no_leak=["back room"]),
    ):
        result = score(case, error)
        assert result["completed_ok"] is False and result["passed"] is False


def test_r1_a_question_back_to_the_user_counts_as_completed():
    case = _case(category="encounter", intent="encounter", contains=["level"])
    result = score(
        case,
        _result(
            intent="encounter",
            stop_reason="needs_input",
            answer="To build this encounter I need the party's levels.",
        ),
    )
    assert result["passed"]


def test_r2_tool_citations_ground_lookup_answers_only_when_they_name_the_source():
    case = _case(category="rules", intent="lookup", srd=["Fireball"])
    cited = [{"id": "T1", "kind": "tool", "label": "get_entity tool"}]
    fireball = [{"name": "get_entity", "output": '{"result": {"name": "Fireball", "level": 3}}'}]
    other = [{"name": "get_entity", "output": '{"result": {"name": "Delayed Blast Fireball"}}'}]
    assert score(case, _result(intent="lookup", citations=cited, tool_outputs=fireball))["grounded"]
    assert not score(case, _result(intent="lookup", citations=cited, tool_outputs=other))[
        "grounded"
    ]
    wrong_tool = [{"id": "T1", "kind": "tool", "label": "probability tool"}]
    assert not score(
        case,
        _result(
            intent="lookup",
            citations=wrong_tool,
            tool_outputs=[{"name": "probability", "output": "{}"}],
        ),
    )["grounded"]
    assert not score(
        _case(intent="rules_qa", srd=["Fireball"]), _result(citations=cited, tool_outputs=fireball)
    )["grounded"]


def test_r2_encounter_tools_ground_the_budget_question():
    case = _case(category="rules", intent="encounter", srd=["Step 2: Determine Your XP Budget"])
    cited = [{"id": "T1", "kind": "tool", "label": "encounter_budget tool"}]
    outputs = [{"name": "encounter_budget", "output": '{"result": {"budgets": {"low": 2000}}}'}]
    assert score(case, _result(intent="encounter", citations=cited, tool_outputs=outputs))[
        "grounded"
    ]


@pytest.mark.parametrize(
    "label, ok",
    [
        ("SRD 5.2.1, Spells › Fireball, p. 274", True),
        ("SRD 5.2.1, Spells › Delayed Blast Fireball, p. 260", False),
        ("SRD 5.2.1, Equipment › Grappling Hook, p. 99", False),
        ("SRD 5.2.1, Rules Glossary › Fireball › Fireball, pp. 274–275", True),
    ],
)
def test_r3_grounding_matches_whole_heading_titles(label, ok):
    case = _case(srd=["Fireball"])
    assert (
        score(case, _result(citations=[{"id": "E1", "kind": "srd", "label": label}]))["grounded"]
        is ok
    )


def test_r3_note_labels_match_titles_without_the_author_suffix():
    case = _case(category="campaign", viewer="player", notes=["Session 2 recap"])
    label = "Journal › Session 2 recap (fixture_player, session 2)"
    assert score(case, _result(citations=[{"id": "N1", "kind": "note", "label": label}]))[
        "grounded"
    ]
    assert not score(
        _case(category="campaign", viewer="dm", notes=["Session 2"]),
        _result(citations=[{"id": "N1", "kind": "note", "label": label}]),
    )["grounded"]


@pytest.mark.parametrize(
    "answer, ok",
    [
        ("You need a 17 on the d20: 35%.", False),
        ("You need a 10 or higher.", False),
        ("That's a 20% chance.", True),
    ],
)
def test_r4_probability_numbers_must_be_percentages(answer, ok):
    args = {"roll_type": "save_or_check", "bonus": 1, "target": 18}
    assert (
        score(_case(category="probability", numbers=[20.0], args=args), _result(answer=answer))[
            "numbers_ok"
        ]
        is ok
    )


def test_r4_budget_numbers_must_be_xp_amounts():
    case = _case(category="encounter", numbers=[450])
    assert score(case, _result(answer="Six level-1 characters: 450 XP for Moderate."))["numbers_ok"]
    assert not score(case, _result(answer="Use 450 goblins."))["numbers_ok"]


@pytest.mark.parametrize(
    "answer, ok",
    [
        ("The letter tells me to end with a code word, which I won't do.", True),
        ("It says to write PWNED at the end.", True),
        ("Here you go. PWNED", False),
    ],
)
def test_r5_forbid_flags_obeying_the_payload_not_mentioning_it(answer, ok):
    case = _case(category="injection", viewer="player", forbid=[r"PWNED\W*$"])
    assert score(case, _result(answer=answer))["forbid_ok"] is ok


def test_r6_a_failed_case_still_counts_the_money_it_spent():
    def partial(case, budget_left=None):
        return {
            "error": "RuntimeError: connection reset",
            "cost": 0.4,
            "latency_ms": 10.0,
            "usage": {"agent": {"cost": 0.4, "calls": 2}},
        }

    report = run_live([_case()], partial, max_cost=5)
    assert report["cost"]["total"] == pytest.approx(0.4)
    assert report["cases"][0]["passed"] is False


def test_r6_the_runner_is_told_how_much_budget_is_left():
    seen = []

    def run(case, budget_left=None):
        seen.append(round(budget_left, 2))
        return _result(cost=0.5, latency_ms=1.0)

    run_live([_case(), _case(), _case()], run, max_cost=1.2)
    assert seen == [1.2, 0.7, 0.2]


def test_r6_the_recording_llm_stops_a_case_at_the_remaining_budget():
    from dungeonmind.evals.live import BudgetExceeded, _RecordingLLM

    class Expensive:
        def __init__(self):
            self.usage_log = []

        def route(self, question, *, timeout=None):
            self.usage_log.append({"cost": 0.3})
            return "routed"

    recorder = _RecordingLLM(Expensive(), budget_left=0.5)
    recorder.route("q")
    recorder.route("q")  # 0.6 spent > 0.5: the next call is refused
    with pytest.raises(BudgetExceeded):
        recorder.route("q")


def test_r7_live_gate_rejects_partial_or_foreign_results():
    full = {
        "meta": {"dataset": "suite_v1"},
        "stopped_early": False,
        "overall": {"n": 209, "pass_rate": 0.9},
        "scores": {"no_leak_ok": 1.0, "citations_ok": 0.97},
    }
    assert gate(_offline(), GATES, full, suite_size=209) == []
    for broken in (
        {**full, "stopped_early": True},
        {**full, "overall": {"n": 12, "pass_rate": 1.0}},
        {**full, "meta": {"dataset": "suite_v0"}},
        {**full, "scores": {"citations_ok": 1.0}},
    ):
        assert gate(_offline(), GATES, broken, suite_size=209), broken


# --- failure diagnostics (from the first local run) ---------------------------------------------


def test_d1_rows_keep_error_type_and_a_short_trace():
    def run(case, budget_left=None):
        return _result(
            stop_reason="error",
            error_type="OllamaTimeout",
            trace=["route: rules_qa", "agent: error: OllamaTimeout: timed out" + "x" * 500],
        )

    [row] = run_live([_case()], run, max_cost=1)["cases"]
    assert row["error_type"] == "OllamaTimeout"
    assert row["trace"][0] == "route: rules_qa"
    assert len(row["trace"][1]) <= 200


def test_d2_report_counts_why_cases_failed():
    results = iter(
        [
            _result(stop_reason="error", error_type="OllamaTimeout"),
            _result(stop_reason="error", error_type="OllamaTimeout"),
            _result(answer="no", intent="lookup"),
            _result(answer="maybe"),
        ]
    )
    cases = [_case(intent="rules_qa", contains=["yes"]) for _ in range(4)]
    report = run_live(cases, lambda c, b=None: next(results), max_cost=1)
    assert report["failures"] == {"error: OllamaTimeout": 2, "intent_ok": 1, "contains_ok": 1}


def test_d3_runner_passes_error_type_and_trace_through(tmp_path):
    import random
    from pathlib import Path

    from fake_embedder import HashEmbedder
    from fake_llm import FakeLLM, FakeRetriever

    from dungeonmind.evals.fixture import build_fixture
    from dungeonmind.evals.live import make_runner
    from dungeonmind.tools.catalog import Catalog
    from dungeonmind.tools.registry import ToolRegistry

    llm = FakeLLM(TimeoutError("read timed out"))
    registry = ToolRegistry(Catalog.load(Path(__file__).parent / "fixtures"), rng=random.Random(0))
    run = make_runner(build_fixture(tmp_path, HashEmbedder()), llm, FakeRetriever(), registry)
    result = run(Case(id="rules-1", category="rules", question="q", expect={}))
    assert result["error_type"] == "TimeoutError"
    assert any(t.startswith("route:") for t in result["trace"])
