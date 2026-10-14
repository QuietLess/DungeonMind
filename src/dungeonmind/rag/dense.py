"""Dense vector retrieval: local ONNX embeddings (fastembed) stored in Qdrant.

Qdrant runs in embedded mode (a folder on disk), so local development needs no
server. Pointing `QdrantClient` at a URL instead switches to a real Qdrant
instance without other code changes.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from pathlib import Path

from fastembed import TextEmbedding
from qdrant_client import QdrantClient, models

from dungeonmind.schemas import Chunk, EntityType

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_INDEX_PATH = Path("data/index/qdrant")
COLLECTION = "srd_5_2_1"

# BGE models are trained with this instruction on the query side only.
_QUERY_PREFIX = {"BAAI/": "Represent this sentence for searching relevant passages: "}


def embedding_text(chunk: Chunk) -> str:
    """What gets embedded: the heading path gives context the body text often lacks."""
    return f"{' › '.join(chunk.heading_path)}\n\n{chunk.text}"


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


class DenseIndex:
    def __init__(
        self,
        path: Path | str = DEFAULT_INDEX_PATH,
        model_name: str = DEFAULT_MODEL,
        client: QdrantClient | None = None,
    ) -> None:
        self.model_name = model_name
        self._model: TextEmbedding | None = None
        self.client = client or QdrantClient(path=str(path))

    @property
    def model(self) -> TextEmbedding:
        if self._model is None:
            self._model = TextEmbedding(self.model_name)
        return self._model

    def _query_vector(self, query: str) -> list[float]:
        prefix = next((p for k, p in _QUERY_PREFIX.items() if self.model_name.startswith(k)), "")
        return next(iter(self.model.embed([prefix + query]))).tolist()

    def build(self, chunks: list[Chunk], batch_size: int = 64) -> None:
        """(Re)create the collection from scratch; the corpus is small enough to rebuild."""
        vectors: Iterable = self.model.embed(
            [embedding_text(c) for c in chunks], batch_size=batch_size
        )
        first = True
        batch: list[models.PointStruct] = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            if first:
                if self.client.collection_exists(COLLECTION):
                    self.client.delete_collection(COLLECTION)
                self.client.create_collection(
                    COLLECTION,
                    vectors_config=models.VectorParams(
                        size=len(vector), distance=models.Distance.COSINE
                    ),
                )
                first = False
            batch.append(
                models.PointStruct(
                    id=_point_id(chunk.chunk_id),
                    vector=vector.tolist(),
                    payload={
                        "chunk_id": chunk.chunk_id,
                        "entity_type": chunk.entity_type.value,
                        "edition": chunk.edition,
                    },
                )
            )
            if len(batch) == 256:
                self.client.upsert(COLLECTION, batch)
                batch = []
        if batch:
            self.client.upsert(COLLECTION, batch)

    def search(
        self,
        query: str,
        k: int = 10,
        entity_types: set[EntityType] | None = None,
    ) -> list[tuple[str, float]]:
        """Return (chunk_id, cosine score) pairs, best first."""
        query_filter = None
        if entity_types:
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="entity_type",
                        match=models.MatchAny(any=[e.value for e in entity_types]),
                    )
                ]
            )
        result = self.client.query_points(
            COLLECTION,
            query=self._query_vector(query),
            query_filter=query_filter,
            limit=k,
            with_payload=["chunk_id"],
        )
        return [(p.payload["chunk_id"], p.score) for p in result.points]
