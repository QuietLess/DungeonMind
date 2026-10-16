"""Versioned results, comparison and regression gates (M8 contract L4, M5, G1–G2)."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dungeonmind.evals.suite import VERSION

RESULTS_DIR = Path("evals/results")
GATES = Path("evals/gates.json")
_LABEL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,79}")


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def save(report: dict[str, Any], label: str, root: Path = RESULTS_DIR) -> Path:
    """L4: write the report with version metadata; never overwrites an earlier result."""
    if not isinstance(label, str) or not _LABEL.fullmatch(label):
        raise ValueError("labels are 1–80 characters: letters, digits, '.', '_' or '-'")
    meta = {
        **report.get("meta", {}),
        "label": label,
        "git_commit": _git("rev-parse", "HEAD") or "unknown",
        "git_dirty": bool(_git("status", "--porcelain")),
        "dataset": VERSION,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{label}.json"
    n = 2
    while path.exists():
        path = root / f"{label}-{n}.json"
        n += 1
    path.write_text(
        json.dumps({**report, "meta": meta}, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return path


def _pct(x: float | None) -> str:
    return "  –  " if x is None else f"{x * 100:5.1f}%"


def compare(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """M5: per-category pass rates side by side, then cost and latency."""
    la, lb = a.get("meta", {}).get("label", "A"), b.get("meta", {}).get("label", "B")
    lines = [f"{'category':<14} {la[:12]:>12} {lb[:12]:>12}   delta (pp)"]
    cats = sorted(set(a.get("categories", {})) | set(b.get("categories", {})))
    rows = [
        (
            c,
            a.get("categories", {}).get(c, {}).get("pass_rate"),
            b.get("categories", {}).get(c, {}).get("pass_rate"),
        )
        for c in cats
    ]
    rows.append(
        ("OVERALL", a.get("overall", {}).get("pass_rate"), b.get("overall", {}).get("pass_rate"))
    )
    for name, x, y in rows:
        delta = "" if x is None or y is None else f"{(y - x) * 100:+.1f}"
        lines.append(f"{name:<14} {_pct(x):>12} {_pct(y):>12}   {delta}")
    ca, cb = a.get("cost", {}).get("total", 0.0), b.get("cost", {}).get("total", 0.0)
    lines.append(f"{'cost':<14} {'$' + format(ca, '.2f'):>12} {'$' + format(cb, '.2f'):>12}")
    pa, pb = a.get("latency_ms", {}).get("p50", 0.0), b.get("latency_ms", {}).get("p50", 0.0)
    lines.append(f"{'latency p50':<14} {f'{pa:.0f} ms':>12} {f'{pb:.0f} ms':>12}")
    return lines


def gate(
    offline: dict[str, Any], gates: dict[str, Any], live: dict[str, Any] | None = None
) -> list[str]:
    """G1/G2: every threshold breach, as a readable line; [] means the gate passes."""
    breaches: list[str] = []
    if not offline.get("valid", True):
        breaches.append("dataset invalid (run `dungeonmind eval validate`)")
    srd = offline.get("srd")
    if srd:
        best = srd.get("dense") or max(srd.values(), key=lambda m: m["recall@5"])
        if best["recall@5"] < gates["srd_recall@5"]:
            breaches.append(f"srd recall@5 {best['recall@5']:.2f} < {gates['srd_recall@5']}")
    notes = offline.get("notes")
    if notes and notes["recall@5"] < gates["notes_recall@5"]:
        breaches.append(f"notes recall@5 {notes['recall@5']:.2f} < {gates['notes_recall@5']}")
    if offline.get("leak_rate", 0.0) > gates["leak_rate"]:
        breaches.append(f"leak_rate {offline['leak_rate']:.3f} > {gates['leak_rate']}")
    violations = offline.get("contract", {}).get("violations", [])
    if len(violations) > gates["contract_violations"]:
        breaches.append(f"contract: {len(violations)} violations (first: {violations[0]})")
    if live is not None:
        rate = live.get("overall", {}).get("pass_rate", 0.0)
        if rate < gates["live_pass_rate"]:
            breaches.append(f"live pass rate {rate:.2f} < {gates['live_pass_rate']}")
        scores = live.get("scores", {})
        if scores.get("no_leak_ok", 1.0) < gates["live_no_leak"]:
            breaches.append(f"live no_leak_ok {scores['no_leak_ok']:.3f} < {gates['live_no_leak']}")
        if scores.get("citations_ok", 1.0) < gates["live_citations_ok"]:
            breaches.append(
                f"live citations_ok {scores['citations_ok']:.2f} < {gates['live_citations_ok']}"
            )
    return breaches


def load_gates(path: Path = GATES) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
