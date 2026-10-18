import random
from pathlib import Path

import pytest
from isolated import run_isolated

from dungeonmind.tools.catalog import Catalog
from dungeonmind.tools.registry import ToolRegistry

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def reg():
    return ToolRegistry(Catalog.load(FIXTURES), rng=random.Random(0))


def test_every_tool_has_a_closed_object_schema(reg):
    for d in reg.definitions():
        schema = d["input_schema"]
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False, d["name"]
        assert len(d["description"]) > 40


def test_probability_tool_blueprint_demo(reg):
    out = reg.call(
        "probability", {"roll_type": "attack", "bonus": 7, "target": 18, "advantage": True}
    )
    assert out["result"]["hit_exact"] == "3/4"


def test_save_has_no_auto_success(reg):
    out = reg.call("probability", {"roll_type": "save_or_check", "bonus": 0, "target": 25})
    assert out["result"]["success"] == 0.0


@pytest.mark.parametrize(
    ("name", "args", "fragment"),
    [
        ("probability", {"roll_type": "attack", "bonus": 7}, "target: Field required"),
        ("probability", {"roll_type": "attack", "bonus": 7, "target": 15, "luck": 1}, "luck"),
        ("search_spells", {"school": "pyromancy"}, "Valid: abjuration"),
        ("search_monsters", {"min_cr": "3/7"}, "challenge rating"),
        ("dice_roll", {"expression": "1d20 7"}, "whitespace"),
        ("encounter_budget", {"party_levels": [25]}, "level must be 1"),
        ("teleport", {}, "Unknown tool"),
    ],
)
def test_bad_calls_return_readable_errors_instead_of_raising(reg, name, args, fragment):
    out = reg.call(name, args)
    assert "error" in out and fragment in out["error"], out


def test_dice_roll_is_audited(reg):
    result = reg.call("dice_roll", {"expression": "2d20kh1+5"})["result"]
    assert len(result["terms"][0]["rolls"]) == 2
    assert result["total"] == max(result["terms"][0]["rolls"]) + 5


# --- H1/H2 limits through the registry (docs/ARCHITECTURE.md › M4 hardening) -----------------


def _attack(damage: str) -> dict:
    return {"roll_type": "attack", "bonus": 5, "target": 15, "damage": damage}


@pytest.mark.parametrize("damage", ["10d100", "11d100"])
def test_h1_probability_with_oversized_damage_is_a_quick_tool_error(damage):
    # Crit doubling: 10d100 -> 20d100 (support 2,000), 11d100 -> 22d100 (2,200); cap is 1,000.
    out = run_isolated(
        f"""
        import random
        from pathlib import Path
        from dungeonmind.tools.catalog import Catalog
        from dungeonmind.tools.registry import ToolRegistry
        reg = ToolRegistry(Catalog.load(Path({str(FIXTURES)!r})), rng=random.Random(0))
        _started = time.perf_counter()
        _done(reg.call("probability", {_attack(damage)!r}))
        """,
        hard_timeout=15,
    )
    assert "error" in out["value"] and "result" not in out["value"], out
    assert "too large" in out["value"]["error"]
    assert out["elapsed"] < 1.0, out


@pytest.mark.parametrize("damage", ["8d6", "1d8+4", "4d6kh3", "2d20kh1+3", "5d100"])
def test_h1_probability_with_normal_damage_still_works(reg, damage):
    out = reg.call("probability", _attack(damage))
    assert out["result"]["expected_damage"] > 0, out


@pytest.mark.parametrize(
    ("name", "args", "field", "limit"),
    [
        ("dice_roll", {"expression": "1d4+" + "1" * 61}, "expression", "64"),
        ("probability", _attack("1d4+" + "1" * 61), "damage", "64"),
        ("search_spells", {"text": "a" * 201}, "text", "200"),
        ("search_monsters", {"text": "a" * 201}, "text", "200"),
        ("get_entity", {"kind": "monster", "name": "a" * 201}, "name", "200"),
        (
            "encounter_budget",
            {"party_levels": [5], "monsters": [{"name": "a" * 201, "cr": "1"}]},
            "name",
            "200",
        ),
        (
            "encounter_budget",
            {"party_levels": [5], "monsters": [{"name": "Goblin", "cr": "1/4"}] * 21},
            "monsters",
            "20",
        ),
    ],
)
def test_h2_oversized_inputs_get_readable_errors(reg, name, args, field, limit):
    out = reg.call(name, args)
    assert "result" not in out, out
    assert field in out["error"] and limit in out["error"], out


@pytest.mark.parametrize(
    ("name", "args"),
    [
        (
            "dice_roll",
            {"expression": "10d1000+10d1000+10d1000+10d1000+10d1000+10d1000+10d1000+1000+100"},
        ),  # 64 characters, constants ≤ 10 000
        ("search_spells", {"text": "a" * 200}),
        ("get_entity", {"kind": "spell", "name": "Fireball"}),
        (
            "encounter_budget",
            {"party_levels": [5], "monsters": [{"name": "Goblin", "cr": "1/4"}] * 20},
        ),
    ],
)
def test_h2_inputs_at_the_limit_are_accepted(reg, name, args):
    out = reg.call(name, args)
    assert "result" in out, out


def test_h2_unknown_entities_are_an_answer_not_an_error(reg):
    out = reg.call("get_entity", {"kind": "monster", "name": "Beholder"})
    assert "error" not in out
    assert out["result"]["found"] is False
    assert "No monster named 'Beholder'" in out["result"]["message"]
    assert isinstance(out["result"]["suggestions"], list)
