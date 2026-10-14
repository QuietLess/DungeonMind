"""Retrieval benchmark: Recall@K and MRR per retrieval mode and question type.

A retrieved chunk is relevant if its title (last heading) is one of the
question's `relevant` titles. Titles are used instead of chunk ids so the
dataset survives chunking changes; `validate` checks every title exists.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from dungeonmind.rag.hybrid import HybridRetriever, Mode
from dungeonmind.schemas import Chunk

DATASET = Path(__file__).parent / "datasets" / "retrieval_v0.jsonl"


@dataclass(frozen=True)
class Question:
    id: str
    type: str
    query: str
    relevant: list[str]


def load(path: Path = DATASET) -> list[Question]:
    with path.open(encoding="utf-8") as f:
        return [Question(**json.loads(line)) for line in f if line.strip()]


def validate(questions: list[Question], chunks: list[Chunk]) -> list[str]:
    """Return problems: relevant titles that don't exist in the corpus."""
    titles = {c.title for c in chunks}
    return [
        f"{q.id}: no chunk titled {t!r}" for q in questions for t in q.relevant if t not in titles
    ]


@dataclass
class QuestionResult:
    question: Question
    retrieved: list[str]  # titles, best first
    first_relevant_rank: int | None
    latency_ms: float


def run(
    retriever: HybridRetriever, questions: list[Question], mode: Mode, k: int = 10
) -> list[QuestionResult]:
    results = []
    for q in questions:
        if not q.relevant:
            continue  # unanswerable: scored by the abstention eval, not retrieval
        started = time.perf_counter()
        hits = retriever.retrieve(q.query, k=k, mode=mode)
        latency = (time.perf_counter() - started) * 1000
        titles = [h.chunk.title for h in hits]
        rank = next((i for i, t in enumerate(titles, start=1) if t in q.relevant), None)
        results.append(QuestionResult(q, titles, rank, latency))
    return results


def summarize(results: list[QuestionResult], ks: tuple[int, ...] = (1, 3, 5, 10)) -> dict:
    def metrics(rs: list[QuestionResult]) -> dict:
        ranks = [r.first_relevant_rank for r in rs]
        out = {f"recall@{k}": sum(1 for x in ranks if x and x <= k) / len(rs) for k in ks}
        out["mrr"] = sum(1 / r.first_relevant_rank for r in rs if r.first_relevant_rank) / len(rs)
        out["n"] = len(rs)
        return out

    by_type: dict[str, list[QuestionResult]] = defaultdict(list)
    for r in results:
        by_type[r.question.type].append(r)
    latencies = sorted(r.latency_ms for r in results)
    return {
        "overall": metrics(results),
        "by_type": {t: metrics(rs) for t, rs in sorted(by_type.items())},
        "latency_ms": {
            "p50": statistics.median(latencies),
            "p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
        },
    }
