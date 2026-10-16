"""Hub chats (M6 contract behaviours 4, 13, 16–19): the real graph with a recording fake LLM.

The central guarantee: the model's input never holds an entry the asking user can't read.
"""

import random
from pathlib import Path

import pytest
from fake_llm import FakeLLM, FakeRetriever, final
from hub_support import PASSWORD, Clock, make_hub, party

from dungeonmind.graph.state import Intent, RouteDecision
from dungeonmind.hub.chats import make_engine
from dungeonmind.hub.errors import Invalid, NotFound, RateLimited
from dungeonmind.tools.catalog import Catalog
from dungeonmind.tools.encounter_builder import EncounterRequest
from dungeonmind.tools.registry import ToolRegistry

FIXTURES = Path(__file__).parent / "fixtures"
SECRET = "Baron Velkor is secretly a vampire"
P2_SECRET = "I stole the baron's signet ring"
OTHER_CAMPAIGN = "Velkor rules the city of Greywater"


def _engine(llm, retrieve=None):
    registry = ToolRegistry(Catalog.load(FIXTURES), rng=random.Random(0))
    return make_engine(llm, retrieve or FakeRetriever(), registry)


def _memory_llm(answer="Here is what your notes say [N1]."):
    route = RouteDecision(intent=Intent.CAMPAIGN_MEMORY, reason="campaign")
    return FakeLLM(route, campaign=final(answer, ["N1"]))


def _llm_input(llm) -> str:
    return repr(llm.calls)


@pytest.fixture
def setup(tmp_path):
    llm = _memory_llm()
    hub = make_hub(tmp_path, engine=_engine(llm))
    camp, dm, p1, p2, p3, outsider = party(hub)
    hub.create_entry(dm, camp.id, board="lore", kind="npc", title="Baron Velkor", body=SECRET)
    hub.create_entry(
        p2, camp.id, board="journal", title="Confession", body=P2_SECRET, visibility="dm"
    )
    hub.create_entry(
        p1,
        camp.id,
        board="journal",
        title="Session 1",
        body="We met Baron Velkor at the harvest feast.",
        session=1,
    )
    other = hub.create_campaign(dm, "Greywater")
    hub.create_entry(
        dm, other.id, board="lore", title="Velkor", body=OTHER_CAMPAIGN, visibility="party"
    )
    return hub, llm, camp, dm, p1, p2


# --- 4. the model never receives an unreadable entry ------------------------------------------


def test_4_player_question_never_sends_hidden_entries_to_the_model(setup):
    hub, llm, camp, dm, p1, p2 = setup
    thread = hub.create_thread(p1, camp.id, "Questions")
    reply = hub.ask(p1, camp.id, thread.id, "Who is Baron Velkor, really? Any signet ring?")

    assert "answer_campaign" in llm.names()  # p1 has a visible note, so the model was asked
    seen = _llm_input(llm)
    assert "harvest feast" in seen
    for hidden in (SECRET, P2_SECRET, OTHER_CAMPAIGN):
        assert hidden not in seen
    assert reply.role == "assistant"
    assert SECRET not in reply.text


def test_4_dm_gets_the_secret_and_the_shared_with_dm_entry(setup):
    hub, llm, camp, dm, p1, p2 = setup
    thread = hub.create_thread(dm, camp.id)
    hub.ask(dm, camp.id, thread.id, "Who is Baron Velkor? Who stole the signet ring?")
    seen = _llm_input(llm)
    assert SECRET in seen and P2_SECRET in seen
    assert OTHER_CAMPAIGN not in seen


def test_4_revealed_lore_reaches_players_on_the_next_question(setup):
    hub, llm, camp, dm, p1, p2 = setup
    lore = next(e for e in hub.entries(dm, camp.id, board="lore"))
    thread = hub.create_thread(p1, camp.id)
    hub.ask(p1, camp.id, thread.id, "Who is Baron Velkor?")
    assert SECRET not in _llm_input(llm)
    hub.update_entry(dm, camp.id, lore.id, visibility="party")
    hub.ask(p1, camp.id, thread.id, "Who is Baron Velkor?")
    assert SECRET in _llm_input(llm)


