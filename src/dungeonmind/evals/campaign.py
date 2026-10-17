"""Notes retrieval on a real campaign, locally (M9f contract E1–E4). No model calls."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dungeonmind.hub.errors import HubError
from dungeonmind.hub.models import User

LEAK_K = 50


@dataclass
class CampaignCase:
    id: str
    question: str
    viewer: str
    expect: list[str]
    no_leak: list[str] = field(default_factory=list)


def make_embedder():
    from dungeonmind.hub.app import NoteEmbedder

    return NoteEmbedder(threading.Lock())


def open_hub(root: Path):
    from dungeonmind.hub.service import Hub

    if not (Path(root) / "app.db").is_file():
        raise FileNotFoundError(f"No hub found: {Path(root) / 'app.db'} doesn't exist.")
    return Hub(root, embedder=make_embedder())


def load_cases(path: Path, hub, cid: str) -> tuple[list[tuple[CampaignCase, User]], list[str]]:
    """E1: every valid case with its viewer, and one message per invalid line."""
    cases, errors = [], []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            case = CampaignCase(
                id=str(raw["id"]),
                question=str(raw["question"]),
                viewer=str(raw["viewer"]),
                expect=[str(t) for t in raw.get("expect", [])],
                no_leak=[str(t) for t in raw.get("no_leak", [])],
            )
        except (ValueError, KeyError, TypeError) as e:
            errors.append(f"line {n}: not a valid case ({e.__class__.__name__})")
            continue
        with hub.storage.app() as db:
            row = db.execute(
                "SELECT id, username FROM users WHERE username = ?", (case.viewer,)
            ).fetchone()
        if row is None:
            errors.append(f"line {n}: no user {case.viewer!r}")
            continue
        user = User(id=row["id"], username=row["username"])
        try:
            hub._member(user, cid)
        except HubError:
            errors.append(f"line {n}: {case.viewer!r} isn't in this campaign")
            continue
        cases.append((case, user))
    return cases, errors


def evaluate(hub, cid: str, cases: list[tuple[CampaignCase, User]], k: int = 5) -> dict[str, Any]:
    """E2: R@k and MRR over cases with expectations; misses; leaks per viewer."""
    with hub.storage.campaign(cid) as db:
        titles = {r["id"]: r["title"] for r in db.execute("SELECT id, title FROM entries")}
    recalls, rrs, misses, leaks = [], [], [], []
    for case, user in cases:
        viewer = hub._viewer(user, hub._member(user, cid))
        hits = hub._search(cid, viewer, case.question, max(k, LEAK_K))
        ranked = [titles.get(h.entry_id, "?") for h in hits]
        if case.expect:
            expected = set(case.expect)
            found = expected & set(ranked[:k])
            recalls.append(len(found) / len(expected))
            rrs.append(next((1 / i for i, t in enumerate(ranked, 1) if t in expected), 0.0))
            if found != expected:
                misses.append(
                    {"id": case.id, "missing": sorted(expected - found), "top": ranked[:k]}
                )
        text = " ".join(f"{h.source} {h.text}" for h in hits[:LEAK_K]).lower()
        if any(s.lower() in text for s in case.no_leak):
            leaks.append(case.id)
    n = len(recalls)
    return {
        "n": n,
        f"recall@{k}": round(sum(recalls) / n, 4) if n else 0.0,
        "mrr": round(sum(rrs) / n, 4) if n else 0.0,
        "misses": misses,
        "leaks": leaks,
        "k": k,
    }
