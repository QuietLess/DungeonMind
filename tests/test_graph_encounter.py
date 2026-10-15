"""M5 encounter path through the orchestrator (docs/ARCHITECTURE.md › M5 contract › Graph changes).

Offline: scripted FakeLLM (route / extract_encounter / present_encounter / agent_step) and the real
builder over the 8-creature fixture catalog. The store classes are imported lazily so a missing
module fails each test rather than the whole file.
"""

import random
from pathlib import Path

from fake_llm import FakeLLM, FakeRetriever, final, final_turn

from dungeonmind.graph.build import OTHER_EDITION_NOTE, Deps, run_chat
from dungeonmind.graph.state import EncounterExtraction, Intent, RouteDecision
from dungeonmind.tools.catalog import Catalog
from dungeonmind.tools.encounter_builder import EncounterRequest
from dungeonmind.tools.registry import ToolRegistry

FIXTURES = Path(__file__).parent / "fixtures"
Q = "Build a hard ruined-temple encounter for four level-5 characters with undead."
REQUEST = EncounterRequest(
    party_levels=[5, 5, 5, 5],
    difficulty="high",
    creature_types=["undead"],
    exclude_flying=True,
    theme="ruined temple",
)


def _store_module():
    from dungeonmind.graph import store

    return store


def _memory_store():
    return _store_module().InMemoryStore()


def _deps(llm, store=None, **kw) -> Deps:
    registry = ToolRegistry(Catalog.load(FIXTURES), rng=random.Random(0))
    if store is not None:
        kw["store"] = store
    return Deps(llm=llm, retrieve=FakeRetriever(), registry=registry, **kw)


def _llm(extraction, presentation=None, **kw) -> FakeLLM:
    route = RouteDecision(intent=Intent.ENCOUNTER, reason="encounter")
    if presentation is None:
        presentation = final("Option 1 uses Wights [T1].", ["T1"])
    return FakeLLM(route, extraction=extraction, presentation=presentation, **kw)


def _build(request=REQUEST) -> EncounterExtraction:
    return EncounterExtraction(kind="build", request=request)


def _collapsed(state) -> list[str]:
    out: list[str] = []
    for e in state["trace"]:
        if not out or out[-1] != e["node"]:
            out.append(e["node"])
    return out


def _payload(llm: FakeLLM, name: str) -> dict:
    (payload,) = [p for n, p in llm.calls if n == name]
    return payload


class RecordingStore:
    """A ConversationStore that records every call (duck-typed, like any store)."""

    def __init__(self, initial=None):
        self.data = dict(initial or {})
        self.gets: list[str] = []
        self.puts: list[tuple[str, EncounterRequest]] = []

    def get(self, conversation_id):
        self.gets.append(conversation_id)
        return self.data.get(conversation_id)

    def put(self, conversation_id, request):
        self.puts.append((conversation_id, request))
        self.data[conversation_id] = request


def _assert_needs_input(state, llm):
    assert state["stop_reason"] == "needs_input"
    assert state["answer"].strip()
    assert state.get("tool_calls", []) == []
    assert "present_encounter" not in llm.names()
    assert state["confidence"] == "low"
    assert state["insufficient_evidence"] is False
    assert state["citations"] == []
    assert "no-citations" not in state.get("violations", [])
    assert state["trace"][-1]["node"] == "verify"


# --- routing ---------------------------------------------------------------------------------


def test_encounter_intent_routes_to_the_encounter_node():
    llm = _llm(_build())
    state = run_chat(Q, _deps(llm, store=RecordingStore()))
    assert _collapsed(state) == ["route", "encounter", "verify"]
    assert "agent_step" not in llm.names()
    assert state["intent"] == Intent.ENCOUNTER


def test_kind_other_hands_over_to_the_agent():
    llm = _llm(
        EncounterExtraction(kind="other"),
        turns=[final_turn(final("The XP budget is in the tool result.", insufficient=True))],
    )
    state = run_chat("What's the XP budget for four level-5s?", _deps(llm, RecordingStore()))
    assert _collapsed(state) == ["route", "encounter", "agent", "verify"]
    assert llm.names() == ["route", "extract_encounter", "agent_step"]
    assert state["stop_reason"] == "completed"


