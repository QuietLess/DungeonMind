"""The M8 evaluation suite: loading and validation (docs/ARCHITECTURE.md › M8 contract, V1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUITE = Path(__file__).parent / "datasets" / "suite_v1.jsonl"
VERSION = "suite_v1"
CATEGORY_MINIMUMS = {
    "rules": 60, "lookup": 25, "probability": 20, "encounter": 12, "campaign": 40, "prep": 8,
    "followup": 8, "out_of_scope": 12, "other_edition": 6, "unanswerable": 10, "injection": 8,
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


def load(path: Path = SUITE) -> list[Case]:
    raise NotImplementedError


def validate(cases: list[Case], srd_titles: set[str], note_titles: set[str]) -> list[str]:
    raise NotImplementedError
