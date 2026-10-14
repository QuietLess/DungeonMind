"""Structured spell and monster lookup over the Open5e SRD 5.2 snapshot.

These are the `search_spells`, `search_monsters` and `get_entity` tools. Bad
filters raise `ToolInputError` with the valid options, and unknown names raise
`EntityNotFound` with close matches. Both messages are written for the model
to read and retry with, rather than for a stack trace.

Each record is linked to its SRD 5.2.1 PDF chunk where one exists, so a
structured result carries the same page citation as rules text.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dungeonmind.schemas import Chunk, EntityType
from dungeonmind.tools.encounter import format_cr, parse_cr
from dungeonmind.tools.open5e import snapshot_path


class ToolInputError(ValueError):
    """A filter value the tool can't use; the message lists valid options."""


class EntityNotFound(LookupError):
    def __init__(self, kind: str, name: str, suggestions: list[str]) -> None:
        self.suggestions = suggestions
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        super().__init__(f"No {kind} named {name!r} in SRD 5.2.1.{hint}")


SCHOOLS = (
    "abjuration conjuration divination enchantment evocation illusion necromancy transmutation"
).split()
SPELL_CLASSES = "bard cleric druid paladin ranger sorcerer warlock wizard".split()
DAMAGE_TYPES = (
    "acid bludgeoning cold fire force lightning necrotic piercing poison psychic radiant "
    "slashing thunder"
).split()
CREATURE_TYPES = (
    "aberration beast celestial construct dragon elemental fey fiend giant humanoid "
    "monstrosity ooze plant undead"
).split()
SIZES = "tiny small medium large huge gargantuan".split()
CASTING_TIMES = {
    "action": "action",
    "bonus-action": "bonus action",
    "reaction": "reaction",
    "1minute": "1 minute",
    "10minutes": "10 minutes",
    "1hour": "1 hour",
}
CastingTime = Literal["action", "bonus action", "reaction", "1 minute", "10 minutes", "1 hour"]


class SpellQuery(BaseModel):
    """Filters for search_spells. All are optional and combine with AND."""

    model_config = ConfigDict(extra="forbid")

    level: int | None = Field(default=None, ge=0, le=9, description="Exact level; 0 = cantrip")
    min_level: int | None = Field(default=None, ge=0, le=9)
    max_level: int | None = Field(default=None, ge=0, le=9)
    school: str | None = Field(default=None, description=f"One of: {', '.join(SCHOOLS)}")
    spell_class: str | None = Field(
        default=None, description=f"Class spell list: {', '.join(SPELL_CLASSES)}"
    )
    damage_type: str | None = Field(default=None, description=f"One of: {', '.join(DAMAGE_TYPES)}")
    concentration: bool | None = None
    ritual: bool | None = None
    casting_time: CastingTime | None = None
    text: str | None = Field(
        default=None, max_length=200, description="Words to find in the name or description"
    )
    limit: int = Field(default=15, ge=1, le=50)


class MonsterQuery(BaseModel):
    """Filters for search_monsters. All are optional and combine with AND."""

    model_config = ConfigDict(extra="forbid")

    min_cr: str | None = Field(default=None, description="e.g. '1/4', '3'")
    max_cr: str | None = Field(default=None, description="e.g. '5'")
    creature_type: str | None = Field(
        default=None, description=f"One of: {', '.join(CREATURE_TYPES)}"
    )
    size: str | None = Field(default=None, description=f"One of: {', '.join(SIZES)}")
    can_fly: bool | None = Field(
        default=None, description="true = only flyers; false = exclude anything with a fly speed"
    )
    text: str | None = Field(
        default=None, max_length=200, description="Words to find in name, traits or actions"
    )
    limit: int = Field(default=15, ge=1, le=50)


class EntityQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["spell", "monster"]
    name: str = Field(max_length=200)


def _choice(value: str | None, options: list[str], field_name: str) -> str | None:
    if value is None:
        return None
    v = value.strip().lower()
    if v.endswith("s") and v[:-1] in options:  # "wizards" → "wizard", "undeads" → "undead"
        v = v[:-1]
    if v not in options:
        raise ToolInputError(f"Unknown {field_name} {value!r}. Valid: {', '.join(options)}.")
    return v


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


