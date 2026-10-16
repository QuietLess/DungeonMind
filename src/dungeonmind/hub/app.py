"""Production wiring for `dungeonmind serve`: real embedder, SRD retriever, catalog and Claude."""

from __future__ import annotations

import threading
from pathlib import Path

from dungeonmind.rag.dense import _QUERY_PREFIX, DenseIndex

DEFAULT_CHUNKS = Path("data/processed/srd_5.2.1_chunks.jsonl")
SRD_K = 6


class FastEmbedder:
    """The hub's Embedder over the SRD index's fastembed model (one model in memory)."""

    def __init__(self, dense: DenseIndex) -> None:
        self.dense = dense
        self._lock = threading.Lock()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            return [v.tolist() for v in self.dense.model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        name = self.dense.model_name
        prefix = next((p for k, p in _QUERY_PREFIX.items() if name.startswith(k)), "")
        with self._lock:
            return next(iter(self.dense.model.embed([prefix + text]))).tolist()


def build_hub(root: Path, daily_message_limit: int = 50, chunks: Path = DEFAULT_CHUNKS):
    from dungeonmind.graph.llm import ClaudeLLM
    from dungeonmind.hub.chats import make_engine
    from dungeonmind.hub.service import Hub
    from dungeonmind.rag.hybrid import HybridRetriever, Mode
    from dungeonmind.rag.ingest import read_jsonl
    from dungeonmind.tools.catalog import Catalog
    from dungeonmind.tools.registry import ToolRegistry

    if not chunks.exists():
        raise SystemExit(f"{chunks} not found: run `dungeonmind ingest` and `dungeonmind index`.")
    dense = DenseIndex()
    srd_chunks = read_jsonl(chunks)
    retriever = HybridRetriever(srd_chunks, dense=dense)
    srd_lock = threading.Lock()  # embedded Qdrant isn't shared across request threads

    def retrieve(query: str):
        with srd_lock:
            return retriever.retrieve(query, k=SRD_K, mode=Mode.DENSE)

    registry = ToolRegistry(Catalog.load(chunks=srd_chunks))
    engine = make_engine(ClaudeLLM(), retrieve, registry)
    return Hub(
        root, embedder=FastEmbedder(dense), engine=engine, daily_message_limit=daily_message_limit
    )
