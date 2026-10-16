"""Entries and characters in one campaign.db: CRUD, chunking, embeddings, search.

No permission logic lives here: hub/service.py decides who may call what, and passes the
viewer to `search` / `list`, whose visibility filter is part of the SQL that selects rows.
"""

from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...