@dataclass
class Spell:
    record: dict[str, Any]
    srd_citation: str | None = None

    @property
    def name(self) -> str:
        return self.record["name"]

    @property
    def classes(self) -> list[str]:
        return [c["name"].lower() for c in self.record.get("classes", [])]

    def components(self) -> str:
        r = self.record
        flags = (("V", r["verbal"]), ("S", r["somatic"]), ("M", r["material"]))
        parts = [letter for letter, on in flags if on]
        text = ", ".join(parts)
        if r["material"] and r.get("material_specified"):
            text += f" ({r['material_specified']})"
        return text

    def summary(self) -> dict[str, Any]:
        r = self.record
        return {
            "name": r["name"],
            "level": r["level"],
            "school": r["school"]["name"],
            "classes": [c["name"] for c in r.get("classes", [])],
            "casting_time": CASTING_TIMES.get(r["casting_time"], r["casting_time"]),
            "range": r["range_text"],
            "concentration": r["concentration"],
            "ritual": r["ritual"],
            "damage_types": r["damage_types"],
            "citation": self.citation,
        }

    def detail(self) -> dict[str, Any]:
        r = self.record
        return {
            **self.summary(),
            "components": self.components(),
            "duration": r["duration"],
            "saving_throw": r["saving_throw_ability"] or None,
            "attack_roll": r["attack_roll"],
            "damage_roll": r["damage_roll"] or None,
            "description": r["desc"],
            "higher_levels": r["higher_level"] or None,
        }

    @property
    def citation(self) -> str:
        return self.srd_citation or f"Open5e API v2, srd-2024 ({self.record['key']})"


@dataclass
class Monster:
    record: dict[str, Any]
    srd_citation: str | None = None

    @property
    def name(self) -> str:
        return self.record["name"]

    @property
    def cr(self) -> Fraction:
        return Fraction(self.record["challenge_rating"]).limit_denominator(8)

    @property
    def fly_speed(self) -> int:
        return int(self.record.get("speed_all", {}).get("fly") or 0)

    def summary(self) -> dict[str, Any]:
        r = self.record
        return {
            "name": r["name"],
            "cr": format_cr(self.cr),
            "xp": r["experience_points"],
            "type": r["type"]["name"],
            "size": r["size"]["name"],
            "ac": r["armor_class"],
            "hp": r["hit_points"],
            # `speed` is the stat block as printed; `speed_all` adds derived half speeds.
            "speed": {k: v for k, v in (r.get("speed") or {}).items() if v and k != "unit"},
            "citation": self.citation,
        }

    def detail(self) -> dict[str, Any]:
        r = self.record
        ri = r.get("resistances_and_immunities", {})
        return {
            **self.summary(),
            "alignment": r.get("alignment"),
            "hit_dice": r.get("hit_dice"),
            "initiative_bonus": r.get("initiative_bonus"),
            "ability_scores": r.get("ability_scores"),
            "saving_throws": r.get("saving_throws"),
            "skills": r.get("skill_bonuses"),
            "damage_resistances": ri.get("damage_resistances_display") or None,
            "damage_immunities": ri.get("damage_immunities_display") or None,
            "condition_immunities": ri.get("condition_immunities_display") or None,
            "senses": {
                "darkvision": r.get("darkvision_range"),
                "blindsight": r.get("blindsight_range"),
                "truesight": r.get("truesight_range"),
                "tremorsense": r.get("tremorsense_range"),
                "passive_perception": r.get("passive_perception"),
            },
            "languages": (r.get("languages") or {}).get("as_string"),
            "traits": [{"name": t["name"], "desc": t["desc"]} for t in r.get("traits", [])],
            "actions": [
                {"name": a["name"], "type": a.get("action_type"), "desc": a["desc"]}
                for a in r.get("actions", [])
            ],
        }

    def search_text(self) -> str:
        r = self.record
        names = [t["name"] for t in r.get("traits", [])] + [a["name"] for a in r.get("actions", [])]
        descs = [t["desc"] for t in r.get("traits", [])] + [a["desc"] for a in r.get("actions", [])]
        return " ".join([r["name"], r["type"]["name"], *names, *descs]).lower()

    @property
    def citation(self) -> str:
        return self.srd_citation or f"Open5e API v2, srd-2024 ({self.record['key']})"


