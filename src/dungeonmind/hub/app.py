"""Production wiring for `dungeonmind serve`: real embedder, SRD retriever, catalog and Claude."""

from __future__ import annotations

import threading
from pathlib import Path

from dungeonmind.rag.dense import DenseIndex

DEFAULT_CHUNKS = Path("data/processed/srd_5.2.1_chunks.jsonl")
SRD_K = 6


class FastEmbedder:
    """The hub's Embedder over the SRD index's fastembed model (one model in memory, F11)."""

    def __init__(self, dense: DenseIndex, lock: threading.Lock) -> None:
        self.dense = dense
        self.lock = lock

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        with self.lock:
            return [v.tolist() for v in self.dense.model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        with self.lock:
            return self.dense._query_vector(text)


def build_hub(
    root: Path,
    daily_message_limit: int = 50,
    chunks: Path = DEFAULT_CHUNKS,
    models=None,
    provider: str = "claude",
):
    from dungeonmind.graph.providers import make_llm, provider_timeout
    from dungeonmind.hub.chats import make_engine
    from dungeonmind.hub.service import Hub
    from dungeonmind.rag.hybrid import HybridRetriever, Mode
    from dungeonmind.rag.ingest import read_jsonl
    from dungeonmind.tools.catalog import Catalog
    from dungeonmind.tools.registry import ToolRegistry

    if not chunks.exists():
        raise SystemExit(f"{chunks} not found: run `dungeonmind ingest` and `dungeonmind index`.")
    dense = DenseIndex()
    _ = dense.model  # load once at start-up, not racily on the first two requests
    srd_chunks = read_jsonl(chunks)
    retriever = HybridRetriever(srd_chunks, dense=dense)
    # One lock for the shared model and the embedded Qdrant client, used by SRD search and
    # note embedding alike (request handlers run in a thread pool).
    model_lock = threading.Lock()

    def retrieve(query: str):
        with model_lock:
            return retriever.retrieve(query, k=SRD_K, mode=Mode.DENSE)

    registry = ToolRegistry(Catalog.load(chunks=srd_chunks))
    engine = make_engine(
        make_llm(provider, models), retrieve, registry, timeout_s=provider_timeout(provider)
    )
    return Hub(
        root,
        embedder=FastEmbedder(dense, model_lock),
        engine=engine,
        daily_message_limit=daily_message_limit,
    )
