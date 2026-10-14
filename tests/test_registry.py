import random
from pathlib import Path

import pytest

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
        ("get_entity", {"kind": "monster", "name": "Beholder"}, "No monster named"),
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
