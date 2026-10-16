"""Deterministic, offline stand-in for the hub's Embedder. Test-only.

Bag-of-words hashed into a fixed number of dimensions and L2-normalised, so texts that share
words score higher than texts that don't. Good enough to exercise ranking without a model.
"""

from __future__ import annotations

import hashlib
import math

from dungeonmind.rag.lexical import tokenize

DIM = 256


def _vector(text: str) -> list[float]:
    v = [0.0] * DIM
    for token in tokenize(text):
        h = int.from_bytes(hashlib.sha1(token.encode()).digest()[:4], "big")
        v[h % DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


class HashEmbedder:
    def __init__(self) -> None:
        self.documents: list[str] = []  # every text embedded as a document, in order
        self.queries: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.documents.extend(texts)
        return [_vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return _vector(text)


class FailingEmbedder(HashEmbedder):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding model unavailable /secret/path")
