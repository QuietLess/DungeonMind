import random
from fractions import Fraction

import pytest
from isolated import run_isolated

from dungeonmind.tools import dice


@pytest.mark.parametrize(
    ("expr", "normalized"),
    [
        ("1d20+7", "1d20+7"),
        ("d20", "1d20"),
        ("8D6", "8d6"),
        ("4d6kh3", "4d6kh3"),
        ("2d20kl1 + 5", "2d20kl1+5"),
        ("d%", "1d100"),
        ("2d6 - 1d4 + 3", "2d6-1d4+3"),
        ("5", "5"),
    ],
)
def test_parse_round_trips(expr, normalized):
    assert str(dice.parse(expr)) == normalized


@pytest.mark.parametrize(
    "bad", ["", "d", "1d1", "0d6", "101d6", "2d6kh3", "1d20++2", "1d20 7", "abc"]
)
def test_parse_rejects_malformed(bad):
    with pytest.raises(dice.DiceError):
        dice.parse(bad)


def test_roll_is_audited_and_within_bounds():
    result = dice.roll("4d6kh3+2", rng=random.Random(42))
    (term,) = result.terms
    assert len(term.rolls) == 4 and len(term.kept) == 3
    assert sorted(term.kept) == sorted(term.rolls)[1:]
    assert result.total == sum(term.kept) + 2


def test_distribution_sums_to_one_and_matches_known_values():
    dist = dice.distribution("2d6")
    assert sum(dist.values()) == 1
    assert dist[7] == Fraction(6, 36)
    assert dice.mean("8d6") == 28
    assert dice.mean("1d20+7") == Fraction(35, 2)


def test_keep_highest_matches_advantage():
    # 2d20kh1 >= 11 should equal 1 - (10/20)^2.
    assert dice.prob_at_least("2d20kh1", 11) == Fraction(3, 4)
    assert dice.mean("4d6kh3") == Fraction(15869, 1296)  # classic ability-score mean ≈ 12.24


# --- H1 dice limits (docs/ARCHITECTURE.md › M4 hardening) -----------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "1d4+" + "1" * 61,  # 65 characters, otherwise valid
        "1+1+1+1+1+1+1+1+1+1+1",  # 11 terms
        "1d6+1d6+1d6+1d6+1d6+1d6+1d6+1d6+1d6+1d6+1d6",  # 11 dice terms
        "60d6+50d6",  # 110 dice in total
        "100d4+1d4",  # 101 dice in total
    ],
)
def test_h1_parse_rejects_oversized_expressions(bad):
    with pytest.raises(dice.DiceError):
        dice.parse(bad)


@pytest.mark.parametrize(
    "ok",
    [
        "10d1000+10d1000+10d1000+10d1000+10d1000+10d1000+10d1000+1000+100",  # 64 chars
        "1d6+1d6+1d6+1d6+1d6+1d6+1d6+1d6+1d6+1",  # exactly 10 terms
        "50d6+50d6",  # exactly 100 dice
        "8d6",
        "4d6kh3",
        "2d20kh1",
    ],
)
def test_h1_parse_accepts_expressions_at_the_limits(ok):
    dice.parse(ok)


@pytest.mark.parametrize("big", ["1d4+10001", "1d4+" + "1" * 60, "99999d6", "1d999999"])
def test_m10_f3_huge_constants_and_numbers_are_refused(big):
    with pytest.raises(dice.DiceError):
        dice.parse(big)


@pytest.mark.parametrize(
    "big",
    [
        "11d100",  # support 1,100 > 1,000
        "30d100",  # support 3,000
        "100d1000",  # support 100,000
        "6d100+5d100",  # support 1,100 across terms
        "7d6kh3",  # 6**7 = 279,936 keep outcomes
        "5d20kh1",  # 20**5 = 3,200,000 keep outcomes
    ],
)
def test_h1_distribution_refuses_supports_too_large_for_exact_odds(big):
    out = run_isolated(
        f"""
        from dungeonmind.tools import dice
        try:
            dice.distribution({big!r})
            _done("computed")
        except dice.DiceError as e:
            _done("DiceError: " + str(e))
        """,
        hard_timeout=10,
    )
    assert out["value"].startswith("DiceError") and "too large" in out["value"], out
    assert out["elapsed"] < 1.0, out


def test_h1_distribution_still_works_for_normal_damage():
    assert sum(dice.distribution("8d6").values()) == 1
    assert sum(dice.distribution("5d100+5d100").values()) == 1  # support exactly 1,000
    assert sum(dice.distribution("6d6kh3").values()) == 1  # 46,656 outcomes: allowed
    assert dice.prob_at_least("2d20kh1", 11) == Fraction(3, 4)
    assert dice.mean("4d6kh3") == Fraction(15869, 1296)
