"""Snapshot SRD 5.2 (2024) spells and creatures from the Open5e v2 API.

Open5e hosts many documents side by side (SRD 5.1, third-party books, A5E),
so every request is pinned to `document__key=srd-2024`. That pin is the
"never silently mix editions" rule for structured data.

Lookups run against the local snapshot rather than the live API: they're
fast, work offline, and give the same answer every time (so tests and evals
are reproducible). `dungeonmind sync-open5e` refreshes it.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

BASE_URL = "https://api.open5e.com/v2"
DOCUMENT_KEY = "srd-2024"
DEFAULT_DIR = Path("data/processed")
# Counts in the SRD 5.2.1 PDF; a sync that disagrees is treated as a failure.
EXPECTED = {"spells": 339, "creatures": 331}


def snapshot_path(kind: str, directory: Path = DEFAULT_DIR) -> Path:
    return directory / f"open5e_{DOCUMENT_KEY}_{kind}.json"


def fetch_all(kind: str, client: httpx.Client | None = None) -> list[dict]:
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    url: str | None = f"{BASE_URL}/{kind}/"
    params: dict | None = {"document__key": DOCUMENT_KEY, "limit": 100, "format": "json"}
    records: list[dict] = []
    while url:
        response = client.get(url, params=params)
        response.raise_for_status()
        page = response.json()
        records.extend(page["results"])
        url, params = page.get("next"), None  # `next` already carries the query string
    wrong = [r["key"] for r in records if r.get("document", {}).get("key") != DOCUMENT_KEY]
    if wrong:
        raise ValueError(f"{len(wrong)} {kind} records aren't from {DOCUMENT_KEY}: {wrong[:5]}")
    return records


def sync(directory: Path = DEFAULT_DIR) -> dict[str, int]:
    directory.mkdir(parents=True, exist_ok=True)
    counts = {}
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for kind, expected in EXPECTED.items():
            records = fetch_all(kind, client)
            if len(records) != expected:
                raise ValueError(f"expected {expected} {kind}, got {len(records)}")
            records.sort(key=lambda r: r["key"])
            snapshot_path(kind, directory).write_text(
                json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            counts[kind] = len(records)
    return counts
