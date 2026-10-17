"""M8 suite validation (V1) and the offline evaluation (O1–O4), with no API calls."""

import json
import random
from collections import Counter
from pathlib import Path

import pytest
from fake_embedder import HashEmbedder

from dungeonmind.evals import suite
from dungeonmind.evals.fixture import build_fixture, note_titles
from dungeonmind.evals.offline import run_offline
from dungeonmind.evals.suite import CATEGORY_MINIMUMS, Case, load, validate
from dungeonmind.tools.catalog import Catalog
from dungeonmind.tools.registry import ToolRegistry

CHUNKS = Path("data/processed/srd_5.2.1_chunks.jsonl")
FIXTURES = Path(__file__).parent / "fixtures"


def _srd_titles() -> set[str]:
    return {json.loads(line)["heading_path"][-1] for line in CHUNKS.open()}


# --- V1 validation ------------------------------------------------------------------------------


def test_v1_suite_has_200_cases_meeting_category_minimums():
    cases = load()
    assert len(cases) >= 200
    counts = Counter(c.category for c in cases)
    for category, minimum in CATEGORY_MINIMUMS.items():
        assert counts[category] >= minimum, category


@pytest.mark.skipif(not CHUNKS.exists(), reason="SRD chunks missing (run ingest)")
def test_v1_the_shipped_suite_is_valid():
    assert validate(load(), _srd_titles(), note_titles()) == []


def _case(**kw) -> Case:
    base = {"id": "x-01", "category": "rules", "question": "q", "expect": {"intent": "rules_qa"}}
    return Case(**{**base, **kw})


@pytest.mark.parametrize(
    "cases, needle",
    [
        ([_case(), _case()], "duplicate"),
        ([_case(category="poetry")], "poetry"),
        ([_case(expect={"srd": ["No Such Rule"]})], "No Such Rule"),
        (
            [_case(category="campaign", viewer="dm", expect={"notes": ["No Such Note"]})],
            "No Such Note",
        ),
        ([_case(category="campaign", expect={"notes": ["Elara Voss"]})], "viewer"),
        ([_case(viewer="dm")], "viewer"),
        ([_case(viewer="wizard", category="campaign")], "wizard"),
        ([_case(expect={"colour": "red"})], "colour"),
        (
            [
                _case(
                    category="probability",
                    expect={
                        "numbers": [50.0],
                        "args": {"roll_type": "attack", "bonus": 5, "target": 15},
                    },
                )
            ],
            "55",
        ),
    ],
)
def test_v1_validation_reports_each_problem(cases, needle):
    problems = validate(cases, {"Grappled [Condition]"}, {"Elara Voss"})
    assert any(needle in p for p in problems), problems


def test_v1_category_minimums_are_checked_on_the_full_suite_only():
    # A subset (as used by --category) is validated without the minimums.
    assert (
        validate([_case(expect={"srd": ["Grappled [Condition]"]})], {"Grappled [Condition]"}, set())
        == []
    )


# --- fixture ----------------------------------------------------------------------------------


@pytest.fixture
def fixture(tmp_path):
    return build_fixture(tmp_path, HashEmbedder())


def test_fixture_hub_has_dm_player_and_respects_visibility(fixture):
    hub, cid, users = fixture.hub, fixture.cid, fixture.users
    dm_titles = {e.title for e in hub.entries(users["dm"], cid)}
    player_titles = {e.title for e in hub.entries(users["player"], cid)}
    assert "Baron Aldric Velkor" in dm_titles and "Baron Aldric Velkor" not in player_titles
    assert "Hag's mark" in player_titles and "Hag's mark" not in dm_titles  # Thorn's private
    assert "Sister Maren" not in dm_titles | player_titles  # Isolde's private
    assert {"Elara Voss", "Strange letter"} <= player_titles


# --- O1–O4 offline ----------------------------------------------------------------------------


def test_o2_o3_notes_retrieval_and_no_leaks_on_the_real_suite(fixture):
    report = run_offline(load(), fixture)
    assert report["notes"]["n"] >= 30
    assert 0 < report["notes"]["recall@5"] <= 1
    assert report["leak_rate"] == 0.0
    assert report["leaks"] == []


def test_o3_the_leak_check_catches_a_leaking_search(fixture, monkeypatch):
    import dungeonmind.hub.entries as entries

    monkeypatch.setattr(entries, "visible_clause", lambda viewer, alias="e": ("1 = 1", []))
    report = run_offline(load(), fixture)
    assert report["leak_rate"] > 0
    assert "campaign-15" in report["leaks"]  # "Is Baron Velkor a vampire?" as the player


def test_o4_covers_follow_ups_with_their_history(fixture):
    registry = ToolRegistry(Catalog.load(FIXTURES), rng=random.Random(0))
    followups = [c for c in load() if c.category == "followup"]
    report = run_offline(followups, fixture, registry=registry)
    assert report["contract"]["n"] == len(followups)
    assert report["contract"]["violations"] == []


def test_o4_contract_check_passes_and_campaign_inputs_hold_only_visible_text(fixture):
    registry = ToolRegistry(Catalog.load(FIXTURES), rng=random.Random(0))
    report = run_offline(load(), fixture, registry=registry)
    assert report["contract"]["n"] >= 60
    assert report["contract"]["violations"] == []


def test_offline_srd_retrieval_uses_the_given_retriever(fixture):
    class Retriever:
        def retrieve(self, query, k, mode):
            from fake_llm import evidence

            return [evidence("grappled", page=1)] if "rappl" in query else []

    cases = [
        _case(id="r-1", expect={"srd": ["Grappled"]}, question="How does grappling work?"),
        _case(id="r-2", expect={"srd": ["Grappled"]}, question="Ready?"),
    ]
    report = run_offline(cases, fixture, retriever=Retriever(), modes=("dense",))
    assert report["srd"]["dense"]["recall@5"] == 0.5
    assert report["srd"]["dense"]["n"] == 2


def test_suite_module_exposes_a_version():
    assert suite.VERSION == "suite_v1"
