"""Hybrid retrieval: BM25 + dense → Reciprocal Rank Fusion → cross-encoder rerank.

BM25 catches exact rules terms ("Ready", "Grappled"); dense vectors catch
paraphrases ("grab someone"). RRF merges the two rankings without having to
calibrate their incompatible scores, and a cross-encoder re-scores the fused
shortlist by reading query and passage together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from fastembed.rerank.cross_encoder import TextCrossEncoder

from dungeonmind.rag.dense import DenseIndex, embedding_text
from dungeonmind.rag.lexical import LexicalIndex
from dungeonmind.schemas import Chunk, EntityType

DEFAULT_RERANKER = "Xenova/ms-marco-MiniLM-L-6-v2"
RRF_K = 60  # standard constant from Cormack et al. (2009)


class Mode(StrEnum):
    BM25 = "bm25"
    DENSE = "dense"
    HYBRID = "hybrid"  # BM25 + dense with RRF
    HYBRID_RERANK = "hybrid_rerank"
    DENSE_RERANK = "dense_rerank"


@dataclass
class Evidence:
    chunk: Chunk
    score: float
    rank: int
    # Where the candidate came from, for traces: {"bm25": 3, "dense": 1, "rerank": 1}
    ranks: dict[str, int] = field(default_factory=dict)

    @property
    def evidence_id(self) -> str:
        return self.chunk.chunk_id


def rrf(
    rankings: dict[str, list[str]],
    weights: dict[str, float] | None = None,
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    """Fuse ranked id lists: score(d) = Σ w_i / (k + rank_i(d))."""
    weights = weights or {}
    scores: dict[str, float] = {}
    for name, ids in rankings.items():
        w = weights.get(name, 1.0)
        for rank, doc_id in enumerate(ids, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + w / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


class Reranker:
    def __init__(self, model_name: str = DEFAULT_RERANKER) -> None:
        self.model_name = model_name
        self._model: TextCrossEncoder | None = None

    def score(self, query: str, chunks: list[Chunk]) -> list[float]:
        if self._model is None:
            self._model = TextCrossEncoder(self.model_name)
        return list(self._model.rerank(query, [embedding_text(c) for c in chunks]))


class HybridRetriever:
    def __init__(
        self,
        chunks: list[Chunk],
        dense: DenseIndex | None = None,
        reranker: Reranker | None = None,
        candidates: int = 30,
        weights: dict[str, float] | None = None,
        rerank_top: int = 20,
    ) -> None:
        self.by_id = {c.chunk_id: c for c in chunks}
        self.lexical = LexicalIndex(chunks)
        self.dense = dense
        self.reranker = reranker
        self.candidates = candidates
        self.weights = weights or {}
        self.rerank_top = rerank_top

    def retrieve(
        self,
        query: str,
        k: int = 5,
        mode: Mode = Mode.DENSE,
        entity_types: set[EntityType] | None = None,
    ) -> list[Evidence]:
        rankings: dict[str, list[str]] = {}
        if mode in (Mode.BM25, Mode.HYBRID, Mode.HYBRID_RERANK):
            hits = self.lexical.search(query, k=self.candidates, entity_types=entity_types)
            rankings["bm25"] = [h.chunk.chunk_id for h in hits]
        if mode is not Mode.BM25:
            if self.dense is None:
                raise ValueError(f"mode {mode} needs a dense index")
            hits = self.dense.search(query, k=self.candidates, entity_types=entity_types)
            rankings["dense"] = [chunk_id for chunk_id, _ in hits]

        fused = rrf(rankings, self.weights)
        positions = {name: {d: i for i, d in enumerate(ids, 1)} for name, ids in rankings.items()}
        source_ranks = {
            doc_id: {name: pos[doc_id] for name, pos in positions.items() if doc_id in pos}
            for doc_id, _ in fused
        }

        if mode in (Mode.HYBRID_RERANK, Mode.DENSE_RERANK):
            if self.reranker is None:
                raise ValueError(f"{mode} mode needs a reranker")
            shortlist = [self.by_id[doc_id] for doc_id, _ in fused[: self.rerank_top]]
            scores = self.reranker.score(query, shortlist)
            ordered = sorted(
                zip(shortlist, scores, strict=True), key=lambda cs: cs[1], reverse=True
            )
            results = []
            for rank, (chunk, score) in enumerate(ordered[:k], start=1):
                ranks = {**source_ranks[chunk.chunk_id], "rerank": rank}
                results.append(Evidence(chunk=chunk, score=float(score), rank=rank, ranks=ranks))
            return results

        return [
            Evidence(chunk=self.by_id[doc_id], score=score, rank=rank, ranks=source_ranks[doc_id])
            for rank, (doc_id, score) in enumerate(fused[:k], start=1)
        ]