def test_4_player_with_no_visible_entries_gets_honest_answer_without_model_call(tmp_path):
    llm = _memory_llm()
    hub = make_hub(tmp_path, engine=_engine(llm))
    camp, dm, p1, *_ = party(hub)
    hub.create_entry(dm, camp.id, board="lore", title="Velkor", body=SECRET)
    thread = hub.create_thread(p1, camp.id)
    reply = hub.ask(p1, camp.id, thread.id, "Who is Velkor?")
    assert llm.names() == ["route"]
    assert reply.insufficient_evidence is True
    assert SECRET not in reply.text


# --- 15, 17. saved messages and citations -----------------------------------------------------


def test_17_question_and_answer_are_saved_with_citations(setup):
    hub, llm, camp, dm, p1, p2 = setup
    thread = hub.create_thread(p1, camp.id, "Velkor")
    reply = hub.ask(p1, camp.id, thread.id, "Who is Baron Velkor?")
    assert reply.citations and reply.citations[0]["kind"] == "note"
    assert isinstance(reply.citations[0]["entry_id"], int)
    assert reply.stop_reason == "completed"
    messages = hub.messages(p1, camp.id, thread.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].text == "Who is Baron Velkor?"
    assert messages[1].citations == reply.citations


def test_17_failed_run_saves_apology_without_error_text(tmp_path):
    route = RouteDecision(intent=Intent.CAMPAIGN_MEMORY, reason="campaign")
    llm = FakeLLM(route, campaign=RuntimeError("Traceback /Users/secret/key sk-ant-123"))
    hub = make_hub(tmp_path, engine=_engine(llm))
    camp, dm, *_ = party(hub)
    hub.create_entry(dm, camp.id, board="journal", title="t", body="Velkor notes")
    thread = hub.create_thread(dm, camp.id)
    reply = hub.ask(dm, camp.id, thread.id, "Velkor?")
    assert reply.stop_reason == "error"
    for leaked in ("Traceback", "/Users/secret", "sk-ant"):
        assert leaked not in reply.text
        assert all(leaked not in m.text for m in hub.messages(dm, camp.id, thread.id))


def test_17_engine_exception_also_saves_apology(tmp_path):
    def exploding(question, search_notes, store, conversation_id, history=()):
        raise RuntimeError("boom /secret")

    hub = make_hub(tmp_path, engine=exploding)
    camp, dm, *_ = party(hub)
    thread = hub.create_thread(dm, camp.id)
    reply = hub.ask(dm, camp.id, thread.id, "hello")
    assert reply.stop_reason == "error" and "/secret" not in reply.text


# --- thread privacy ---------------------------------------------------------------------------


def test_threads_are_private_to_their_owner_even_from_the_dm(setup):
    hub, llm, camp, dm, p1, p2 = setup
    thread = hub.create_thread(p1, camp.id, "My secret plans")
    assert [t.id for t in hub.threads(p1, camp.id)] == [thread.id]
    assert hub.threads(dm, camp.id) == []
    for other in (dm, p2):
        with pytest.raises(NotFound):
            hub.messages(other, camp.id, thread.id)
        with pytest.raises(NotFound):
            hub.ask(other, camp.id, thread.id, "hi")


def test_question_length_is_limited(setup):
    hub, llm, camp, dm, p1, p2 = setup
    thread = hub.create_thread(p1, camp.id)
    for bad in ("", "   ", "x" * 2001):
        with pytest.raises(Invalid):
            hub.ask(p1, camp.id, thread.id, bad)


def test_thread_title_is_limited(setup):
    hub, llm, camp, dm, p1, p2 = setup
    with pytest.raises(Invalid):
        hub.create_thread(p1, camp.id, "x" * 121)


# --- 16. encounter state per thread -----------------------------------------------------------


def _spy_engine(log):
    def engine(question, search_notes, store, conversation_id, history=()):
        if question == "save":
            store.put(conversation_id, EncounterRequest(party_levels=[5, 5, 5, 5]))
        log.append((conversation_id, store.get(conversation_id)))
        return {
            "answer": "ok",
            "citations": [],
            "confidence": "low",
            "insufficient_evidence": True,
            "stop_reason": "completed",
            "trace": [],
        }

    return engine


