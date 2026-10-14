"""Deterministic encounter builder (blueprint §9). Contract: docs/ARCHITECTURE.md › M5 contract.

The model extracts an EncounterRequest and writes the result up; this module chooses the
monsters, so all XP arithmetic stays in code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dungeonmind.tools.catalog import Catalog, Monster
from dungeonmind.tools.encounter import Difficulty

Role = Literal["boss", "controller", "ranged", "frontline"]


class EncounterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    party_levels: list[int] = Field(
        min_length=1, max_length=12, description="One entry per character, e.g. [5, 5, 5, 5]"
    )
    difficulty: Difficulty = Difficulty.MODERATE
    creature_types: list[str] = Field(
        default_factory=list, description="Allowed creature types; empty means any"
    )
    exclude_flying: bool = False
    exclude_names: list[str] = Field(default_factory=list, max_length=20)
    max_stat_blocks: int = Field(default=3, ge=1, le=3)
    theme: str | None = Field(
        default=None, max_length=200, description="Flavour for the write-up; never a filter"
    )


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


def roles(monster: Monster) -> set[str]:
    raise NotImplementedError("M5: capability tags from the stat block")


def primary_role(monster: Monster) -> Role:
    raise NotImplementedError("M5: boss / controller / ranged / frontline")


def build_options(request: EncounterRequest, catalog: Catalog, max_options: int = 3) -> BuildResult:
    raise NotImplementedError("M5: deterministic option search")


def revise(request: EncounterRequest, direction: Literal["easier", "harder"]) -> EncounterRequest:
    raise NotImplementedError("M5: one difficulty step")
