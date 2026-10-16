"""Campaign-notes benchmark (M6 contract behaviour 12): Recall@K and MRR per search mode.

A fixture campaign (datasets/notes_v0.json) is loaded into a throwaway hub with the real
embedding model; each question lists the entry titles that answer it. Run with
`dungeonmind eval-notes`. Local only: no API calls.
"""

from __future__ import annotations

import json
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path

from dungeonmind.hub import entries as store
from dungeonmind.hub.entries import Embedder

DATASET = Path(__file__).parent / "datasets" / "notes_v0.json"
MODES = {"dense": ("dense",), "bm25": ("bm25",), "hybrid": ("dense", "bm25")}


def run(embedder: Embedder, k: int = 5, path: Path = DATASET) -> dict:
    from dungeonmind.hub.service import Hub

    data = json.loads(path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as root:
        hub = Hub(root, embedder=embedder, scrypt_n=2**4)
        dm = hub.register("eval_dm", "eval password 1")
        camp = hub.create_campaign(dm, data["campaign"])
        owners = {}
        for i, char in enumerate(data["characters"]):
            player = hub.register(f"eval_player_{i}", "eval password 1")
            hub.accept_invite(player, hub.create_invite(dm, camp.id).code)
            owners[char["name"]] = (player, hub.create_character(player, camp.id, **char).id)
        by_id = {}
        for raw in data["entries"]:
            e = {k: v for k, v in raw.items() if k not in ("visibility", "author")}
            author, character_id = dm, None
            if name := e.pop("character", None):
                author, character_id = owners[name]
            made = hub.create_entry(
                author, camp.id, visibility="party", character_id=character_id, **e
            )
            by_id[made.id] = made.title

        viewer = store.Viewer(user_id=dm.id, is_dm=True)
        report = {}
        for mode, modes in MODES.items():
            per_type = defaultdict(lambda: {"recall": [], "rr": []})
            misses = []
            with hub.storage.campaign(camp.id) as db:
                for q in data["questions"]:
                    hits = store.search(db, viewer, embedder, q["query"], k, modes=modes)
                    ranked = [by_id[h.entry_id] for h in hits]
                    relevant = set(q["relevant"])
                    found = relevant & set(ranked)
                    rr = next((1 / i for i, t in enumerate(ranked, 1) if t in relevant), 0.0)
                    for bucket in (q["type"], "all"):
                        per_type[bucket]["recall"].append(len(found) / len(relevant))
                        per_type[bucket]["rr"].append(rr)
                    if not found:
                        misses.append(q["id"])
            report[mode] = {
                t: {
                    f"recall@{k}": round(statistics.mean(v["recall"]), 3),
                    "mrr": round(statistics.mean(v["rr"]), 3),
                    "n": len(v["rr"]),
                }
                for t, v in per_type.items()
            }
            report[mode]["misses"] = misses
        return report
