"""BM25 lexical retrieval over SRD chunks.

Exact rules terms ("Grappled", "Ready", "Opportunity Attack") are where dense
retrieval tends to slip, so this is half of the hybrid retriever (M2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from dungeonmind.schemas import Chunk, EntityType

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORD_TEXT = (
    "a an and are as at be by can do does for from how i if in is it its my of on or "
    "that the their them they this to was what when where which who will with you your"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    rank: int


class LexicalIndex:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        # Headings carry a lot of signal in a rules reference; repeat the entry title to boost it.
        docs = [tokenize(" ".join([*c.heading_path, c.title, c.title, c.text])) for c in chunks]
        self._bm25 = BM25Okapi(docs)

    def search(
        self,
        query: str,
        k: int = 10,
        entity_types: set[EntityType] | None = None,
    ) -> list[Hit]:
        scores = self._bm25.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        hits: list[Hit] = []
        for i in order:
            if scores[i] <= 0:
                break
            chunk = self.chunks[i]
            if entity_types and chunk.entity_type not in entity_types:
                continue
            hits.append(Hit(chunk=chunk, score=float(scores[i]), rank=len(hits) + 1))
            if len(hits) == k:
                break
        return hits