# --- build -----------------------------------------------------------------------------------


def test_build_records_t1_and_presents_it():
    store = RecordingStore()
    llm = _llm(_build())
    deps = _deps(llm, store, timeout_s=33.0)
    state = run_chat(Q, deps, conversation_id="conv-1")

    (record,) = state["tool_calls"]
    assert record["id"] == "T1"
    assert record["name"] == "encounter_builder"
    assert record["is_error"] is False
    assert record["arguments"]["party_levels"] == [5, 5, 5, 5]
    assert record["arguments"]["difficulty"] == "high"
    result = record["output"]["result"]
    assert set(result) >= {"options", "budget", "band", "note"}
    assert result["budget"] == 4400
    assert list(result["band"]) == [3000, 4400]
    assert isinstance(result["options"], list)

    extract = _payload(llm, "extract_encounter")
    assert extract["question"] == Q and extract["previous"] is None
    present = _payload(llm, "present_encounter")
    assert present["question"] == Q
    assert present["request"] == REQUEST
    assert present["record"] == record
    assert ("extract_encounter", 33.0) in llm.timeouts
    assert ("present_encounter", 33.0) in llm.timeouts

    assert store.puts == [("conv-1", REQUEST)]
    assert state["stop_reason"] == "completed"
    assert {c["id"] for c in state["citations"]} == {"T1"}
    assert state["citations"][0]["kind"] == "tool"
    assert state.get("violations", []) == []
    assert state["confidence"] == "high"


def test_build_without_conversation_id_does_not_touch_the_store():
    store = RecordingStore()
    state = run_chat(Q, _deps(_llm(_build()), store))
    assert state["tool_calls"][0]["name"] == "encounter_builder"
    assert store.puts == [] and store.gets == []


def test_presentation_is_verified_invented_ids_stripped():
    llm = _llm(_build(), presentation=final("Use Wights [T1]. Ghosts too [T5, E2].", ["T1", "E2"]))
    state = run_chat(Q, _deps(llm, RecordingStore()))
    assert {c["id"] for c in state["citations"]} == {"T1"}
    assert {"T5", "E2"} <= set(state["violations"])
    assert "T5" not in state["answer"] and "E2" not in state["answer"]
    assert "[T1]" in state["answer"]


def test_uncited_presentation_is_flagged():
    llm = _llm(_build(), presentation=final("Use some Wights.", []))
    state = run_chat(Q, _deps(llm, RecordingStore()))
    assert "no-citations" in state["violations"]
    assert state["confidence"] == "low"


def test_default_store_is_in_memory_and_remembers_the_request():
    deps = _deps(_llm(_build()))  # no store passed: the default applies
    run_chat(Q, deps, conversation_id="conv-default")
    assert deps.store.get("conv-default") == REQUEST
    assert deps.store.get("someone-else") is None


# --- build with missing information ----------------------------------------------------------


def test_build_with_no_request_asks_for_the_missing_info():
    llm = _llm(EncounterExtraction(kind="build", request=None, missing=["party_levels"]))
    state = run_chat("Build me an undead encounter.", _deps(llm, RecordingStore()))
    _assert_needs_input(state, llm)


def test_build_with_missing_fields_asks_even_if_a_request_was_given():
    llm = _llm(EncounterExtraction(kind="build", request=REQUEST, missing=["party_levels"]))
    store = RecordingStore()
    state = run_chat("Build me an undead encounter.", _deps(llm, store), conversation_id="c")
    _assert_needs_input(state, llm)
    assert store.puts == []


# --- revise ----------------------------------------------------------------------------------


def test_revise_without_previous_needs_input():
    llm = _llm(EncounterExtraction(kind="revise", direction="harder"))
    state = run_chat("Make it harder.", _deps(llm, RecordingStore()))
    _assert_needs_input(state, llm)