class Catalog:
    def __init__(
        self,
        spells: list[dict[str, Any]],
        creatures: list[dict[str, Any]],
        chunks: list[Chunk] | None = None,
    ) -> None:
        pages = _citation_index(chunks or [])
        self.spells = [Spell(r, pages.get(("spell", r["name"].lower()))) for r in spells]
        self.monsters = [
            Monster(r, pages.get(("monster", n)) or pages.get(("any", n)))
            for r in creatures
            if (n := r["name"].lower())
        ]
        self._spell_by_name = {s.name.lower(): s for s in self.spells}
        self._monster_by_name = {m.name.lower(): m for m in self.monsters}

    @classmethod
    def load(cls, directory: Path | None = None, chunks: list[Chunk] | None = None) -> Catalog:
        kwargs = {"directory": directory} if directory else {}
        try:
            spells = json.loads(snapshot_path("spells", **kwargs).read_text(encoding="utf-8"))
            creatures = json.loads(snapshot_path("creatures", **kwargs).read_text(encoding="utf-8"))
        except FileNotFoundError as e:
            raise FileNotFoundError(f"{e.filename} missing — run `dungeonmind sync-open5e`") from e
        return cls(spells, creatures, chunks)

    def search_spells(self, q: SpellQuery) -> list[Spell]:
        school = _choice(q.school, SCHOOLS, "school")
        spell_class = _choice(q.spell_class, SPELL_CLASSES, "class")
        damage = _choice(q.damage_type, DAMAGE_TYPES, "damage type")
        if q.min_level is not None and q.max_level is not None and q.min_level > q.max_level:
            raise ToolInputError("min_level is greater than max_level")
        casting_key = {v: k for k, v in CASTING_TIMES.items()}.get(q.casting_time or "")
        words = _words(q.text) if q.text else []

        def keep(s: Spell) -> bool:
            r = s.record
            return (
                (q.level is None or r["level"] == q.level)
                and (q.min_level is None or r["level"] >= q.min_level)
                and (q.max_level is None or r["level"] <= q.max_level)
                and (school is None or r["school"]["key"] == school)
                and (spell_class is None or spell_class in s.classes)
                and (damage is None or damage in r["damage_types"])
                and (q.concentration is None or r["concentration"] == q.concentration)
                and (q.ritual is None or r["ritual"] == q.ritual)
                and (casting_key is None or r["casting_time"] == casting_key)
                and all(w in (r["name"] + " " + r["desc"]).lower() for w in words)
            )

        hits = sorted(filter(keep, self.spells), key=lambda s: (s.record["level"], s.name))
        return hits[: q.limit]

    def search_monsters(self, q: MonsterQuery) -> list[Monster]:
        try:
            lo = parse_cr(q.min_cr) if q.min_cr is not None else None
            hi = parse_cr(q.max_cr) if q.max_cr is not None else None
        except ValueError as e:
            raise ToolInputError(f"{e}. Use values like '1/8', '1/4', '1/2', '1' … '30'.") from e
        if lo is not None and hi is not None and lo > hi:
            raise ToolInputError("min_cr is greater than max_cr")
        ctype = _choice(q.creature_type, CREATURE_TYPES, "creature type")
        size = _choice(q.size, SIZES, "size")
        words = _words(q.text) if q.text else []

        def keep(m: Monster) -> bool:
            r = m.record
            return (
                (lo is None or m.cr >= lo)
                and (hi is None or m.cr <= hi)
                and (ctype is None or r["type"]["key"] == ctype)
                and (size is None or r["size"]["key"] == size)
                and (q.can_fly is None or (m.fly_speed > 0) == q.can_fly)
                and all(w in m.search_text() for w in words)
            )

        hits = sorted(filter(keep, self.monsters), key=lambda m: (m.cr, m.name))
        return hits[: q.limit]

    def get_entity(self, q: EntityQuery) -> Spell | Monster:
        index = self._spell_by_name if q.kind == "spell" else self._monster_by_name
        key = q.name.strip().lower()
        if key in index:
            return index[key]
        names = [v.name for v in index.values()]
        # Whole-word matches first ("red dragon" → "Adult Red Dragon"), then spelling
        # near-misses ("Fireblal" → "Fireball").
        words = set(_words(q.name))
        suggestions = [n for n in names if words and words <= set(_words(n))]
        suggestions += difflib.get_close_matches(q.name, names, n=3, cutoff=0.6)
        raise EntityNotFound(q.kind, q.name, list(dict.fromkeys(suggestions))[:5])


def _citation_index(chunks: list[Chunk]) -> dict[tuple[str, str], str]:
    """Map (kind, lowercase title) → SRD PDF citation.

    Spells and monsters are matched within their own chapters first; ("any", title)
    covers stat blocks printed elsewhere, such as the Giant Fly inside the Figurine
    of Wondrous Power magic item.
    """
    kinds = {EntityType.SPELL: "spell", EntityType.MONSTER: "monster"}
    index: dict[tuple[str, str], str] = {}
    for c in chunks:  # setdefault keeps part 1 of a split entry
        if c.entity_type in kinds:
            index.setdefault((kinds[c.entity_type], c.title.lower()), c.citation)
        index.setdefault(("any", c.title.lower()), c.citation)
    return index
