"""Entries and characters in one campaign.db: CRUD, chunking, embeddings, search.

No permission logic lives here: hub/service.py decides who may call what. What this module
does own is the visibility filter (`visible_clause`), because it is part of the SQL that
selects candidate rows: an entry the viewer may not read is never loaded, ranked or returned.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import numpy as np

from dungeonmind.graph.state import NoteHit
from dungeonmind.hub.models import Character, Entry
from dungeonmind.rag.hybrid import rrf
from dungeonmind.rag.lexical import tokenize

CHUNK_CHARS = 1_500
QUERY_CHARS = 200
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_KIND_LABELS = {"npc": "NPC"}
BM25_K1 = 1.5
BM25_B = 0.75


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


def replace_chunks(
    conn: sqlite3.Connection, entry_id: int, title: str, chunks: list[tuple[str, bytes]]
) -> None:
    """Store an entry's chunks with their vectors, token counts and per-term frequencies."""
    conn.execute("DELETE FROM chunks WHERE entry_id = ?", (entry_id,))
    conn.execute("DELETE FROM chunk_terms WHERE entry_id = ?", (entry_id,))
    for seq, (text, blob) in enumerate(chunks):
        tokens = tokenize(f"{title} {text}")
        conn.execute(
            "INSERT INTO chunks (entry_id, seq, text, vector, length) VALUES (?, ?, ?, ?, ?)",
            (entry_id, seq, text, blob, len(tokens)),
        )
        conn.executemany(
            "INSERT INTO chunk_terms (entry_id, seq, term, tf) VALUES (?, ?, ?, ?)",
            [(entry_id, seq, term, tf) for term, tf in Counter(tokens).items()],
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
        id=row["id"],
        owner_id=row["owner_id"],
        name=row["name"],
        summary=row["summary"],
        # sqlite3.Row: `in row` checks values, so the column names it is.
        is_npc=bool(row["is_npc"]) if "is_npc" in row.keys() else False,  # noqa: SIM118
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
        clauses.append("(instr(casefold(e.title), ?) > 0 OR instr(casefold(e.body), ?) > 0)")
        params += [q.casefold(), q.casefold()]
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


def _bm25(
    conn: sqlite3.Connection, where: str, params: list[Any], tokens: list[str]
) -> dict[str, float]:
    """BM25 scores of the visible chunks containing a query term, keyed "entry:seq".

    Chunk count, average length and document frequencies come from the visible chunks only,
    so entries the viewer can't read never influence their ranking (F2). IDF uses the +1 form,
    which is never negative, so keywords still count in a campaign with one or two entries.
    """
    terms = sorted(set(tokens))
    if not terms:
        return {}
    marks = ",".join("?" * len(terms))
    n, avg_len = conn.execute(
        f"SELECT COUNT(*), AVG(c.length) FROM chunks c JOIN entries e ON e.id = c.entry_id "
        f"WHERE {where}",
        params,
    ).fetchone()
    rows = conn.execute(
        "SELECT t.entry_id, t.seq, t.term, t.tf, c.length FROM chunk_terms t "
        "JOIN chunks c ON c.entry_id = t.entry_id AND c.seq = t.seq "
        f"JOIN entries e ON e.id = t.entry_id WHERE t.term IN ({marks}) AND {where}",
        [*terms, *params],
    ).fetchall()
    df = Counter(r["term"] for r in rows)
    avg_len = avg_len or 1.0
    scores: dict[str, float] = {}
    for r in rows:
        idf = math.log((n - df[r["term"]] + 0.5) / (df[r["term"]] + 0.5) + 1.0)
        norm = BM25_K1 * (1 - BM25_B + BM25_B * r["length"] / avg_len)
        key = f"{r['entry_id']}:{r['seq']}"
        scores[key] = scores.get(key, 0.0) + idf * r["tf"] * (BM25_K1 + 1) / (r["tf"] + norm)
    return scores


SEARCH_MODES = ("dense", "bm25")


def search(
    conn: sqlite3.Connection,
    viewer: Viewer,
    embedder: Embedder,
    query: str,
    k: int,
    modes: tuple[str, ...] = SEARCH_MODES,
) -> list[NoteHit]:
    """The k visible entries best matching `query` (cosine + BM25 over chunks, fused with RRF).

    Only chunks of entries the viewer may read are loaded or counted; one hit per entry, its
    best chunk. `modes` exists for the notes benchmark (dense-only vs hybrid).
    """
    where, params = visible_clause(viewer)
    rows = conn.execute(
        "SELECT c.entry_id, c.seq, c.vector FROM chunks c JOIN entries e ON e.id = c.entry_id "
        f"WHERE {where}",
        params,
    ).fetchall()
    if not rows:
        return []
    query = query[:QUERY_CHARS]
    keys = [f"{r['entry_id']}:{r['seq']}" for r in rows]

    rankings: dict[str, list[str]] = {}
    if "dense" in modes:
        q = np.asarray(embedder.embed_query(query), dtype=np.float32)
        q /= float(np.linalg.norm(q)) or 1.0
        matrix = np.frombuffer(b"".join(r["vector"] for r in rows), dtype=np.float32)
        cosine = matrix.reshape(len(rows), -1) @ q
        rankings["dense"] = [keys[i] for i in np.argsort(-cosine, kind="stable")]
    keyword = _bm25(conn, where, params, tokenize(query)) if "bm25" in modes else {}
    if keyword:
        rankings["bm25"] = sorted(keyword, key=lambda key: (-keyword[key], key))

    best: list[tuple[int, int, float]] = []
    seen: set[int] = set()
    for key, score in rrf(rankings):
        entry_id, seq = (int(x) for x in key.split(":"))
        if entry_id not in seen:
            seen.add(entry_id)
            best.append((entry_id, seq, score))
            if len(best) == k:
                break
    hits = []
    for entry_id, seq, score in best:
        row = conn.execute(
            "SELECT c.text AS chunk, e.board, e.kind, e.title, e.author_name, e.session, "
            "ch.name AS character_name FROM chunks c JOIN entries e ON e.id = c.entry_id "
            "LEFT JOIN characters ch ON ch.id = e.character_id "
            f"WHERE c.entry_id = ? AND c.seq = ? AND {where}",
            [entry_id, seq, *params],
        ).fetchone()
        hits.append(
            NoteHit(
                entry_id=entry_id,
                source=source_label(row),
                text=row["chunk"],
                canon=row["board"] == "lore",
                score=score,
            )
        )
    return hits