def test_revise_with_unknown_conversation_needs_input():
    llm = _llm(EncounterExtraction(kind="revise", direction="harder"))
    store = RecordingStore()
    state = run_chat("Make it harder.", _deps(llm, store), conversation_id="new-conv")
    _assert_needs_input(state, llm)
    assert store.gets == ["new-conv"]


def test_revise_uses_the_stored_previous_request():
    moderate = REQUEST.model_copy(update={"difficulty": "moderate"})
    store = RecordingStore({"conv-1": moderate})
    llm = _llm(EncounterExtraction(kind="revise", direction="harder"))
    state = run_chat("Make it harder.", _deps(llm, store), conversation_id="conv-1")

    assert _payload(llm, "extract_encounter")["previous"] == moderate
    (record,) = state["tool_calls"]
    assert record["arguments"]["difficulty"] == "high"
    assert record["arguments"]["creature_types"] == ["undead"]
    assert _payload(llm, "present_encounter")["request"] == REQUEST
    assert store.data["conv-1"] == REQUEST
    assert state["stop_reason"] == "completed"


def test_revise_after_a_previous_run_in_the_same_conversation():
    store = _memory_store()
    first = _llm(_build())
    run_chat(Q, _deps(first, store), conversation_id="conv-2")

    second = _llm(EncounterExtraction(kind="revise", direction="easier"))
    state = run_chat("Make it a bit easier.", _deps(second, store), conversation_id="conv-2")

    assert _payload(second, "extract_encounter")["previous"] == REQUEST
    assert state["tool_calls"][0]["arguments"]["difficulty"] == "moderate"
    assert store.get("conv-2").difficulty == "moderate"


def test_revise_past_high_explains_the_limit():
    store = RecordingStore({"conv-1": REQUEST})  # already High
    llm = _llm(EncounterExtraction(kind="revise", direction="harder"))
    state = run_chat("Even harder!", _deps(llm, store), conversation_id="conv-1")

    assert state["stop_reason"] == "completed"
    assert state["insufficient_evidence"] is True
    assert "high" in state["answer"].lower()
    assert state.get("tool_calls", []) == []
    assert "present_encounter" not in llm.names()
    assert "no-citations" not in state.get("violations", [])


def test_revise_past_low_explains_the_limit():
    low = REQUEST.model_copy(update={"difficulty": "low"})
    llm = _llm(EncounterExtraction(kind="revise", direction="easier"))
    state = run_chat("Easier.", _deps(llm, RecordingStore({"c": low})), conversation_id="c")
    assert state["stop_reason"] == "completed"
    assert state["insufficient_evidence"] is True
    assert "low" in state["answer"].lower()


# --- errors (behaviour 6) --------------------------------------------------------------------


class _ExtractFailed(Exception):
    pass


class _PresentFailed(Exception):
    pass


def test_extract_exception_gives_error_stop():
    state = run_chat(Q, _deps(_llm(_ExtractFailed("down")), RecordingStore()))
    assert state["stop_reason"] == "error"
    assert state["error_type"] == "_ExtractFailed"
    assert state["confidence"] == "low" and state["insufficient_evidence"] is True
    assert state["trace"][-1]["node"] == "verify"


def test_present_exception_gives_error_stop():
    llm = _llm(_build(), presentation=_PresentFailed("down"))
    state = run_chat(Q, _deps(llm, RecordingStore()))
    assert state["stop_reason"] == "error"
    assert state["error_type"] == "_PresentFailed"
    assert state["trace"][-1]["node"] == "verify"


# --- stores ----------------------------------------------------------------------------------


def test_in_memory_store_get_put():
    store = _memory_store()
    assert store.get("x") is None
    store.put("x", REQUEST)
    assert store.get("x") == REQUEST


def test_json_file_store_round_trips_across_instances(tmp_path):
    path = tmp_path / "conversations.json"
    store_cls = _store_module().JsonFileStore
    store_cls(path).put("conv-1", REQUEST)
    store_cls(path).put("conv-2", REQUEST.model_copy(update={"party_levels": [1, 2]}))

    reopened = store_cls(path)
    assert reopened.get("conv-1") == REQUEST
    assert reopened.get("conv-2").party_levels == [1, 2]
    assert reopened.get("missing") is None


