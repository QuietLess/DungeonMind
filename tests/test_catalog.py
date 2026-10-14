"""Structured lookup tools, run against a small offline fixture of real SRD records."""

import json
from pathlib import Path

import pytest

from dungeonmind.tools.catalog import (
    Catalog,
    EntityNotFound,
    EntityQuery,
    MonsterQuery,
    SpellQuery,
    ToolInputError,
)
from dungeonmind.tools.encounter import XP_BY_CR, parse_cr
from dungeonmind.tools.open5e import snapshot_path

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT_DIR = Path(__file__).parents[1] / "data/processed"


@pytest.fixture(scope="module")
def cat():
    return Catalog.load(FIXTURES)


def names(results):
    return [r.name for r in results]


def test_level_3_fire_spells_is_exactly_fireball(cat):
    # BM25 ranked Flaming Sphere (level 2) first for this query; filters can't make that mistake.
    assert names(cat.search_spells(SpellQuery(level=3, damage_type="fire"))) == ["Fireball"]


def test_spell_filters_combine(cat):
    cantrips = cat.search_spells(SpellQuery(level=0, spell_class="wizard", damage_type="fire"))
    assert names(cantrips) == ["Fire Bolt"]
    assert "Detect Magic" in names(cat.search_spells(SpellQuery(ritual=True)))
    assert names(cat.search_spells(SpellQuery(casting_time="reaction"))) == ["Counterspell"]
    assert "Flaming Sphere" in names(cat.search_spells(SpellQuery(concentration=True)))


def test_spell_filter_values_are_forgiving_but_validated(cat):
    assert cat.search_spells(SpellQuery(spell_class="Wizards", level=3))
    with pytest.raises(ToolInputError, match="Valid: abjuration"):
        cat.search_spells(SpellQuery(school="pyromancy"))
    with pytest.raises(ToolInputError, match="min_level"):
        cat.search_spells(SpellQuery(min_level=5, max_level=2))


def test_undead_without_flyers_for_the_blueprint_demo(cat):
    # "hard ruined-temple encounter with undead, but no flying creatures"
    found = names(cat.search_monsters(MonsterQuery(creature_type="undead", can_fly=False)))
    assert found == ["Skeleton", "Zombie", "Wight", "Vampire Spawn"]  # by CR; Ghost excluded
    flyers = names(cat.search_monsters(MonsterQuery(creature_type="undead", can_fly=True)))
    assert flyers == ["Ghost"]


def test_cr_range_and_bad_cr(cat):
    found = cat.search_monsters(MonsterQuery(min_cr="1/4", max_cr="3"))
    assert found and all(parse_cr("1/4") <= m.cr <= 3 for m in found)
    with pytest.raises(ToolInputError, match="challenge rating"):
        cat.search_monsters(MonsterQuery(min_cr="3/7"))
    with pytest.raises(ToolInputError, match="greater than"):
        cat.search_monsters(MonsterQuery(min_cr="5", max_cr="1"))


def test_2024_goblins_are_fey(cat):
    assert names(cat.search_monsters(MonsterQuery(creature_type="fey"))) == ["Goblin Warrior"]


def test_get_entity_exact_and_case_insensitive(cat):
    detail = cat.get_entity(EntityQuery(kind="spell", name="fireball")).detail()
    assert detail["level"] == 3 and "8d6" in detail["description"]
    monster = cat.get_entity(EntityQuery(kind="monster", name="Adult Red Dragon")).detail()
    assert monster["cr"] == "17" and monster["actions"]


@pytest.mark.parametrize(
    ("kind", "name", "suggestion"),
    [
        ("monster", "red dragon", "Adult Red Dragon"),
        ("spell", "Fireblal", "Fireball"),
        ("monster", "skeletn", "Skeleton"),
    ],
)
def test_nonexistent_entities_get_suggestions(cat, kind, name, suggestion):
    with pytest.raises(EntityNotFound) as exc:
        cat.get_entity(EntityQuery(kind=kind, name=name))
    assert exc.value.suggestions[0] == suggestion


def test_out_of_srd_monster_has_no_fake_match(cat):
    with pytest.raises(EntityNotFound, match="Beholder"):
        cat.get_entity(EntityQuery(kind="monster", name="Beholder"))


@pytest.mark.skipif(
    not snapshot_path("creatures", SNAPSHOT_DIR).exists(), reason="run `dungeonmind sync-open5e`"
)
def test_cr_xp_table_agrees_with_every_srd_stat_block():
    """Cross-check the transcribed SRD p. 256 table against all 331 creatures."""
    creatures = json.loads(snapshot_path("creatures", SNAPSHOT_DIR).read_text(encoding="utf-8"))
    mismatches = [
        (c["name"], c["challenge_rating"], c["experience_points"])
        for c in creatures
        if c["challenge_rating"] > 0
        and XP_BY_CR[parse_cr(c["challenge_rating"])] != c["experience_points"]
    ]
    assert mismatches == []
