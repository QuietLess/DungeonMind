"""Encounter budgets and difficulty checks under SRD 5.2.1 (2024) rules.

Source: SRD 5.2.1, Gameplay Toolbox › Combat Encounters, pp. 202–203, and
Monsters › Parts of a Stat Block › Experience Points, p. 256.

The 2024 method has no multiplier for the number of monsters: the budget is
the per-character XP for the chosen difficulty times the number of
characters, and each creature's XP is spent from it. The SRD's
troubleshooting advice becomes deterministic warnings here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction

from pydantic import BaseModel, ConfigDict, Field


class Difficulty(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


# XP Budget per Character (SRD 5.2.1 p. 202): level → (low, moderate, high)
XP_BUDGET_PER_CHARACTER: dict[int, tuple[int, int, int]] = {
    1: (50, 75, 100),
    2: (100, 150, 200),
    3: (150, 225, 400),
    4: (250, 375, 500),
    5: (500, 750, 1_100),
    6: (600, 1_000, 1_400),
    7: (750, 1_300, 1_700),
    8: (1_000, 1_700, 2_100),
    9: (1_300, 2_000, 2_600),
    10: (1_600, 2_300, 3_100),
    11: (1_900, 2_900, 4_100),
    12: (2_200, 3_700, 4_700),
    13: (2_600, 4_200, 5_400),
    14: (2_900, 4_900, 6_200),
    15: (3_300, 5_400, 7_800),
    16: (3_800, 6_100, 9_800),
    17: (4_500, 7_200, 11_700),
    18: (5_000, 8_700, 14_200),
    19: (5_500, 10_700, 17_200),
    20: (6_400, 13_200, 22_000),
}

# Experience Points by Challenge Rating (SRD 5.2.1 p. 256). CR 0 is "0 or 10";
# 10 is used here, and a creature's own stat-block XP always takes precedence.
XP_BY_CR: dict[Fraction, int] = {
    Fraction(0): 10,
    Fraction(1, 8): 25,
    Fraction(1, 4): 50,
    Fraction(1, 2): 100,
    **{
        Fraction(cr): xp
        for cr, xp in {
            1: 200, 2: 450, 3: 700, 4: 1_100, 5: 1_800, 6: 2_300, 7: 2_900, 8: 3_900,
            9: 5_000, 10: 5_900, 11: 7_200, 12: 8_400, 13: 10_000, 14: 11_500,
            15: 13_000, 16: 15_000, 17: 18_000, 18: 20_000, 19: 22_000, 20: 25_000,
            21: 33_000, 22: 41_000, 23: 50_000, 24: 62_000, 25: 75_000, 26: 90_000,
            27: 105_000, 28: 120_000, 29: 135_000, 30: 155_000,
        }.items()
    },
}  # fmt: skip


def parse_cr(value: str | float | int) -> Fraction:
    """Accept 5, 0.25, "1/4", "0.125"; reject values that aren't a real CR."""
    try:
        cr = Fraction(str(value).strip()).limit_denominator(8)
    except (ValueError, ZeroDivisionError) as e:
        raise ValueError(f"not a challenge rating: {value!r}") from e
    if cr not in XP_BY_CR:
        raise ValueError(f"no such challenge rating: {value!r}")
    return cr


def format_cr(cr: Fraction | float) -> str:
    cr = Fraction(cr).limit_denominator(8)
    return str(cr.numerator) if cr.denominator == 1 else f"{cr.numerator}/{cr.denominator}"


def xp_for_cr(value: str | float | int) -> int:
    return XP_BY_CR[parse_cr(value)]


class EncounterMonster(BaseModel):
    """One line of an encounter: a creature and how many of it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Creature name, e.g. 'Skeleton'")
    cr: str = Field(description="Challenge rating, e.g. '1/4' or '5'")
    count: int = Field(default=1, ge=1, le=50)
    xp: int | None = Field(
        default=None, ge=0, description="XP from the stat block; defaults to the CR table value"
    )


class EncounterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    party_levels: list[int] = Field(
        min_length=1, max_length=12, description="One entry per character, e.g. [5, 5, 5, 5]"
    )
    monsters: list[EncounterMonster] = Field(default_factory=list)
    target: Difficulty | None = Field(
        default=None, description="Desired difficulty, to report remaining budget"
    )


@dataclass
class EncounterReport:
    party_levels: list[int]
    budgets: dict[str, int]  # difficulty → total XP budget for this party
    total_xp: int
    rating: str  # "none" | "low" | "moderate" | "high" | "above high"
    target: str | None = None
    remaining: int | None = None  # budget left for the target difficulty (negative = over)
    warnings: list[str] = field(default_factory=list)
    source: str = "SRD 5.2.1, Gameplay Toolbox › Combat Encounters, pp. 202–203"


def party_budgets(party_levels: list[int]) -> dict[str, int]:
    """Total XP budget per difficulty. Mixed-level parties sum each character's row."""
    for level in party_levels:
        if level not in XP_BUDGET_PER_CHARACTER:
            raise ValueError(f"character level must be 1–20, got {level}")
    return {
        d.value: sum(XP_BUDGET_PER_CHARACTER[lvl][i] for lvl in party_levels)
        for i, d in enumerate(Difficulty)
    }


def evaluate(spec: EncounterInput) -> EncounterReport:
    budgets = party_budgets(spec.party_levels)
    party_size = len(spec.party_levels)
    max_level = max(spec.party_levels)

    total = 0
    creatures = 0
    warnings: list[str] = []
    for m in spec.monsters:
        cr = parse_cr(m.cr)
        xp = m.xp if m.xp is not None else XP_BY_CR[cr]
        total += xp * m.count
        creatures += m.count
        if cr > max_level:
            warnings.append(
                f"{m.name} (CR {format_cr(cr)}) is above the party's level and might take "
                "out a character with a single action (SRD p. 203, Powerful Creatures)."
            )
        if cr == 0:
            warnings.append(f"{m.name} is CR 0; use these sparingly, or as a swarm (SRD p. 203).")

    if creatures > 2 * party_size:
        warnings.append(
            f"{creatures} creatures for {party_size} characters is more than two per character; "
            "include fragile creatures that go down quickly (SRD p. 203, Many Creatures)."
        )
    if len(spec.monsters) > 3:
        warnings.append(
            f"{len(spec.monsters)} different stat blocks can be daunting to run; two or three "
            "is easier (SRD p. 203, Number of Stat Blocks)."
        )

    if total == 0:
        rating = "none"
    elif total <= budgets["low"]:
        rating = "low"  # the SRD defines nothing easier than Low
    elif total <= budgets["moderate"]:
        rating = "moderate"
    elif total <= budgets["high"]:
        rating = "high"
    else:
        rating = "above high"
        warnings.append(
            f"{total:,} XP exceeds the High budget of {budgets['high']:,}; the SRD gives no "
            "difficulty above High, so treat this as potentially lethal."
        )

    remaining = budgets[spec.target.value] - total if spec.target else None
    return EncounterReport(
        party_levels=spec.party_levels,
        budgets=budgets,
        total_xp=total,
        rating=rating,
        target=spec.target.value if spec.target else None,
        remaining=remaining,
        warnings=warnings,
    )