def test_json_file_store_starts_empty_without_a_file(tmp_path):
    store = _store_module().JsonFileStore(tmp_path / "nope.json")
    assert store.get("anything") is None


# --- M5 clarifications -----------------------------------------------------------------------


def test_c_t1_arguments_are_the_request_json_dump():
    state = run_chat(Q, _deps(_llm(_build()), RecordingStore()))
    assert state["tool_calls"][0]["arguments"] == REQUEST.model_dump(mode="json")


def test_c_node_uses_the_registry_catalog():
    knight = {
        "key": "test_ossuary_knight",
        "name": "Ossuary Knight",
        "challenge_rating": 3,
        "experience_points": 700,
        "type": {"key": "undead", "name": "Undead"},
        "size": {"key": "medium", "name": "Medium"},
        "armor_class": 16,
        "hit_points": 50,
        "speed": {"walk": 30},
        "speed_all": {"walk": 30},
        "traits": [],
        "actions": [{"name": "Blade", "desc": "Melee Attack Roll: +5.", "action_type": "ACTION"}],
    }
    registry = ToolRegistry(Catalog([], [knight]), rng=random.Random(0))
    request = EncounterRequest(party_levels=[5], difficulty="moderate")
    deps = Deps(
        llm=_llm(_build(request)), retrieve=FakeRetriever(), registry=registry,
        store=RecordingStore(),
    )  # fmt: skip
    state = run_chat("One level-5 hero, moderate.", deps)
    options = state["tool_calls"][0]["output"]["result"]["options"]
    assert [[m["name"] for m in o["monsters"]] for o in options] == [["Ossuary Knight"]]


def test_c_json_file_store_creates_missing_parent_dirs(tmp_path):
    path = tmp_path / "a" / "b" / "conversations.json"
    store_cls = _store_module().JsonFileStore
    store_cls(path).put("conv-1", REQUEST)
    assert path.exists()
    assert store_cls(path).get("conv-1") == REQUEST


def _other_edition_llm(extraction, **kw) -> FakeLLM:
    route = RouteDecision(intent=Intent.ENCOUNTER, reason="encounter", other_edition=True)
    return FakeLLM(
        route,
        extraction=extraction,
        presentation=final("Option 1 uses Wights [T1].", ["T1"]),
        **kw,
    )


def test_c_other_edition_note_on_a_completed_encounter_answer():
    state = run_chat(Q, _deps(_other_edition_llm(_build()), RecordingStore()))
    assert state["stop_reason"] == "completed"
    assert state["answer"].startswith(OTHER_EDITION_NOTE)
    assert "Option 1 uses Wights [T1]." in state["answer"]


def test_c_no_other_edition_note_on_needs_input():
    llm = _other_edition_llm(EncounterExtraction(kind="build", request=None, missing=["party"]))
    state = run_chat("Build a 2014-style encounter.", _deps(llm, RecordingStore()))
    assert state["stop_reason"] == "needs_input"
    assert not state["answer"].startswith(OTHER_EDITION_NOTE)


# --- P1 revisions are explained (docs/ARCHITECTURE.md › M5 fixes from the live check) --------


def test_p1_revise_passes_the_direction_to_present_encounter():
    moderate = REQUEST.model_copy(update={"difficulty": "moderate"})
    store = RecordingStore({"conv-1": REQUEST})
    llm = _llm(EncounterExtraction(kind="revise", direction="easier"))
    state = run_chat("Make it easier.", _deps(llm, store), conversation_id="conv-1")
    assert state["tool_calls"][0]["arguments"]["difficulty"] == "moderate"
    assert _payload(llm, "present_encounter")["request"] == moderate
    assert llm.revisions == ["easier"]


def test_p1_build_passes_no_revision_to_present_encounter():
    llm = _llm(_build())
    run_chat(Q, _deps(llm, RecordingStore()), conversation_id="conv-1")
    assert llm.revisions == [None]
