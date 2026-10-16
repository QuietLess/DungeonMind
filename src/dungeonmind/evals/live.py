"""Live evaluation: scoring, budget and the production runner (M8 contract L1–L3)."""

from __future__ import annotations

from typing import Any

DEFAULT_MAX_COST = 2.00


def score(case, result: dict[str, Any]) -> dict[str, bool]:
    raise NotImplementedError


def estimate(cases, config) -> float:
    raise NotImplementedError


def run_live(cases, runner, *, max_cost: float = DEFAULT_MAX_COST) -> dict[str, Any]:
    raise NotImplementedError
