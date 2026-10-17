"""M6 campaign node (docs/ARCHITECTURE.md › M6 contract, behaviours 13–15, 20), fully offline.

`search_notes` is a scripted fake bound (as the hub binds it) to one campaign and viewer; the
graph only ever sees the NoteHits it returns.
"""

import random
from pathlib import Path

import pytest
from fake_llm import FakeLLM, FakeNotes, FakeRetriever, evidence, final, note

from dungeonmind.graph.build import Deps, run_chat
from dungeonmind.graph.state import Intent, RouteDecision
from dungeonmind.tools.catalog import Catalog
from dungeonmind.tools.registry import ToolRegistry

FIXTURES = Path(__file__).parent / "fixtures"
Q = "What did we promise Elara?"


def _deps(llm, notes=None, retrieve=None, **kw) -> Deps:
    return Deps(
        llm=llm,
        retrieve=retrieve or FakeRetriever(),
        registry=ToolRegistry(Catalog.load(FIXTURES), rng=random.Random(0)),
        search_notes=notes,
        **kw,
    )


def _route(intent=Intent.CAMPAIGN_MEMORY, other_edition=False) -> RouteDecision:
    return RouteDecision(intent=intent, reason="campaign", other_edition=other_edition)


def _nodes(state) -> list[str]:
    return [e["node"] for e in state["trace"]]


HITS = [
    note(11, "We promised Elara a share of the dragon hoard.", "Journal › Session 2 (Mia)"),
    note(4, "Elara is the duke's spymaster.", "Lore › NPC › Elara", canon=True),
]


# --- 14. campaign node ------------------------------------------------------------------------


def test_14_memory_searches_notes_and_srd_then_answers_with_both():
    notes = FakeNotes({Q: HITS})
    ev = [evidence(f"r{i}") for i in range(6)]
    retrieve = FakeRetriever({Q: ev})
    llm = FakeLLM(
        _route(), campaign=final("A share of the hoard [N1]; she's a spy [N2] [E1].", ["N1"])
    )
    state = run_chat(Q, _deps(llm, notes, retrieve))

    assert notes.calls == [(Q, 8)]
    assert retrieve.queries == [Q]
    assert llm.names() == ["route", "answer_campaign"]
    payload = llm.calls[1][1]
    assert payload["mode"] == "memory"
    assert payload["notes"] == HITS
    assert payload["evidence"] == ev[:4]
    assert state["notes"] == HITS
    assert state["evidence"] == ev[:4]
    assert state["stop_reason"] == "completed"
    assert _nodes(state) == ["route", "campaign", "verify"]
    assert state.get("violations", []) == []


def test_14_session_prep_uses_12_notes_and_prep_mode():
    q = "Turn my notes into 5 scenes"
    notes = FakeNotes({q: HITS})
    llm = FakeLLM(_route(Intent.SESSION_PREP), campaign=final("Scene 1 [N1].", ["N1"]))
    run_chat(q, _deps(llm, notes))
    assert notes.calls == [(q, 12)]
    assert llm.calls[1][1]["mode"] == "prep"


@pytest.mark.parametrize("intent", [Intent.CAMPAIGN_MEMORY, Intent.SESSION_PREP])
def test_14_no_visible_notes_abstains_without_llm_call_or_srd_search(intent):
    retrieve = FakeRetriever({Q: [evidence("x")]})
    llm = FakeLLM(_route(intent))
    state = run_chat(Q, _deps(llm, FakeNotes(), retrieve))
    assert llm.names() == ["route"]
    assert retrieve.queries == []
    assert state["insufficient_evidence"] is True
    assert state["stop_reason"] == "completed"
    assert state["citations"] == []
    assert "no-citations" not in state.get("violations", [])
    assert "notes" in state["answer"].lower()


@pytest.mark.parametrize("intent", [Intent.CAMPAIGN_MEMORY, Intent.SESSION_PREP])
def test_14_without_search_notes_says_campaign_questions_need_the_hub(intent):
    llm = FakeLLM(_route(intent))
    state = run_chat(Q, _deps(llm, None))
    assert llm.names() == ["route"]
    assert "hub" in state["answer"].lower()
    assert state["insufficient_evidence"] is True
    assert state["stop_reason"] == "completed"
    assert _nodes(state) == ["route", "campaign", "verify"]


def test_14_llm_error_gives_error_answer():
    llm = FakeLLM(_route(), campaign=RuntimeError("overloaded"))
    state = run_chat(Q, _deps(llm, FakeNotes({Q: HITS})))
    assert state["stop_reason"] == "error"
    assert state["error_type"] == "RuntimeError"
    assert state["insufficient_evidence"] is True
    assert "overloaded" not in state["answer"]
    assert _nodes(state)[-1] == "verify"


