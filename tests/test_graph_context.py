"""M7 thread context (docs/ARCHITECTURE.md › M7 contract, UI8): follow-ups become standalone."""

import random
from pathlib import Path

from fake_llm import FakeLLM, FakeNotes, FakeRetriever, evidence, final, note

from dungeonmind.graph.build import Deps, run_chat
from dungeonmind.graph.state import Intent, RouteDecision
from dungeonmind.tools.catalog import Catalog
from dungeonmind.tools.registry import ToolRegistry

FIXTURES = Path(__file__).parent / "fixtures"
HISTORY = [{"question": "Who is Elara?", "answer": "Elara is the duke's spymaster [N1]."}]


def _deps(llm, notes=None, retrieve=None):
    return Deps(
        llm=llm,
        retrieve=retrieve or FakeRetriever(),
        registry=ToolRegistry(Catalog.load(FIXTURES), rng=random.Random(0)),
        search_notes=notes,
    )


def _route(intent=Intent.CAMPAIGN_MEMORY):
    return RouteDecision(intent=intent, reason="r")


def test_ui8_without_history_there_is_no_extra_call_or_trace():
    llm = FakeLLM(_route(Intent.OUT_OF_SCOPE), standalone="unused")
    state = run_chat("hi", _deps(llm))
    assert llm.names() == ["route"]
    assert [e["node"] for e in state["trace"]] == ["route", "decline", "verify"]


def test_ui8_follow_up_is_rewritten_and_used_for_routing_search_and_answer():
    standalone = "Who is Elara's brother?"
    notes = FakeNotes({standalone: [note(1, "Elara's brother is Joss.")]})
    llm = FakeLLM(_route(), standalone=standalone, campaign=final("Joss [N1].", ["N1"]))
    state = run_chat("What about her brother?", _deps(llm, notes), history=HISTORY)

    assert llm.calls[0] == (
        "standalone_question", {"question": "What about her brother?", "history": HISTORY}
    )
    assert llm.calls[1] == ("route", {"question": standalone})
    assert notes.calls == [(standalone, 8)]
    assert llm.calls[2][1]["question"] == standalone
    assert state["question"] == standalone
    assert state["original_question"] == "What about her brother?"
    nodes = [e["node"] for e in state["trace"]]
    assert nodes == ["contextualize", "route", "campaign", "verify"]
    assert standalone in state["trace"][0]["detail"]


def test_ui8_rules_question_uses_the_rewrite_for_retrieval():
    standalone = "How does grappling work for a Large creature?"
    retrieve = FakeRetriever({standalone: [evidence("grappled")]})
    llm = FakeLLM(_route(Intent.RULES_QA), standalone=standalone, final=final("Yes [E1].", ["E1"]))
    run_chat("and for a large one?", _deps(llm, retrieve=retrieve), history=HISTORY)
    assert retrieve.queries == [standalone]


def test_ui8_history_is_capped_to_three_exchanges_of_1000_chars():
    long = [{"question": f"q{i}" + "x" * 2000, "answer": "a" * 2000} for i in range(5)]
    llm = FakeLLM(_route(Intent.OUT_OF_SCOPE), standalone="standalone question")
    run_chat("follow up", _deps(llm), history=long)
    sent = llm.calls[0][1]["history"]
    assert len(sent) == 3
    assert [h["question"][:2] for h in sent] == ["q2", "q3", "q4"]
    assert all(len(h["question"]) <= 1000 and len(h["answer"]) <= 1000 for h in sent)


def test_ui8_failed_or_unusable_rewrites_fall_back_to_the_original():
    for bad in (RuntimeError("down"), "", "   ", "x" * 201):
        llm = FakeLLM(_route(Intent.OUT_OF_SCOPE), standalone=bad)
        state = run_chat("follow up?", _deps(llm), history=HISTORY)
        assert llm.calls[1] == ("route", {"question": "follow up?"})
        assert state["question"] == "follow up?"
        assert state["stop_reason"] == "completed"
        assert "warning" in state["trace"][0]["detail"]
