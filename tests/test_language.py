"""M8d language support (T1–T4)."""

import random
from pathlib import Path

import pytest
from fake_llm import FakeLLM, FakeNotes, FakeRetriever, evidence, final, note
from hub_support import PASSWORD, make_hub

from dungeonmind.graph.build import Deps, run_chat
from dungeonmind.graph.state import Intent, RouteDecision
from dungeonmind.rag.lexical import tokenize
from dungeonmind.tools.catalog import Catalog
from dungeonmind.tools.registry import ToolRegistry

FIXTURES = Path(__file__).parent / "fixtures"


# --- T1 tokenizer -----------------------------------------------------------------------------


def test_t1_turkish_words_stay_whole_and_fold_diacritics():
    assert tokenize("Gümüş Çınar Köyü ve Altınîa Şehri") == [
        "gumus",
        "cinar",
        "koyu",
        "altinia",
        "sehri",
    ]
    assert tokenize("İSTANBUL ılık") == ["istanbul", "ilik"]
    assert tokenize("gumus cinar") == tokenize("Gümüş Çınar")  # typed without Turkish letters


def test_t1_english_tokenizes_as_before():
    assert tokenize("How does the Grappled condition work?") == ["grappled", "condition", "work"]
    assert tokenize("you're Prone") == ["re", "prone"]


# --- T2 per-campaign embedding model ----------------------------------------------------------


class NamedEmbedder:
    def __init__(self, name):
        self.name = name

    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


def test_t2_a_campaign_remembers_its_embedding_model_and_refuses_a_different_one(tmp_path):
    from dungeonmind.hub.db import StorageError

    hub = make_hub(tmp_path, embedder=NamedEmbedder("model-a"))
    dm = hub.register("dm_dana", PASSWORD)
    camp = hub.create_campaign(dm, "Eldoria")
    hub.create_entry(dm, camp.id, board="journal", title="t", body="Kızıl Köy")
    assert hub.search(dm, camp.id, "kizil koy")

    other = make_hub(tmp_path, embedder=NamedEmbedder("model-b"))
    assert other.entries(dm, camp.id)  # reading still works
    with pytest.raises(StorageError, match="re-index"):
        other.search(dm, camp.id, "kizil")
    with pytest.raises(StorageError, match="re-index"):
        other.create_entry(dm, camp.id, board="journal", title="t2", body="b")


def test_t2_the_default_note_model_is_multilingual():
    from dungeonmind.hub.app import NOTE_MODEL

    assert NOTE_MODEL == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


# --- T3 cross-lingual rules search -------------------------------------------------------------


def _deps(llm, retrieve, notes=None):
    return Deps(
        llm=llm,
        retrieve=retrieve,
        registry=ToolRegistry(Catalog.load(FIXTURES), rng=random.Random(0)),
        search_notes=notes,
    )


def test_t3_non_english_rules_questions_search_the_srd_in_english():
    q = "Tutunma (grapple) nasıl çalışır?"
    retrieve = FakeRetriever({"How does grappling work?": [evidence("grappled")]})
    llm = FakeLLM(
        RouteDecision(intent=Intent.RULES_QA, reason="r"),
        final=final("Tutunma ... [E1].", ["E1"]),
        english="How does grappling work?",
    )
    state = run_chat(q, _deps(llm, retrieve))
    assert retrieve.queries == ["How does grappling work?"]
    assert llm.calls[1][1]["question"] == q  # the answer is asked in the user's language
    assert state["citations"][0]["kind"] == "srd"


def test_t3_english_questions_make_no_extra_call():
    q = "How does grappling work?"
    llm = FakeLLM(
        RouteDecision(intent=Intent.RULES_QA, reason="r"),
        final=final("Yes [E1].", ["E1"]),
        english="unused",
    )
    run_chat(q, _deps(llm, FakeRetriever({q: [evidence("grappled")]})))
    assert "english_query" not in llm.names()


def test_t3_campaign_questions_search_notes_in_the_original_and_srd_in_english():
    q = "Arden kimdir ve tutunabilir mi?"
    notes = FakeNotes({q: [note(1, "Arden loncanın başında.")]})
    retrieve = FakeRetriever({"Who is Arden and can he grapple?": [evidence("grappled")]})
    llm = FakeLLM(
        RouteDecision(intent=Intent.CAMPAIGN_MEMORY, reason="r"),
        campaign=final("Arden [N1].", ["N1"]),
        english="Who is Arden and can he grapple?",
    )
    run_chat(q, _deps(llm, retrieve, notes))
    assert notes.calls == [(q, 8)]
    assert retrieve.queries == ["Who is Arden and can he grapple?"]


def test_t3_a_failed_translation_falls_back_to_the_original():
    q = "Tutunma nasıl çalışır?"
    retrieve = FakeRetriever({q: [evidence("grappled")]})
    llm = FakeLLM(
        RouteDecision(intent=Intent.RULES_QA, reason="r"),
        final=final("Tutunma [E1].", ["E1"]),
        english=RuntimeError("down"),
    )
    state = run_chat(q, _deps(llm, retrieve))
    assert retrieve.queries == [q] and state["stop_reason"] == "completed"
    assert any("warning" in e["detail"] for e in state["trace"])


# --- T4 answer language -----------------------------------------------------------------------


def test_t4_every_answer_prompt_asks_for_the_users_language():
    from dungeonmind.graph import llm as L
    from dungeonmind.rag.answer import SYSTEM_PROMPT

    for prompt in (SYSTEM_PROMPT, L.AGENT_PROMPT, L.PRESENT_PROMPT, L.CAMPAIGN_PROMPT):
        assert "language of the user's question" in prompt
    assert "English" in L.AGENT_PROMPT and "search_rules" in L.AGENT_PROMPT


def test_t3_adapter_english_query_returns_the_rewrite():
    import json
    from types import SimpleNamespace

    from dungeonmind.graph.llm import ClaudeLLM

    resp = SimpleNamespace(
        stop_reason="end_turn",
        model="claude-opus-5-5",
        content=[
            SimpleNamespace(type="text", text=json.dumps({"query": "How does grappling work?"}))
        ],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    calls = []
    client = SimpleNamespace(
        beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: calls.append(kw) or resp))
    )
    out = ClaudeLLM(client=client).english_query("Tutunma nasıl çalışır?")
    assert out == "How does grappling work?"
    assert calls[0]["messages"][0]["content"].endswith("Tutunma nasıl çalışır?")


def test_t2_reindex_moves_a_campaign_to_the_current_model(tmp_path):
    hub = make_hub(tmp_path, embedder=NamedEmbedder("model-a"))
    dm = hub.register("dm_dana", PASSWORD)
    camp = hub.create_campaign(dm, "Eldoria")
    hub.create_entry(dm, camp.id, board="journal", title="Kızıl Köy", body="dağların eteğinde")
    other = make_hub(tmp_path, embedder=NamedEmbedder("model-b"))
    assert other.reindex(camp.id) == 1
    assert other.search(dm, camp.id, "kizil koy")[0].text == "dağların eteğinde"
