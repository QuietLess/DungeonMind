"""Server-side rolls for the game log (M10 contract R1).

The bonus always comes from the stored sheet's derived values; the request only names what to
roll. Dice are rolled by the dice tool, so every die is recorded.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any

from dungeonmind.hub.errors import Invalid
from dungeonmind.hub.sheets import ABILITIES, ABILITY_NAMES, SKILLS, Sheet
from dungeonmind.tools.dice import DiceError, DiceExpression, DiceTerm, parse, roll

D20_KINDS = ("check", "save", "skill", "initiative", "attack", "spell_attack", "death_save")
KINDS = (*D20_KINDS, "damage", "custom", "hit_die", "expression")
MODES = ("normal", "advantage", "disadvantage", "critical")


@dataclass
class Spec:
    label: str
    dice: DiceExpression  # dice only; constants go into `bonus`
    bonus: int
    d20: bool


def _skill_name(key: str) -> str:
    return key.replace("_", " ").title().replace(" Of ", " of ")


def _d20(mode: str) -> DiceExpression:
    if mode == "critical":
        raise Invalid("Only damage and custom dice can be critical.")
    term = {
        "normal": DiceTerm(1, 20),
        "advantage": DiceTerm(2, 20, keep=("kh", 1)),
        "disadvantage": DiceTerm(2, 20, keep=("kl", 1)),
    }[mode]
    return DiceExpression(dice=(term,), constant=0, source=str(term))


def _split(expression: str, critical: bool) -> tuple[DiceExpression, int]:
    """Dice terms (doubled on a critical) and the expression's own constant."""
    try:
        expr = parse(expression)
    except DiceError as e:
        raise Invalid(f"Not a dice expression: {e}") from None
    factor = 2 if critical else 1
    terms = tuple(  # a critical doubles the dice, and how many of them are kept
        DiceTerm(
            t.count * factor, t.sides, t.sign, (t.keep[0], t.keep[1] * factor) if t.keep else None
        )
        for t in expr.dice
    )
    if not terms:
        raise Invalid("A roll needs at least one die.")
    if sum(t.count for t in terms) > 100:
        raise Invalid("Too many dice.")
    return DiceExpression(dice=terms, constant=0, source=expression), expr.constant


def _index(key: str | None, items: list[Any], what: str) -> int:
    if key is None or not re.fullmatch(r"[0-9]{1,3}", key) or int(key) >= len(items):
        raise Invalid(f"No such {what}.")
    return int(key)


def spec_for(kind: str, key: str | None, mode: str, sheet: Sheet, derived: dict[str, Any]) -> Spec:
    """What to roll for a character, with the bonus taken from the sheet."""
    penalty = derived["d20_penalty"]
    if kind in ("check", "save"):
        if key not in ABILITIES:
            raise Invalid("Unknown ability.")
        base = derived["modifiers"][key] if kind == "check" else derived["saves"][key]
        return Spec(f"{ABILITY_NAMES[key]} {kind}", _d20(mode), base + penalty, True)
    if kind == "skill":
        if key not in SKILLS:
            raise Invalid("Unknown skill.")
        return Spec(_skill_name(key), _d20(mode), derived["skills"][key] + penalty, True)
    if kind == "initiative":
        return Spec("Initiative", _d20(mode), derived["initiative"] + penalty, True)
    if kind == "attack":
        i = _index(key, sheet.attacks, "attack")
        bonus = derived["attacks"][i]["to_hit"] + penalty
        return Spec(f"{sheet.attacks[i].name}: attack", _d20(mode), bonus, True)
    if kind == "spell_attack":
        if derived["spell_attack"] is None:
            raise Invalid("This sheet has no spellcasting ability.")
        return Spec("Spell attack", _d20(mode), derived["spell_attack"] + penalty, True)
    if kind == "death_save":
        return Spec("Death save", _d20(mode), penalty, True)
    critical = mode == "critical"
    if mode not in ("normal", "critical"):
        raise Invalid("Advantage and disadvantage are for d20 rolls.")
    if kind == "damage":
        i = _index(key, sheet.attacks, "attack")
        attack = sheet.attacks[i]
        if not attack.damage:
            raise Invalid("This attack has no damage dice.")
        dice, constant = _split(attack.damage, critical)
        mod = derived["modifiers"][attack.ability] if attack.ability else 0
        bonus = constant + (mod if attack.add_ability_to_damage else 0)
        label = f"{attack.name}: {'critical ' if critical else ''}damage"
        return Spec(label, dice, bonus, False)
    if kind == "custom":
        i = _index(key, sheet.custom_dice, "custom die")
        die = sheet.custom_dice[i]
        dice, constant = _split(die.expression, critical)
        return Spec(die.name + (" (critical)" if critical else ""), dice, constant, False)
    if kind == "hit_die":
        if key not in derived["hit_dice"]:
            raise Invalid("This character has no such hit die.")
        dice = DiceExpression(dice=(DiceTerm(1, int(key)),), constant=0, source=f"1d{key}")
        return Spec(f"Hit die (d{key})", dice, derived["modifiers"]["con"], False)
    raise Invalid("Unknown roll.")


def spec_for_expression(expression: str | None, mode: str) -> Spec:
    if not expression:
        raise Invalid("Give a dice expression, e.g. 2d6+3.")
    if mode not in ("normal", "critical"):
        raise Invalid("Advantage and disadvantage are for d20 rolls.")
    dice, constant = _split(expression, mode == "critical")
    return Spec(expression.replace(" ", ""), dice, constant, False)


def perform(spec: Spec, rng: random.Random | None = None) -> dict[str, Any]:
    """Rolls the dice and returns the stored detail (every die, the bonus and the total)."""
    result = roll(spec.dice, rng)
    total = result.total + spec.bonus
    detail: dict[str, Any] = {
        "terms": [
            {"term": t.term, "rolls": t.rolls, "kept": t.kept, "subtotal": t.subtotal}
            for t in result.terms
        ],
        "bonus": spec.bonus,
        "total": total,
        "expression": f"{result.expression}{spec.bonus:+d}" if spec.bonus else result.expression,
    }
    if spec.d20:
        natural = result.terms[0].kept[0]
        detail["natural"] = natural
        if spec.label == "Death save":
            if natural == 20:
                detail["outcome"] = "critical success"
            elif natural == 1:
                detail["outcome"] = "critical failure"
            else:
                detail["outcome"] = "success" if total >= 10 else "failure"
    return detail
