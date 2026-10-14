import random
from fractions import Fraction

import pytest

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
