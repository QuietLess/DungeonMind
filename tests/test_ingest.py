"""Fixture tests against the real SRD PDF (skipped if it hasn't been downloaded)."""

import re
from pathlib import Path

import pytest

from dungeonmind.rag.ingest import ingest
from dungeonmind.rag.lexical import LexicalIndex
from dungeonmind.schemas import EntityType

PDF = Path(__file__).parents[1] / "data/raw/srd/SRD_CC_v5.2.1.pdf"
pytestmark = pytest.mark.skipif(not PDF.exists(), reason="run scripts/download_srd.py first")


@pytest.fixture(scope="module")
def chunks():
    return ingest(PDF)


@pytest.fixture(scope="module")
def by_title(chunks):
    return {c.title: c for c in chunks}


def test_chunk_ids_are_unique(chunks):
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))


def test_no_page_furniture_or_toc(chunks):
    footer = re.compile(r"System Reference Document 5\.2\.1 \| \d+|^\d+ \| System Reference")
    assert not any(footer.search(c.text) for c in chunks)
    assert not any(c.chapter in {"Contents", "Front Matter"} for c in chunks)


@pytest.mark.parametrize(
    ("title", "path", "page", "entity"),
    [
        ("Fireball", ["Spells", "Spell Descriptions", "Fireball"], 131, EntityType.SPELL),
        (
            "Grappled [Condition]",
            ["Rules Glossary", "Rules Definitions", "Grappled [Condition]"],
            182,
            EntityType.GLOSSARY,
        ),
        ("Assassin", ["Monsters A–Z", "Assassin"], 260, EntityType.MONSTER),
        ("Opportunity Attacks", None, 15, EntityType.RULE),
    ],
)
def test_known_sections_have_provenance(by_title, chunks, title, path, page, entity):
    matches = [c for c in chunks if c.title == title and c.page_start == page]
    assert matches, f"{title} not found on p.{page}"
    chunk = matches[0]
    assert chunk.entity_type == entity
    if path:
        assert chunk.heading_path == path


def test_summoned_stat_block_does_not_parent_later_spells(by_title):
    assert by_title["Animated Object"].heading_path[-2] == "Animate Objects"
    assert "Otherworldly Steed" not in by_title["Fireball"].heading_path


def test_spell_text_content(by_title):
    text = by_title["Fireball"].text
    assert "Level 3 Evocation" in text and "8d6 Fire damage" in text


@pytest.mark.parametrize(
    ("query", "expected_title"),
    [
        ("How does the Ready action work", "Ready [Action]"),
        ("what happens when I grapple someone", "Grappling"),
        ("exhaustion levels penalty", "Exhaustion [Condition]"),
    ],
)
def test_bm25_top1_for_exact_rule_terms(chunks, query, expected_title):
    hits = LexicalIndex(chunks).search(query, k=3)
    assert hits[0].chunk.title == expected_title
