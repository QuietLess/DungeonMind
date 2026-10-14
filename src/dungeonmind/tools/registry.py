"""Allow-listed tools with typed inputs, in one place.

Each tool has a pydantic input model (which doubles as the JSON schema Claude
sees), a description written for the model, and a function that returns
JSON-serialisable output. `ToolRegistry.call` validates input and turns
expected failures into `{"error": ...}` results the model can read and retry,
so a bad argument never crashes the agent loop (M4).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dungeonmind.tools import dice, probability
from dungeonmind.tools.catalog import (
    Catalog,
    EntityNotFound,
    EntityQuery,
    MonsterQuery,
    SpellQuery,
    ToolInputError,
)
from dungeonmind.tools.encounter import EncounterInput, evaluate


class RollInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: str = Field(
        max_length=64, description="Dice expression, e.g. '1d20+7', '8d6', '4d6kh3'"
    )


class ProbabilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roll_type: Literal["attack", "save_or_check"] = Field(
        description="attack: nat 20 hits, nat 1 misses. save_or_check: no automatic results"
    )
    bonus: int = Field(ge=-10, le=30, description="Total modifier added to the d20")
    target: int = Field(ge=1, le=40, description="Target AC (attack) or DC (save/check)")
    advantage: bool = False
    disadvantage: bool = False
    crit_range: int = Field(default=20, ge=2, le=20, description="Lowest natural roll that crits")
    damage: str | None = Field(
        default=None,
        max_length=64,
        description="Optional damage dice for expected damage, e.g. '1d8+4'",
    )


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[Any], Any]

    def definition(self) -> dict[str, Any]:
        """The tool definition sent to the Claude API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


class ToolRegistry:
    def __init__(self, catalog: Catalog, rng: random.Random | None = None) -> None:
        self.catalog = catalog
        self.rng = rng
        self.tools = {t.name: t for t in self._specs()}

    def definitions(self) -> list[dict[str, Any]]:
        return [t.definition() for t in self.tools.values()]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run a tool by name. Expected failures come back as {"error": ...}."""
        spec = self.tools.get(name)
        if spec is None:
            return {"error": f"Unknown tool {name!r}. Available: {', '.join(self.tools)}."}
        try:
            parsed = spec.input_model.model_validate(arguments)
            return {"result": spec.run(parsed)}
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(map(str, err['loc'])) or 'input'}: {err['msg']}" for err in e.errors()
            )
            return {"error": f"Invalid arguments for {name}: {problems}"}
        except (ToolInputError, EntityNotFound, dice.DiceError, ValueError) as e:
            return {"error": str(e)}

    def _specs(self) -> list[ToolSpec]:
        cat = self.catalog
        return [
            ToolSpec(
                "search_spells",
                "Filter SRD 5.2.1 spells by level, school, class list, damage type, "
                "concentration, ritual, casting time or text. Use this instead of rules search "
                "for questions like 'level-3 fire spells' or 'wizard cantrips'.",
                SpellQuery,
                lambda q: [s.summary() for s in cat.search_spells(q)],
            ),
            ToolSpec(
                "search_monsters",
                "Filter SRD 5.2.1 creatures by challenge rating range, creature type, size, "
                "whether they fly, or text in their traits and actions. The SRD has no habitat "
                "data, so pick thematically fitting types yourself.",
                MonsterQuery,
                lambda q: [m.summary() for m in cat.search_monsters(q)],
            ),
            ToolSpec(
                "get_entity",
                "Full record for one spell or monster by exact name, including description, "
                "stats, traits and actions. Returns close-match suggestions if not found.",
                EntityQuery,
                lambda q: cat.get_entity(q).detail(),
            ),
            ToolSpec(
                "dice_roll",
                "Roll dice on the server and return every die rolled. Use this for any actual "
                "roll; never invent roll results.",
                RollInput,
                lambda q: asdict(dice.roll(q.expression, rng=self.rng)),
            ),
            ToolSpec(
                "probability",
                "Exact probability that a d20 attack roll, saving throw or ability check "
                "succeeds, with advantage/disadvantage, crit range and optional expected "
                "damage. Use this for any odds question; don't compute odds yourself.",
                ProbabilityInput,
                _probability,
            ),
            ToolSpec(
                "encounter_budget",
                "XP budgets for a party (Low/Moderate/High) under the SRD 5.2.1 rules, and, "
                "if monsters are given, the encounter's total XP, difficulty rating and the "
                "SRD's warnings (too many creatures, CR above party level, etc.).",
                EncounterInput,
                lambda q: asdict(evaluate(q)),
            ),
        ]


def _probability(q: ProbabilityInput) -> dict[str, Any]:
    mode = probability.RollMode.resolve(q.advantage, q.disadvantage)
    if q.roll_type == "attack":
        odds = probability.attack(q.bonus, q.target, mode, q.crit_range, q.damage)
        return {**odds.as_dict(), "rules": "SRD 5.2.1, Playing the Game › D20 Tests, pp. 6–8"}
    p = probability.check(q.bonus, q.target, mode)
    return {
        "success": float(p),
        "success_exact": str(p),
        "mode": mode.value,
        "rules": "SRD 5.2.1, Playing the Game › D20 Tests, pp. 6–8 (no auto-success on 20)",
    }
