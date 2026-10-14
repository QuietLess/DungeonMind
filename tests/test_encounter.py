from fractions import Fraction

import pytest

from dungeonmind.tools.encounter import (
    XP_BY_CR,
    Difficulty,
    EncounterInput,
    EncounterMonster,
    evaluate,
    format_cr,
    parse_cr,
    party_budgets,
)


@pytest.mark.parametrize(
    ("levels", "difficulty", "expected"),
    [
        # The three worked examples in SRD 5.2.1, Steps 2–3, p. 202.
        ([1] * 4, Difficulty.LOW, 200),
        ([3] * 5, Difficulty.MODERATE, 1_125),
        ([15] * 6, Difficulty.HIGH, 46_800),
    ],
)
def test_budgets_match_srd_examples(levels, difficulty, expected):
    assert party_budgets(levels)[difficulty.value] == expected


def test_mixed_level_party_sums_each_characters_row():
    assert party_budgets([4, 5])["moderate"] == 375 + 750


def test_srd_example_2_encounter_is_moderate():
    # 2 Druids (450 XP) + 9 Stirges (25 XP) for five level-3 characters = 1,125 XP.
    spec = EncounterInput(
        party_levels=[3] * 5,
        monsters=[
            EncounterMonster(name="Druid", cr="2", count=2),
            EncounterMonster(name="Stirge", cr="1/8", count=9),
        ],
        target=Difficulty.MODERATE,
    )
    report = evaluate(spec)
    assert report.total_xp == 1_125
    assert report.rating == "moderate"
    assert report.remaining == 0


def test_srd_example_3_two_adult_red_dragons_and_two_fire_giants_is_high():
    spec = EncounterInput(
        party_levels=[15] * 6,
        monsters=[
            EncounterMonster(name="Adult Red Dragon", cr="17", count=2),
            EncounterMonster(name="Fire Giant", cr="9", count=2),
        ],
    )
    report = evaluate(spec)
    assert report.total_xp == 46_000 and report.rating == "high"
    # CR 17 vs level 15: the SRD's own example still earns its "Powerful Creatures" caution.
    assert any("Adult Red Dragon (CR 17) is above" in w for w in report.warnings)


def test_stat_block_xp_overrides_cr_table():
    spec = EncounterInput(
        party_levels=[5], monsters=[EncounterMonster(name="Lair dragon", cr="17", xp=20_000)]
    )
    assert evaluate(spec).total_xp == 20_000


def test_warnings_follow_srd_troubleshooting():
    spec = EncounterInput(
        party_levels=[1, 1],
        monsters=[
            EncounterMonster(name="Ogre", cr="2"),
            EncounterMonster(name="Rat", cr="0", count=4),
            EncounterMonster(name="A", cr="1/8"),
            EncounterMonster(name="B", cr="1/8"),
        ],
    )
    warnings = " ".join(evaluate(spec).warnings)
    assert "above the party's level" in warnings  # Ogre CR 2 vs level 1
    assert "CR 0" in warnings
    assert "more than two per character" in warnings  # 7 creatures, 2 characters
    assert "stat blocks" in warnings  # 4 different stat blocks
    assert "exceeds the High budget" in warnings  # 450 + 40 + 50 > 200


def test_no_monsters_reports_budgets_only():
    report = evaluate(EncounterInput(party_levels=[5] * 4))
    assert report.rating == "none" and report.budgets["high"] == 4_400


@pytest.mark.parametrize("value", ["1/4", 0.25, "0.25"])
def test_parse_cr_accepts_fractions_and_decimals(value):
    assert parse_cr(value) == Fraction(1, 4)
    assert format_cr(parse_cr(value)) == "1/4"


@pytest.mark.parametrize("bad", ["3/7", "31", "-1", "abc", "1/3"])
def test_parse_cr_rejects_values_that_arent_crs(bad):
    with pytest.raises(ValueError):
        parse_cr(bad)


@pytest.mark.parametrize("level", [0, 21])
def test_levels_outside_1_to_20_rejected(level):
    with pytest.raises(ValueError):
        party_budgets([level])


def test_cr_table_is_monotonic():
    xps = [XP_BY_CR[cr] for cr in sorted(XP_BY_CR)]
    assert xps == sorted(xps)
