"""Character sheets: the model (M10 contract S1) and 2024-rules derived values (S2).

Everything a player types is validated here with limits; everything that can be computed is
computed by `derive`, so the browser, the dice and the chatbot all read the same numbers.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dungeonmind.tools.dice import DiceError, parse

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
ABILITY_NAMES = {
    "str": "Strength",
    "dex": "Dexterity",
    "con": "Constitution",
    "int": "Intelligence",
    "wis": "Wisdom",
    "cha": "Charisma",
}
SKILLS: dict[str, str] = {  # 2024 skills and their abilities
    "acrobatics": "dex",
    "animal_handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight_of_hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}
CONDITIONS = (
    "blinded",
    "charmed",
    "deafened",
    "frightened",
    "grappled",
    "incapacitated",
    "invisible",
    "paralyzed",
    "petrified",
    "poisoned",
    "prone",
    "restrained",
    "stunned",
    "unconscious",
)
HIT_DICE = (6, 8, 10, 12)
MAX_LEVEL = 20
COINS_PER_POUND = 50

Ability = Literal["str", "dex", "con", "int", "wis", "cha"]
Proficiency = Literal["none", "proficient", "expertise"]
Text = Annotated[str, Field(max_length=60)]
Bonus = Annotated[int, Field(ge=-20, le=20)]
Score = Annotated[int, Field(ge=1, le=30)]  # an alias: Abilities' fields shadow `int`/`str`


def _dice(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    try:
        parse(value)
    except DiceError as e:
        raise ValueError(f"not a dice expression: {e}") from None
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClassLevel(_Model):
    name: Text
    subclass: Text = ""
    level: Annotated[int, Field(ge=1, le=MAX_LEVEL)] = 1
    hit_die: int = 8

    @field_validator("hit_die")
    @classmethod
    def _hit_die(cls, v: int) -> int:
        if v not in HIT_DICE:
            raise ValueError("hit die must be 6, 8, 10 or 12")
        return v


class Abilities(_Model):
    str: Score = 10
    dex: Score = 10
    con: Score = 10
    int: Score = 10
    wis: Score = 10
    cha: Score = 10


class HitPoints(_Model):
    max: Annotated[int, Field(ge=0, le=2000)] = 0
    current: Annotated[int, Field(ge=0, le=2000)] = 0
    temp: Annotated[int, Field(ge=0, le=2000)] = 0


class DeathSaves(_Model):
    successes: Annotated[int, Field(ge=0, le=3)] = 0
    failures: Annotated[int, Field(ge=0, le=3)] = 0


class Attack(_Model):
    name: Text
    ability: Ability | None = None
    proficient: bool = False
    to_hit_misc: Bonus = 0
    damage: Annotated[str, Field(max_length=40)] = ""
    add_ability_to_damage: bool = False
    damage_type: Annotated[str, Field(max_length=20)] = ""
    notes: Annotated[str, Field(max_length=300)] = ""

    _check_damage = field_validator("damage")(classmethod(lambda cls, v: _dice(v)))


class Slot(_Model):
    max: Annotated[int, Field(ge=0, le=9)] = 0
    used: Annotated[int, Field(ge=0, le=9)] = 0

    @model_validator(mode="after")
    def _used(self) -> Slot:
        if self.used > self.max:
            raise ValueError("used slots can't exceed the maximum")
        return self


class Spell(_Model):
    name: Text
    level: Annotated[int, Field(ge=0, le=9)] = 0
    prepared: bool = False
    notes: Annotated[str, Field(max_length=300)] = ""


class Spellcasting(_Model):
    ability: Ability | None = None
    slots: Annotated[list[Slot], Field(max_length=9)] = []


class Item(_Model):
    name: Text
    quantity: Annotated[int, Field(ge=0, le=9999)] = 1
    weight: Annotated[float, Field(ge=0, le=1000)] = 0
    equipped: bool = False
    notes: Annotated[str, Field(max_length=300)] = ""


class Currency(_Model):
    cp: Annotated[int, Field(ge=0, le=10_000_000)] = 0
    sp: Annotated[int, Field(ge=0, le=10_000_000)] = 0
    ep: Annotated[int, Field(ge=0, le=10_000_000)] = 0
    gp: Annotated[int, Field(ge=0, le=10_000_000)] = 0
    pp: Annotated[int, Field(ge=0, le=10_000_000)] = 0


class Feature(_Model):
    name: Annotated[str, Field(max_length=80)]
    source: Text = ""
    text: Annotated[str, Field(max_length=2000)] = ""


class CustomDie(_Model):
    name: Annotated[str, Field(min_length=1, max_length=40)]
    expression: Annotated[str, Field(min_length=1, max_length=40)]
    notes: Annotated[str, Field(max_length=200)] = ""

    _check_expression = field_validator("expression")(classmethod(lambda cls, v: _dice(v)))


class Sheet(_Model):
    classes: Annotated[list[ClassLevel], Field(max_length=6)] = []
    species: Text = ""
    background: Text = ""
    alignment: Annotated[str, Field(max_length=40)] = ""
    xp: Annotated[int, Field(ge=0, le=10_000_000)] = 0
    abilities: Abilities = Abilities()
    saves: Annotated[list[Ability], Field(max_length=6)] = []
    save_misc: dict[Ability, Bonus] = {}
    skills: dict[str, Proficiency] = {}
    skill_misc: dict[str, Bonus] = {}
    jack_of_all_trades: bool = False
    armor_class: Annotated[int, Field(ge=0, le=50)] = 10
    speed: Annotated[int, Field(ge=0, le=300)] = 30
    initiative_misc: Bonus = 0
    hp: HitPoints = HitPoints()
    hit_dice_used: dict[str, Annotated[int, Field(ge=0, le=MAX_LEVEL)]] = {}
    death_saves: DeathSaves = DeathSaves()
    inspiration: bool = False
    exhaustion: Annotated[int, Field(ge=0, le=6)] = 0
    conditions: Annotated[list[str], Field(max_length=len(CONDITIONS))] = []
    attacks: Annotated[list[Attack], Field(max_length=30)] = []
    spellcasting: Spellcasting = Spellcasting()
    spells: Annotated[list[Spell], Field(max_length=150)] = []
    inventory: Annotated[list[Item], Field(max_length=150)] = []
    currency: Currency = Currency()
    features: Annotated[list[Feature], Field(max_length=60)] = []
    proficiencies: Annotated[str, Field(max_length=2000)] = ""
    custom_dice: Annotated[list[CustomDie], Field(max_length=30)] = []
    notes: Annotated[str, Field(max_length=5000)] = ""

    @field_validator("skills", "skill_misc")
    @classmethod
    def _known_skills(cls, v: dict[str, Any]) -> dict[str, Any]:
        unknown = set(v) - set(SKILLS)
        if unknown:
            raise ValueError(f"unknown skills: {', '.join(sorted(unknown))}")
        return v

    @field_validator("conditions")
    @classmethod
    def _known_conditions(cls, v: list[str]) -> list[str]:
        unknown = set(v) - set(CONDITIONS)
        if unknown:
            raise ValueError(f"unknown conditions: {', '.join(sorted(unknown))}")
        return list(dict.fromkeys(v))

    @field_validator("hit_dice_used")
    @classmethod
    def _known_dice(cls, v: dict[str, int]) -> dict[str, int]:
        if any(k not in {str(d) for d in HIT_DICE} for k in v):
            raise ValueError("hit dice are 6, 8, 10 or 12")
        return v

    @model_validator(mode="after")
    def _total_level(self) -> Sheet:
        if sum(c.level for c in self.classes) > MAX_LEVEL:
            raise ValueError(f"total level can't exceed {MAX_LEVEL}")
        return self


def modifier(score: int) -> int:
    return (score - 10) // 2


def level_of(sheet: Sheet) -> int:
    return max(1, sum(c.level for c in sheet.classes))


def proficiency_bonus(level: int) -> int:
    return 2 + (max(1, level) - 1) // 4


def derive(sheet: Sheet) -> dict[str, Any]:
    """S2: every number the sheet implies under the 2024 rules."""
    level = level_of(sheet)
    pb = proficiency_bonus(level)
    mods = {a: modifier(getattr(sheet.abilities, a)) for a in ABILITIES}
    half = pb // 2 if sheet.jack_of_all_trades else 0
    saves = {
        a: mods[a] + (pb if a in sheet.saves else 0) + sheet.save_misc.get(a, 0) for a in ABILITIES
    }
    skills = {}
    for skill, ability in SKILLS.items():
        level_of_skill = sheet.skills.get(skill, "none")
        bonus = {"none": half, "proficient": pb, "expertise": 2 * pb}[level_of_skill]
        skills[skill] = mods[ability] + bonus + sheet.skill_misc.get(skill, 0)
    attacks = []
    for a in sheet.attacks:
        mod = mods[a.ability] if a.ability else 0
        damage = a.damage
        if damage and a.add_ability_to_damage and mod:
            damage = f"{damage}{mod:+d}"
        attacks.append(
            {"to_hit": mod + (pb if a.proficient else 0) + a.to_hit_misc, "damage": damage}
        )
    spell_mod = mods[sheet.spellcasting.ability] if sheet.spellcasting.ability else None
    hit_dice: dict[str, int] = {}
    for c in sheet.classes:
        hit_dice[str(c.hit_die)] = hit_dice.get(str(c.hit_die), 0) + c.level
    coins = sum(sheet.currency.model_dump().values())
    items = sum(i.quantity * i.weight for i in sheet.inventory)
    weight = round(items + coins / COINS_PER_POUND, 2)
    return {
        "level": level,
        "proficiency_bonus": pb,
        "modifiers": mods,
        "saves": saves,
        "skills": skills,
        "passive_perception": 10 + skills["perception"],
        # 2024: Jack of All Trades applies to skill checks only; initiative is a plain DEX check.
        "initiative": mods["dex"] + sheet.initiative_misc,
        "attacks": attacks,
        "spell_save_dc": 8 + pb + spell_mod if spell_mod is not None else None,
        "spell_attack": pb + spell_mod if spell_mod is not None else None,
        "hit_dice": hit_dice,
        "carried_weight": int(weight) if weight == int(weight) else weight,
        "carrying_capacity": sheet.abilities.str * 15,
        "d20_penalty": -2 * sheet.exhaustion,
        "speed": max(0, sheet.speed - 5 * sheet.exhaustion),
    }


def _signed(n: int) -> str:
    return f"{n:+d}"


def summary_text(name: str, sheet: Sheet, d: dict[str, Any]) -> str:
    """M10d C1: the sheet as plain text for search and the chatbot (derived numbers included)."""
    classes = " / ".join(
        f"{c.name}{f' ({c.subclass})' if c.subclass else ''} {c.level}" for c in sheet.classes
    )
    who = ", ".join(
        x for x in (classes or f"level {d['level']}", sheet.species, sheet.background) if x
    )
    lines = [
        f"{name}: {who}. Proficiency bonus {_signed(d['proficiency_bonus'])}.",
        f"AC {sheet.armor_class}, HP {sheet.hp.current}/{sheet.hp.max}"
        + (f" (+{sheet.hp.temp} temp)" if sheet.hp.temp else "")
        + f", speed {d['speed']} ft, initiative {_signed(d['initiative'])}, "
        f"passive Perception {d['passive_perception']}.",
        "Abilities: "
        + ", ".join(
            f"{ABILITY_NAMES[a]} {getattr(sheet.abilities, a)} ({_signed(d['modifiers'][a])})"
            for a in ABILITIES
        )
        + ".",
        "Saving throws: "
        + ", ".join(f"{ABILITY_NAMES[a]} {_signed(d['saves'][a])}" for a in ABILITIES)
        + ".",
        "Skills: "
        + ", ".join(
            f"{k.replace('_', ' ').title().replace(' Of ', ' of ')} {_signed(v)}"
            for k, v in d["skills"].items()
        )
        + ".",
    ]
    if sheet.exhaustion or sheet.conditions or sheet.inspiration:
        state = [f"exhaustion {sheet.exhaustion}"] if sheet.exhaustion else []
        state += sheet.conditions + (["Heroic Inspiration"] if sheet.inspiration else [])
        lines.append("Current state: " + ", ".join(state) + ".")
    if sheet.attacks:
        lines.append(
            "Attacks: "
            + "; ".join(
                f"{a.name} {_signed(d['attacks'][i]['to_hit'])} to hit"
                + (f", {d['attacks'][i]['damage']} {a.damage_type}".rstrip() if a.damage else "")
                for i, a in enumerate(sheet.attacks)
            )
            + "."
        )
    if d["spell_save_dc"] is not None:
        lines.append(
            f"Spellcasting: save DC {d['spell_save_dc']}, spell attack "
            f"{_signed(d['spell_attack'])}."
        )
    slots = [
        f"level {i + 1}: {s.max - s.used}/{s.max}"
        for i, s in enumerate(sheet.spellcasting.slots)
        if s.max
    ]
    if slots:
        lines.append("Spell slots left: " + ", ".join(slots) + ".")
    if sheet.spells:
        lines.append(
            "Spells: "
            + ", ".join(
                f"{s.name} ({'cantrip' if s.level == 0 else f'level {s.level}'}"
                f"{', prepared' if s.prepared else ''})"
                for s in sheet.spells
            )
            + "."
        )
    if sheet.inventory:
        lines.append(
            "Inventory: "
            + ", ".join(
                f"{i.name}{f' ×{i.quantity}' if i.quantity != 1 else ''}"
                f"{' (equipped)' if i.equipped else ''}"
                for i in sheet.inventory
            )
            + f". Carrying {d['carried_weight']} of {d['carrying_capacity']} lb."
        )
    coins = {k: v for k, v in sheet.currency.model_dump().items() if v}
    if coins:
        lines.append("Money: " + ", ".join(f"{v} {k}" for k, v in coins.items()) + ".")
    for f in sheet.features:
        lines.append(
            f"Feature — {f.name}{f' ({f.source})' if f.source else ''}: {f.text}".rstrip(": ")
        )
    if sheet.custom_dice:
        lines.append(
            "Custom dice: " + ", ".join(f"{c.name} {c.expression}" for c in sheet.custom_dice) + "."
        )
    if sheet.proficiencies:
        lines.append(f"Proficiencies and languages: {sheet.proficiencies}")
    return "\n\n".join(lines)[:10_000]
