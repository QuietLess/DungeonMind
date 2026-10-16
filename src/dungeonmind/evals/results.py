"""Versioned results, comparison and regression gates (M8 contract L4, M5, G1–G2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

RESULTS_DIR = Path("evals/results")
GATES = Path("evals/gates.json")


def save(report: dict[str, Any], label: str, root: Path = RESULTS_DIR) -> Path:
    raise NotImplementedError


def compare(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    raise NotImplementedError


def gate(offline: dict[str, Any], gates: dict[str, Any], live: dict[str, Any] | None = None) -> list[str]:
    raise NotImplementedError
