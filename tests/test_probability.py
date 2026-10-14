from fractions import Fraction

import pytest

from dungeonmind.tools.probability import RollMode, attack, check


def test_attack_hand_calculated_normal():
    # +7 vs AC 17 needs a 10+: faces 10..20 = 11/20.
    assert attack(7, 17).hit == Fraction(11, 20)


def test_attack_with_advantage_blueprint_demo():
    # "+7 attack hits AC 18 with advantage": p=10/20 per die -> 1 - (1/2)^2 = 3/4.
    assert attack(7, 18, RollMode.ADVANTAGE).hit == Fraction(3, 4)


def test_disadvantage_squares_single_die_probability():
    assert attack(7, 17, RollMode.DISADVANTAGE).hit == Fraction(11, 20) ** 2


def test_natural_20_always_hits_and_natural_1_always_misses():
    assert attack(0, 30).hit == Fraction(1, 20)  # only a nat 20
    assert attack(0, 30).min_roll_to_hit == 20
    assert attack(30, 5).hit == Fraction(19, 20)  # everything but a nat 1


def test_expanded_crit_range():
    odds = attack(5, 15, crit_range=19)
    assert odds.crit == Fraction(2, 20)
    assert attack(5, 15, RollMode.ADVANTAGE, crit_range=19).crit == 1 - Fraction(18, 20) ** 2


def test_expected_damage_doubles_dice_not_modifiers_on_crit():
    # 1d8+4 vs AC 10 with +10: hit on 2..20 (19/20), crit on 20 (1/20).
    odds = attack(10, 10, damage="1d8+4")
    normal, crit = Fraction(17, 2), Fraction(13)  # 4.5+4, 9+4
    assert odds.expected_damage == Fraction(18, 20) * normal + Fraction(1, 20) * crit


def test_saves_have_no_auto_success_or_failure():
    assert check(0, 25) == 0  # a natural 20 doesn't save against DC 25
    assert check(10, 5) == 1  # a natural 1 still succeeds with +10 vs DC 5
    assert check(3, 15) == Fraction(9, 20)  # needs 12+


@pytest.mark.parametrize(
    ("adv", "dis", "expected"),
    [
        (True, False, RollMode.ADVANTAGE),
        (False, True, RollMode.DISADVANTAGE),
        (True, True, RollMode.NORMAL),
        (False, False, RollMode.NORMAL),
    ],
)
def test_advantage_and_disadvantage_cancel(adv, dis, expected):
    assert RollMode.resolve(adv, dis) is expected
