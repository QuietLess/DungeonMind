"""The M8 evaluation suite: loading and validation (docs/ARCHITECTURE.md › M8 contract, V1)."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUITE = Path(__file__).parent / "datasets" / "suite_v1.jsonl"
VERSION = "suite_v1"
CATEGORY_MINIMUMS = {
    "rules": 60,
    "lookup": 25,
    "probability": 20,
    "encounter": 12,
    "campaign": 40,
    "prep": 8,
    "followup": 8,
    "out_of_scope": 12,
    "other_edition": 6,
    "unanswerable": 10,
    "injection": 8,
}
VIEWER_CATEGORIES = frozenset({"campaign", "prep"})  # always read the campaign


@dataclass
class Case:
    id: str
    category: str
    question: str
    expect: dict[str, Any]
    viewer: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)


EXPECT_FIELDS = frozenset(
    {
        "intent",
        "srd",
        "notes",
        "tool",
        "contains",
        "numbers",
        "abstain",
        "decline",
        "other_edition",
        "no_leak",
        "forbid",
        "args",
    }
)
VIEWERS = frozenset({"dm", "player"})
NUMBER_TOLERANCE = 0.5


def load(path: Path = SUITE) -> list[Case]:
    cases = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                cases.append(Case(**json.loads(line)))
    return cases


def reads_campaign(case: Case) -> bool:
    return case.category in VIEWER_CATEGORIES or bool(case.expect.get("notes"))


def validate(cases: list[Case], srd_titles: set[str], note_titles: set[str]) -> list[str]:
    """Problems with the dataset; [] when it's sound. Minimums apply to the full suite only."""
    problems: list[str] = []
    seen: set[str] = set()
    for c in cases:
        where = f"{c.id}:"
        if c.id in seen:
            problems.append(f"{where} duplicate id")
        seen.add(c.id)
        if c.category not in CATEGORY_MINIMUMS:
            problems.append(f"{where} unknown category {c.category!r}")
        unknown = set(c.expect) - EXPECT_FIELDS
        if unknown:
            problems.append(f"{where} unknown expect fields {sorted(unknown)}")
        if c.viewer is not None and c.viewer not in VIEWERS:
            problems.append(f"{where} unknown viewer {c.viewer!r}")
        if reads_campaign(c) and c.viewer is None:
            problems.append(f"{where} reads the campaign but has no viewer")
        if c.viewer is not None and not (
            reads_campaign(c) or c.expect.get("no_leak") or c.category in ("followup", "injection")
        ):
            problems.append(f"{where} has a viewer but doesn't read the campaign")
        for title in c.expect.get("srd", []):
            if title not in srd_titles:
                problems.append(f"{where} no SRD section titled {title!r}")
        for title in c.expect.get("notes", []):
            if title not in note_titles:
                problems.append(f"{where} no fixture entry titled {title!r}")
        if c.category == "probability":
            problems += _check_probability(c)
    if len(cases) >= sum(CATEGORY_MINIMUMS.values()):  # the full suite, not a --category subset
        counts = Counter(c.category for c in cases)
        for category, minimum in CATEGORY_MINIMUMS.items():
            if counts[category] < minimum:
                problems.append(f"{category}: {counts[category]} cases, needs {minimum}")
    return problems


def _check_probability(c: Case) -> list[str]:
    """The case's numbers must be what our probability tool computes for its args."""
    from dungeonmind.tools import probability as P

    args = c.expect.get("args")
    if not args:
        return [f"{c.id}: probability case without args"]
    mode = P.RollMode.resolve(args.get("advantage", False), args.get("disadvantage", False))
    if args["roll_type"] == "attack":
        odds = P.attack(args["bonus"], args["target"], mode, args.get("crit_range", 20))
        want = odds.crit if "crit" in c.question.lower() else odds.hit
    else:
        want = P.check(args["bonus"], args["target"], mode)
    expected = round(float(want) * 100, 2)
    got = c.expect.get("numbers", [])
    if not got or abs(got[0] - expected) > 0.01:
        return [f"{c.id}: numbers {got} but the tool computes {expected}"]
    return []
