"""Deterministic encounter builder (blueprint §9). Contract: docs/ARCHITECTURE.md › M5 contract.

The model extracts an EncounterRequest and writes the result up; this module chooses the
monsters, so all XP arithmetic stays in code.

Search, and why it is bounded: candidates are grouped into classes by (XP, primary role).
Monsters in one class are interchangeable for everything except the name tie-break, so the
search runs over multisets of at most 3 classes and only expands names at the end.

- At most MAX_CLASSES classes are kept (the highest-XP ones; low-XP classes matter only to
  low-level parties, which have few classes anyway). That caps the multisets at
  C(48 + 2, 3) ≈ 20k.
- Counts depend only on the multiset's XP values, so the best counts (most XP inside the band,
  then fewest creatures) are computed once per XP multiset, by a loop of at most
  (2 × party size)² ≤ 576 steps.
- Names are expanded only for the best-ranked multisets, with at most NAMES_PER_CLASS names
  per class and at most MAX_EXPANSIONS concrete options looked at in total.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dungeonmind.tools.catalog import CREATURE_TYPES, Catalog, Monster
from dungeonmind.tools.encounter import (
    Difficulty,
    EncounterInput,
    EncounterMonster,
    evaluate,
    format_cr,
    party_budgets,
)

Role = Literal["boss", "controller", "ranged", "frontline"]
ROLE_ORDER: tuple[Role, ...] = ("boss", "controller", "ranged", "frontline")

MAX_CLASSES = 48
NAMES_PER_CLASS = 4
MAX_EXPANSIONS = 5_000

CONDITIONS = (
    "Blinded Charmed Deafened Frightened Grappled Incapacitated Paralyzed Petrified Poisoned "
    "Prone Restrained Stunned Unconscious"
).split()
_CONDITION = re.compile(rf"\b(?:{'|'.join(CONDITIONS)})\b")
_STEPS = list(Difficulty)  # low, moderate, high


class EncounterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    party_levels: list[Annotated[int, Field(ge=1, le=20)]] = Field(
        min_length=1, max_length=12, description="One entry per character, e.g. [5, 5, 5, 5]"
    )
    difficulty: Difficulty = Difficulty.MODERATE
    creature_types: list[str] = Field(
        default_factory=list, description="Allowed creature types; empty means any"
    )
    exclude_flying: bool = False
    exclude_names: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=20
    )
    max_stat_blocks: int = Field(default=3, ge=1, le=3)
    theme: str | None = Field(
        default=None, max_length=200, description="Flavour for the write-up; never a filter"
    )

    @field_validator("creature_types")
    @classmethod
    def _known_types(cls, value: list[str]) -> list[str]:
        unknown = [t for t in value if t not in CREATURE_TYPES]
        if unknown:
            raise ValueError(
                f"unknown creature types {unknown}; valid: {', '.join(CREATURE_TYPES)}"
            )
        return value


@dataclass
class OptionMonster:
    name: str
    cr: str
    xp: int
    count: int
    role: Role
    citation: str


@dataclass
class EncounterOption:
    monsters: list[OptionMonster]
    total_xp: int
    rating: str
    warnings: list[str] = field(default_factory=list)
    roles: list[Role] = field(default_factory=list)


@dataclass
class BuildResult:
    options: list[EncounterOption]
    budget: int
    band: tuple[int, int]  # (exclusive low, inclusive high)
    note: str | None = None


# --- roles ------------------------------------------------------------------------------------


def _texts(monster: Monster) -> list[str]:
    r = monster.record
    return [t.get("desc", "") for t in r.get("traits", [])] + [
        a.get("desc", "") for a in r.get("actions", [])
    ]


def roles(monster: Monster) -> set[str]:
    """Capability tags from the stat block (a variety heuristic, not a rules claim)."""
    texts = _texts(monster)
    tags: set[str] = set()
    if any("Melee Attack Roll" in t for t in texts):
        tags.add("melee")
    if any("Ranged Attack Roll" in t for t in texts):
        tags.add("ranged")
    if any("Saving Throw" in t and _CONDITION.search(t) for t in texts):
        tags.add("controller")
    actions = monster.record.get("actions", [])
    if any(a.get("action_type") == "LEGENDARY_ACTION" for a in actions):
        tags.add("legendary")
    return tags


def primary_role(monster: Monster) -> Role:
    tags = roles(monster)
    if "legendary" in tags:
        return "boss"
    if "controller" in tags:
        return "controller"
    if "ranged" in tags and "melee" not in tags:
        return "ranged"
    return "frontline"


# --- revise -----------------------------------------------------------------------------------


def revise(request: EncounterRequest, direction: Literal["easier", "harder"]) -> EncounterRequest:
    """Move the difficulty one step; the SRD defines nothing outside Low–High."""
    i = _STEPS.index(request.difficulty) + (1 if direction == "harder" else -1)
    if i < 0:
        raise ValueError("Low is the easiest difficulty the SRD defines; it can't go easier.")
    if i >= len(_STEPS):
        raise ValueError("High is the hardest difficulty the SRD defines; it can't go harder.")
    return request.model_copy(update={"difficulty": _STEPS[i]}, deep=True)


# --- search -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Candidate:
    monster: Monster
    xp: int
    role: Role

    @property
    def sort_key(self) -> tuple[str, str]:
        return self.monster.name.lower(), self.monster.name


def _candidates(request: EncounterRequest, catalog: Catalog) -> list[_Candidate]:
    top = max(request.party_levels)
    excluded = {n.strip().lower() for n in request.exclude_names}
    out = []
    for m in catalog.monsters:
        if not 0 < m.cr <= top:
            continue
        if request.creature_types and m.record["type"]["key"] not in request.creature_types:
            continue
        if request.exclude_flying and m.fly_speed:
            continue
        if m.name.lower() in excluded:
            continue
        out.append(_Candidate(m, int(m.record["experience_points"]), primary_role(m)))
    return sorted(out, key=lambda c: c.sort_key)


def _best_counts(
    xps: tuple[int, ...], lo: int, hi: int, max_creatures: int
) -> tuple[int, int, tuple[int, ...]] | None:
    """Counts (aligned with ascending `xps`) giving the most XP in (lo, hi], then fewest creatures.

    The smallest XP value is the filler: for fixed counts of the others its best count is
    determined, so the loop is over at most two counts.
    """
    filler, *others = xps
    best: tuple[int, int, tuple[int, ...]] | None = None
    for counts, spent, used in _counts(others, hi - filler, max_creatures - 1):
        c0 = min(max_creatures - used, (hi - spent) // filler)
        total, n = spent + c0 * filler, used + c0
        if total > lo and (best is None or (total, -n) > (best[0], -best[1])):
            best = (total, n, (c0, *counts))
    return best


def _counts(others: list[int], budget: int, slots: int) -> list[tuple[tuple[int, ...], int, int]]:
    """(counts ≥ 1 per XP value, XP spent, creatures used) for the non-filler stat blocks.

    `budget` and `slots` already leave room (XP and a creature) for at least one filler.
    """
    if not others:
        return [((), 0, 0)]
    if len(others) == 1:
        (x,) = others
        return [((c,), c * x, c) for c in range(1, min(slots, budget // x) + 1)]
    x1, x2 = others
    return [
        ((c1, c2), c1 * x1 + c2 * x2, c1 + c2)
        for c1 in range(1, min(slots - 1, (budget - x2) // x1) + 1)
        for c2 in range(1, min(slots - c1, (budget - c1 * x1) // x2) + 1)
    ]


def _expand(
    combo: tuple[int, ...],
    classes: list[list[_Candidate]],
    counts: tuple[int, ...],
    used_tops: set[str],
) -> Iterator[list[tuple[_Candidate, int]]]:
    """Every name choice for a class multiset, each paired with its counts.

    In the highest-XP classes, names already used as an earlier option's highest-XP stat block
    are skipped before the NAMES_PER_CLASS cap, since variety (rule 4) would reject them anyway.
    """
    distinct = sorted(set(combo))
    top_xp = max(classes[c][0].xp for c in distinct)

    def names(c: int) -> list[_Candidate]:
        pool = classes[c]
        if pool[0].xp == top_xp:
            pool = [cand for cand in pool if cand.monster.name not in used_tops]
        return pool[:NAMES_PER_CLASS]

    choices = [itertools.combinations(names(c), combo.count(c)) for c in distinct]
    for picked in itertools.product(*choices):
        slots = sorted(
            (cand for group in picked for cand in group), key=lambda c: (c.xp, c.sort_key)
        )
        yield list(zip(slots, counts, strict=True))


def _option(request: EncounterRequest, chosen: list[tuple[_Candidate, int]]) -> EncounterOption:
    chosen = sorted(chosen, key=lambda pc: (-pc[0].xp, pc[0].sort_key))
    monsters = [
        OptionMonster(
            name=c.monster.name,
            cr=format_cr(c.monster.cr),
            xp=c.xp,
            count=n,
            role=c.role,
            citation=c.monster.citation,
        )
        for c, n in chosen
    ]
    report = evaluate(
        EncounterInput(
            party_levels=request.party_levels,
            monsters=[
                EncounterMonster(name=m.name, cr=m.cr, count=m.count, xp=m.xp) for m in monsters
            ],
        )
    )
    present = {m.role for m in monsters}
    return EncounterOption(
        monsters=monsters,
        total_xp=report.total_xp,
        rating=report.rating,
        warnings=report.warnings,
        roles=[r for r in ROLE_ORDER if r in present],
    )


def build_options(request: EncounterRequest, catalog: Catalog, max_options: int = 3) -> BuildResult:
    budgets = party_budgets(request.party_levels)
    step = _STEPS.index(request.difficulty)
    hi = budgets[request.difficulty.value]
    lo = budgets[_STEPS[step - 1].value] if step else 0
    result = BuildResult(options=[], budget=hi, band=(lo, hi))

    candidates = _candidates(request, catalog)
    if not candidates:
        result.note = (
            "No SRD creatures pass the filters (CR above 0 and at most the party's highest "
            "level, the requested creature types, flying and name exclusions)."
        )
        return result

    by_class: dict[tuple[int, Role], list[_Candidate]] = {}
    for c in candidates:
        if c.xp <= hi:
            by_class.setdefault((c.xp, c.role), []).append(c)
    keys = sorted(by_class, key=lambda k: (-k[0], ROLE_ORDER.index(k[1])))[:MAX_CLASSES]
    classes = [by_class[k] for k in keys]
    max_creatures = 2 * len(request.party_levels)

    best_cache: dict[tuple[int, ...], tuple[int, int, tuple[int, ...]] | None] = {}
    ranked: list[tuple[tuple[int, int, int], tuple[int, ...], tuple[int, ...]]] = []
    for size in range(1, request.max_stat_blocks + 1):
        for combo in itertools.combinations_with_replacement(range(len(classes)), size):
            if any(combo.count(c) > min(len(classes[c]), NAMES_PER_CLASS) for c in set(combo)):
                continue
            xps = tuple(sorted(keys[c][0] for c in combo))
            if xps not in best_cache:
                best_cache[xps] = _best_counts(xps, lo, hi, max_creatures)
            best = best_cache[xps]
            if best is None:
                continue
            total, n, counts = best
            n_roles = len({keys[c][1] for c in combo})
            ranked.append(((-n_roles, -total, n), combo, counts))
    ranked.sort(key=lambda r: r[0])

    # Greedy in ranking order (rule 5), skipping options that break variety (rule 4). Options
    # with equal (roles, XP, creatures) are compared by their sorted, lowercased names.
    chosen_sets: set[frozenset[str]] = set()
    chosen_tops: set[str] = set()
    expansions = 0
    for _, group in itertools.groupby(ranked, key=lambda r: r[0]):
        if len(result.options) >= max_options or expansions >= MAX_EXPANSIONS:
            break
        concrete = []
        for _, combo, counts in group:
            for picked in _expand(combo, classes, counts, chosen_tops):
                concrete.append(picked)
                expansions += 1
        concrete.sort(key=lambda p: sorted(c.monster.name.lower() for c, _ in p))
        for picked in concrete:
            names = frozenset(c.monster.name for c, _ in picked)
            top_xp = max(c.xp for c, _ in picked)
            tops = {c.monster.name for c, _ in picked if c.xp == top_xp}
            if names in chosen_sets or tops & chosen_tops:
                continue
            result.options.append(_option(request, picked))
            chosen_sets.add(names)
            chosen_tops |= tops
            if len(result.options) >= max_options:
                break

    if not result.options:
        result.note = (
            f"No combination of 1–{request.max_stat_blocks} stat blocks with at most "
            f"{max_creatures} creatures lands in the {request.difficulty.value} band "
            f"(more than {lo:,} and at most {hi:,} XP)."
        )
    return result
