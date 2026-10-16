"""Entries and characters in one campaign.db: CRUD, chunking, embeddings, search.

No permission logic lives here: hub/service.py decides who may call what. What this module
does own is the visibility filter (`visible_clause`), because it is part of the SQL that
selects candidate rows: an entry the viewer may not read is never loaded, ranked or returned.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import numpy as np
from rank_bm25 import BM25Okapi

from dungeonmind.graph.state import NoteHit
from dungeonmind.hub.models import Character, Entry
from dungeonmind.rag.hybrid import rrf
from dungeonmind.rag.lexical import tokenize

CHUNK_CHARS = 1_500
QUERY_CHARS = 200
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_KIND_LABELS = {"npc": "NPC"}


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True)
class Viewer:
    """Who is reading: decides which rows `visible_clause` lets through."""

    user_id: str
    is_dm: bool


def visible_clause(viewer: Viewer, alias: str = "e") -> tuple[str, list[Any]]:
    """SQL condition for entries `viewer` may read (M6 contract, Roles and visibility).

    party → every member; dm → the author and the DM; private → the author only. Membership
    itself is checked by the caller before any campaign query runs.
    """
    if viewer.is_dm:
        return f"({alias}.visibility IN ('party', 'dm') OR {alias}.author_id = ?)", [viewer.user_id]
    return f"({alias}.visibility = 'party' OR {alias}.author_id = ?)", [viewer.user_id]


# --- chunking and vectors ---------------------------------------------------------------------


def _split_long(paragraph: str) -> list[str]:
    """A paragraph over CHUNK_CHARS: split at sentence ends, then hard-wrap what's still long."""
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        while len(sentence) > CHUNK_CHARS:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:CHUNK_CHARS])
            sentence = sentence[CHUNK_CHARS:]
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > CHUNK_CHARS:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def chunk_body(body: str) -> list[str]:
    """Blank-line paragraphs packed into pieces of at most CHUNK_CHARS characters."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        parts = _split_long(paragraph) if len(paragraph) > CHUNK_CHARS else [paragraph]
        for part in parts:
            candidate = f"{current}\n\n{part}" if current else part
            if len(candidate) > CHUNK_CHARS:
                chunks.append(current)
                current = part
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks or [body.strip()[:CHUNK_CHARS]]


def _to_blob(vector: list[float]) -> bytes:
    v = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(v)) or 1.0
    return (v / norm).astype(np.float32).tobytes()


def embed_entry(embedder: Embedder, title: str, body: str) -> list[tuple[str, bytes]]:
    """(chunk text, normalised float32 vector) pairs; the title is embedded with every chunk."""
    chunks = chunk_body(body)
    vectors = embedder.embed_documents([f"{title}\n\n{c}" for c in chunks])
    if len(vectors) != len(chunks):
        raise ValueError("embedder returned the wrong number of vectors")
    return [(c, _to_blob(v)) for c, v in zip(chunks, vectors, strict=True)]


def replace_chunks(conn: sqlite3.Connection, entry_id: int, chunks: list[tuple[str, bytes]]):
    conn.execute("DELETE FROM chunks WHERE entry_id = ?", (entry_id,))
    conn.executemany(
        "INSERT INTO chunks (entry_id, seq, text, vector) VALUES (?, ?, ?, ?)",
        [(entry_id, i, text, blob) for i, (text, blob) in enumerate(chunks)],
    )


# --- rows -------------------------------------------------------------------------------------


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def entry_from_row(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        board=row["board"],
        character_id=row["character_id"],
        author_id=row["author_id"],
        kind=row["kind"],
        title=row["title"],
        body=row["body"],
        visibility=row["visibility"],
        session=row["session"],
        created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
    )


def character_from_row(row: sqlite3.Row) -> Character:
    return Character(
        id=row["id"], owner_id=row["owner_id"], name=row["name"], summary=row["summary"]
    )


def get_entry(conn: sqlite3.Connection, eid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone()


def list_entries(
    conn: sqlite3.Connection,
    viewer: Viewer,
    *,
    board: str | None = None,
    character_id: int | None = None,
    session: int | None = None,
    q: str | None = None,
) -> list[Entry]:
    where, params = visible_clause(viewer)
    clauses = [where]
    if board is not None:
        clauses.append("e.board = ?")
        params.append(board)
    if character_id is not None:
        clauses.append("e.character_id = ?")
        params.append(character_id)
    if session is not None:
        clauses.append("e.session = ?")
        params.append(session)
    if q:
        clauses.append("(instr(lower(e.title), ?) > 0 OR instr(lower(e.body), ?) > 0)")
        params += [q.lower(), q.lower()]
    rows = conn.execute(
        f"SELECT e.* FROM entries e WHERE {' AND '.join(clauses)} "
        "ORDER BY e.updated_at DESC, e.id DESC",
        params,
    ).fetchall()
    return [entry_from_row(r) for r in rows]


def source_label(row: sqlite3.Row) -> str:
    """The citation label a reader sees, e.g. "Lore › NPC › Elara"."""
    title = row["title"]
    if row["board"] == "lore":
        kind = _KIND_LABELS.get(row["kind"], row["kind"].capitalize())
        return f"Lore › {kind} › {title}"
    if row["board"] == "character":
        kind = row["kind"].capitalize()
        return f"{row['character_name']}'s board › {kind} › {title}"
    who = row["author_name"]
    if row["session"] is not None:
        who += f", session {row['session']}"
    return f"Journal › {title} ({who})"


def search(
    conn: sqlite3.Connection, viewer: Viewer, embedder: Embedder, query: str, k: int
) -> list[NoteHit]:
    """The k visible entries best matching `query` (cosine + BM25 over chunks, fused with RRF).

    Only chunks of entries the viewer may read are loaded; one hit per entry, its best chunk.
    """
    where, params = visible_clause(viewer)
    rows = conn.execute(
        "SELECT c.entry_id, c.seq, c.text AS chunk, c.vector, e.board, e.kind, e.title, "
        "e.author_name, e.session, ch.name AS character_name "
        "FROM chunks c JOIN entries e ON e.id = c.entry_id "
        f"LEFT JOIN characters ch ON ch.id = e.character_id WHERE {where}",
        params,
    ).fetchall()
    if not rows:
        return []
    query = query[:QUERY_CHARS]
    keys = [f"{r['entry_id']}:{r['seq']}" for r in rows]

    q = np.asarray(embedder.embed_query(query), dtype=np.float32)
    q /= float(np.linalg.norm(q)) or 1.0
    matrix = np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
    cosine = matrix @ q
    rankings = {"dense": [keys[i] for i in np.argsort(-cosine, kind="stable")]}

    tokens = tokenize(query)
    if tokens:
        bm25 = BM25Okapi([tokenize(f"{r['title']} {r['chunk']}") or ["_"] for r in rows])
        scores = bm25.get_scores(tokens)
        order = np.argsort(-scores, kind="stable")
        rankings["bm25"] = [keys[i] for i in order if scores[i] > 0]

    by_key = dict(zip(keys, rows, strict=True))
    hits: list[NoteHit] = []
    seen: set[int] = set()
    for key, score in rrf(rankings):
        row = by_key[key]
        if row["entry_id"] in seen:
            continue
        seen.add(row["entry_id"])
        hits.append(
            NoteHit(
                entry_id=row["entry_id"],
                source=source_label(row),
                text=row["chunk"],
                canon=row["board"] == "lore",
                score=score,
            )
        )
        if len(hits) == k:
            break
    return hits