def test_16_encounter_state_is_per_thread_and_survives_restart(tmp_path):
    log = []
    hub = make_hub(tmp_path, engine=_spy_engine(log))
    camp, dm, p1, *_ = party(hub)
    t1 = hub.create_thread(p1, camp.id)
    t2 = hub.create_thread(p1, camp.id)
    hub.ask(p1, camp.id, t1.id, "save")
    hub.ask(p1, camp.id, t2.id, "look")
    assert log[0][1] is not None and log[1][1] is None
    assert log[0][0] != log[1][0]

    reopened = make_hub(tmp_path, engine=_spy_engine(log))
    reopened.ask(p1, camp.id, t1.id, "look")
    assert log[-1][1] == EncounterRequest(party_levels=[5, 5, 5, 5])


def test_13_search_notes_is_bound_to_campaign_and_viewer(tmp_path):
    seen = []

    def engine(question, search_notes, store, conversation_id, history=()):
        seen.extend(h.text for h in search_notes("Velkor", 50))
        return {
            "answer": "ok",
            "citations": [],
            "confidence": "low",
            "insufficient_evidence": True,
            "stop_reason": "completed",
            "trace": [],
        }

    hub = make_hub(tmp_path, engine=engine)
    camp, dm, p1, *_ = party(hub)
    hub.create_entry(dm, camp.id, board="lore", title="Velkor", body=SECRET)
    hub.create_entry(
        dm, camp.id, board="lore", title="Velkor", body="Velkor is a baron", visibility="party"
    )
    hub.ask(p1, camp.id, hub.create_thread(p1, camp.id).id, "q")
    assert len(seen) == 1 and "Velkor is a baron" in seen[0]


# --- 19. spend guard --------------------------------------------------------------------------


def test_19_daily_message_limit_per_user(tmp_path):
    clock = Clock()
    log = []
    hub = make_hub(tmp_path, engine=_spy_engine(log), clock=clock, daily_message_limit=2)
    camp, dm, p1, p2, *_ = party(hub)
    thread = hub.create_thread(p1, camp.id)
    hub.ask(p1, camp.id, thread.id, "one")
    hub.ask(p1, camp.id, thread.id, "two")
    with pytest.raises(RateLimited):
        hub.ask(p1, camp.id, thread.id, "three")
    hub.ask(p2, camp.id, hub.create_thread(p2, camp.id).id, "other user is fine")
    clock.advance(days=1)
    hub.ask(p1, camp.id, thread.id, "next day")
    assert len(log) == 4


def test_19_limit_counts_across_campaigns(tmp_path):
    hub = make_hub(tmp_path, engine=_spy_engine([]), daily_message_limit=1)
    dm = hub.register("dm_dana", PASSWORD)
    a = hub.create_campaign(dm, "A")
    b = hub.create_campaign(dm, "B")
    hub.ask(dm, a.id, hub.create_thread(dm, a.id).id, "one")
    with pytest.raises(RateLimited):
        hub.ask(dm, b.id, hub.create_thread(dm, b.id).id, "two")


# --- UI8: thread history reaches the engine ---------------------------------------------------


def test_ui8_engine_gets_the_last_three_exchanges_of_this_thread_only(tmp_path):
    seen = []

    def engine(question, search_notes, store, conversation_id, history=()):
        seen.append(list(history))
        return {
            "answer": f"answer to {question}",
            "citations": [],
            "confidence": "low",
            "insufficient_evidence": True,
            "stop_reason": "completed",
            "trace": [],
        }

    hub = make_hub(tmp_path, engine=engine)
    camp, dm, p1, *_ = party(hub)
    mine = hub.create_thread(p1, camp.id)
    other = hub.create_thread(p1, camp.id)
    hub.ask(p1, camp.id, other.id, "unrelated question")
    for i in range(5):
        hub.ask(p1, camp.id, mine.id, f"q{i}")
    assert seen[1] == []  # first question in `mine`: nothing from the other thread
    assert seen[-1] == [{"question": f"q{i}", "answer": f"answer to q{i}"} for i in (1, 2, 3)]