def test_14_search_notes_error_gives_error_answer():
    def broken(query, k):
        raise OSError("disk gone")

    state = run_chat(Q, _deps(FakeLLM(_route()), broken))
    assert state["stop_reason"] == "error"
    assert state["error_type"] == "OSError"


def test_14_srd_retrieve_error_still_answers_from_notes():
    def broken(query):
        raise RuntimeError("index missing")

    llm = FakeLLM(_route(), campaign=final("Hoard [N1].", ["N1"]))
    state = run_chat(Q, _deps(llm, FakeNotes({Q: HITS}), broken))
    assert state["stop_reason"] == "completed"
    assert llm.calls[1][1]["evidence"] == []
    assert any("warning" in e["detail"] for e in state["trace"] if e["node"] == "campaign")


# --- 15. citations ----------------------------------------------------------------------------


def test_15_note_citations_are_labelled_with_source_and_entry_id():
    llm = FakeLLM(_route(), campaign=final("Hoard [N1]. Spy [N2].", ["N1", "N2"]))
    state = run_chat(Q, _deps(llm, FakeNotes({Q: HITS})))
    assert state["citations"] == [
        {"id": "N1", "kind": "note", "label": "Journal › Session 2 (Mia)", "entry_id": 11},
        {"id": "N2", "kind": "note", "label": "Lore › NPC › Elara", "entry_id": 4},
    ]


def test_15_srd_citation_in_campaign_answer_is_labelled_srd():
    ev = [evidence("grappled", page=9)]
    llm = FakeLLM(_route(), campaign=final("Hoard [N1]; grapple rules [E1].", ["N1", "E1"]))
    state = run_chat(Q, _deps(llm, FakeNotes({Q: HITS}), FakeRetriever({Q: ev})))
    kinds = {c["id"]: c["kind"] for c in state["citations"]}
    assert kinds == {"N1": "note", "E1": "srd"}


def test_15_unknown_note_id_is_removed_and_reported():
    llm = FakeLLM(_route(), campaign=final("Hoard [N1]. Secret [N9].", ["N1"]))
    state = run_chat(Q, _deps(llm, FakeNotes({Q: HITS})))
    assert "[N9]" not in state["answer"]
    assert "N9" in state["violations"]
    assert [c["id"] for c in state["citations"]] == ["N1"]


def test_15_malformed_note_marker_is_removed():
    llm = FakeLLM(_route(), campaign=final("Hoard [N1-N2]. Yes [N1].", ["N1"]))
    state = run_chat(Q, _deps(llm, FakeNotes({Q: HITS})))
    assert "[N1-N2]" not in state["answer"]
    assert "malformed-citation" in state["violations"]


def test_15_uncited_campaign_answer_is_downgraded():
    llm = FakeLLM(_route(), campaign=final("You promised her gold.", []))
    state = run_chat(Q, _deps(llm, FakeNotes({Q: HITS})))
    assert "no-citations" in state["violations"]
    assert state["confidence"] == "low"


def test_15_n_markers_in_rules_answers_are_violations():
    """N ids exist only when notes were given; a rules answer citing one is invented."""
    q = "Grapple?"
    llm = FakeLLM(_route(Intent.RULES_QA), final=final("Yes [E1] [N1].", ["E1"]))
    state = run_chat(q, _deps(llm, FakeNotes(), FakeRetriever({q: [evidence("g")]})))
    assert "[N1]" not in state["answer"]
    assert "N1" in state["violations"]


def test_21_other_edition_note_not_added_to_campaign_answers():
    llm = FakeLLM(_route(other_edition=True), campaign=final("Hoard [N1].", ["N1"]))
    state = run_chat(Q, _deps(llm, FakeNotes({Q: HITS})))
    assert state["answer"] == "Hoard [N1]."


def test_14_timeout_is_passed_to_answer_campaign():
    llm = FakeLLM(_route(), campaign=final("Hoard [N1].", ["N1"]))
    run_chat(Q, _deps(llm, FakeNotes({Q: HITS}), timeout_s=12.5))
    assert ("answer_campaign", 12.5) in llm.timeouts


def test_h1_route_knows_when_it_is_inside_a_campaign():
    llm = FakeLLM(_route(), campaign=final("Hoard [N1].", ["N1"]))
    run_chat(Q, _deps(llm, FakeNotes({Q: HITS})))
    assert llm.route_campaign == [True]
    llm = FakeLLM(_route(Intent.OUT_OF_SCOPE))
    run_chat("hi", _deps(llm, None))
    assert llm.route_campaign == [False]
