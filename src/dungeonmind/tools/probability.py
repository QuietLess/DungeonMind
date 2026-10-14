"""Exact D20 Test probabilities under SRD 5.2.1 (2024) rules.

Rules encoded here (SRD 5.2.1, Playing the Game › D20 Tests):

* Attack rolls: a natural 20 always hits (Critical Hit), a natural 1 always misses.
* Saving throws and ability checks: no automatic success or failure on 20/1.
* Advantage and Disadvantage cancel out; multiple sources don't stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from dungeonmind.tools import dice


class RollMode(StrEnum):
    NORMAL = "normal"
    ADVANTAGE = "advantage"
    DISADVANTAGE = "disadvantage"

    @classmethod
    def resolve(cls, advantage: bool = False, disadvantage: bool = False) -> RollMode:
        if advantage and not disadvantage:
            return cls.ADVANTAGE
        if disadvantage and not advantage:
            return cls.DISADVANTAGE
        return cls.NORMAL


def _with_mode(p: Fraction, mode: RollMode) -> Fraction:
    """Probability that a per-die event of probability p occurs on the d20 actually used."""
    if mode is RollMode.ADVANTAGE:
        return 1 - (1 - p) ** 2
    if mode is RollMode.DISADVANTAGE:
        return p**2
    return p


@dataclass(frozen=True)
class AttackOdds:
    hit: Fraction  # includes crits
    crit: Fraction
    mode: RollMode
    min_roll_to_hit: int  # natural d20 face needed (after nat-1/nat-20 rules)
    expected_damage: Fraction | None = None

    def as_dict(self) -> dict:
        out = {
            "hit": float(self.hit),
            "crit": float(self.crit),
            "miss": float(1 - self.hit),
            "mode": self.mode.value,
            "min_roll_to_hit": self.min_roll_to_hit,
            "hit_exact": str(self.hit),
        }
        if self.expected_damage is not None:
            out["expected_damage"] = round(float(self.expected_damage), 3)
        return out


def attack(
    attack_bonus: int,
    target_ac: int,
    mode: RollMode = RollMode.NORMAL,
    crit_range: int = 20,
    damage: str | None = None,
) -> AttackOdds:
    """Chance an attack roll hits / crits, optionally with expected damage.

    ``crit_range`` is the lowest natural roll that crits (19 for e.g. Improved Critical).
    ``damage`` is a dice expression; on a crit its dice are rolled twice (modifiers once).
    """
    if not 2 <= crit_range <= 20:
        raise ValueError("crit_range must be between 2 and 20")
    hit_faces = [
        face
        for face in range(1, 21)
        if face != 1 and (face >= crit_range or face + attack_bonus >= target_ac)
    ]
    p_hit_die = Fraction(len(hit_faces), 20)
    p_crit_die = Fraction(21 - crit_range, 20)
    hit = _with_mode(p_hit_die, mode)
    crit = _with_mode(p_crit_die, mode)

    expected = None
    if damage:
        expr = dice.parse(damage)
        crit_expr = dice.DiceExpression(
            dice=expr.dice + expr.dice, constant=expr.constant, source=f"crit({damage})"
        )
        # Damage can't go below 0 on a hit.
        normal = sum((max(k, 0) * p for k, p in dice.distribution(expr).items()), Fraction(0))
        critical = sum(
            (max(k, 0) * p for k, p in dice.distribution(crit_expr).items()), Fraction(0)
        )
        expected = (hit - crit) * normal + crit * critical

    return AttackOdds(
        hit=hit,
        crit=crit,
        mode=mode,
        min_roll_to_hit=min(hit_faces) if hit_faces else 21,
        expected_damage=expected,
    )


def check(bonus: int, dc: int, mode: RollMode = RollMode.NORMAL) -> Fraction:
    """Chance a saving throw or ability check meets the DC (no auto-success on 20)."""
    successes = sum(1 for face in range(1, 21) if face + bonus >= dc)
    return _with_mode(Fraction(successes, 20), mode)
