"""DungeonMind developer CLI."""

from __future__ import annotations

import collections
from pathlib import Path
from typing import Annotated

import typer

from dungeonmind.rag.dense import DEFAULT_INDEX_PATH, DenseIndex
from dungeonmind.rag.hybrid import DEFAULT_RERANKER, HybridRetriever, Mode, Reranker
from dungeonmind.rag.ingest import ingest as ingest_pdf
from dungeonmind.rag.ingest import read_jsonl, write_jsonl
from dungeonmind.schemas import EntityType

app = typer.Typer(no_args_is_help=True, add_completion=False)

DEFAULT_PDF = Path("data/raw/srd/SRD_CC_v5.2.1.pdf")
DEFAULT_CHUNKS = Path("data/processed/srd_5.2.1_chunks.jsonl")


@app.command()
def ingest(
    pdf: Path = DEFAULT_PDF,
    out: Path = DEFAULT_CHUNKS,
) -> None:
    """Parse the SRD PDF into heading-aware chunks (JSONL)."""
    if not pdf.exists():
        raise typer.BadParameter(f"{pdf} not found — run scripts/download_srd.py first")
    chunks = ingest_pdf(pdf)
    write_jsonl(chunks, out)
    by_type = collections.Counter(c.entity_type.value for c in chunks)
    words = [len(c.text.split()) for c in chunks]
    typer.echo(f"Wrote {len(chunks)} chunks to {out}")
    typer.echo(
        f"  words/chunk: min={min(words)} median={sorted(words)[len(words) // 2]} max={max(words)}"
    )
    for name, n in by_type.most_common():
        typer.echo(f"  {name:<11} {n}")


@app.command()
def index(chunks: Path = DEFAULT_CHUNKS, path: Path = DEFAULT_INDEX_PATH) -> None:
    """Embed chunks and (re)build the local Qdrant vector index."""
    import time

    items = read_jsonl(chunks)
    started = time.perf_counter()
    DenseIndex(path).build(items)
    typer.echo(f"Indexed {len(items)} chunks in {time.perf_counter() - started:.1f}s → {path}")


def load_retriever(mode: Mode, chunks: Path = DEFAULT_CHUNKS) -> HybridRetriever:
    return HybridRetriever(
        read_jsonl(chunks),
        dense=None if mode is Mode.BM25 else DenseIndex(DEFAULT_INDEX_PATH),
        reranker=Reranker() if mode in (Mode.HYBRID_RERANK, Mode.DENSE_RERANK) else None,
    )


@app.command()
def ask(
    question: str,
    k: Annotated[int, typer.Option("-k", "--top-k")] = 6,
    mode: Mode = Mode.DENSE,
    show_evidence: bool = False,
) -> None:
    """Answer a rules question from retrieved SRD evidence, with citations."""
    import anthropic

    from dungeonmind.rag.answer import AnswerGenerator

    evidence = load_retriever(mode).retrieve(question, k=k, mode=mode)
    if show_evidence:
        for i, ev in enumerate(evidence, start=1):
            typer.echo(f"  E{i}  {ev.chunk.citation}")
        typer.echo()
    no_key = "No valid Anthropic credentials. Set ANTHROPIC_API_KEY (see README)."
    try:
        answer = AnswerGenerator().answer(question, evidence)
    except anthropic.AuthenticationError as e:
        raise typer.BadParameter(no_key) from e
    except TypeError as e:  # the SDK raises this when no credential is configured at all
        if "authentication method" not in str(e):
            raise
        raise typer.BadParameter(no_key) from e

    typer.echo(answer.text + "\n")
    label = "insufficient evidence" if answer.insufficient_evidence else answer.confidence
    typer.secho(f"Confidence: {label}", bold=True)
    for c in answer.citations:
        typer.echo(f"  [{c.evidence_id}] {c.citation}")
    if answer.violations:
        typer.secho(f"Citation contract violations removed: {answer.violations}", fg="yellow")
    typer.echo(
        f"\n{answer.model} · {answer.usage['input_tokens']} in / "
        f"{answer.usage['output_tokens']} out tokens · {answer.latency_ms:.0f} ms"
    )


@app.command()
def search(
    query: str,
    k: Annotated[int, typer.Option("-k", "--top-k")] = 5,
    mode: Mode = Mode.DENSE,
    entity: Annotated[list[EntityType] | None, typer.Option(help="Filter by entity type")] = None,
    chunks: Path = DEFAULT_CHUNKS,
    full: bool = False,
) -> None:
    """Search the SRD (BM25, dense, hybrid, or hybrid + rerank)."""
    retriever = load_retriever(mode, chunks)
    hits = retriever.retrieve(query, k=k, mode=mode, entity_types=set(entity) if entity else None)
    for ev in hits:
        ranks = " ".join(f"{name}={r}" for name, r in ev.ranks.items())
        typer.secho(f"[{ev.rank}] {ev.score:7.3f}  {ev.chunk.chunk_id}   ({ranks})", bold=True)
        typer.echo(f"     {ev.chunk.citation}")
        body = ev.chunk.text if full else ev.chunk.text[:300].replace("\n", " ") + "…"
        typer.echo(f"     {body}\n")


@app.command("sync-open5e")
def sync_open5e() -> None:
    """Download the SRD 5.2 (2024) spell and creature snapshot from Open5e."""
    from dungeonmind.tools.open5e import sync

    for kind, n in sync().items():
        typer.echo(f"  {kind:<10} {n}")


@app.command()
def tool(
    name: Annotated[str, typer.Argument(help="Tool name, or 'list' to show all tools")],
    arguments: Annotated[str, typer.Argument(help="JSON arguments")] = "{}",
) -> None:
    """Call a structured tool directly, e.g. tool search_spells '{"level": 3}'."""
    import json

    from dungeonmind.tools.catalog import Catalog
    from dungeonmind.tools.registry import ToolRegistry

    registry = ToolRegistry(Catalog.load(chunks=read_jsonl(DEFAULT_CHUNKS)))
    if name == "list":
        for d in registry.definitions():
            typer.secho(d["name"], bold=True)
            typer.echo(f"  {d['description']}")
        return
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError as e:
        raise typer.BadParameter(f"arguments must be JSON: {e}") from e
    typer.echo(json.dumps(registry.call(name, args), indent=2, ensure_ascii=False))


@app.command("eval-retrieval")
def eval_retrieval(
    modes: Annotated[list[Mode] | None, typer.Option("--mode")] = None,
    k: int = 10,
    chunks: Path = DEFAULT_CHUNKS,
    out: Path | None = None,
    show_misses: bool = False,
    bm25_weight: float = 1.0,
    reranker: str = DEFAULT_RERANKER,
) -> None:
    """Benchmark retrieval modes: Recall@K and MRR per question type."""
    import json

    from dungeonmind.evals import retrieval

    items = read_jsonl(chunks)
    questions = retrieval.load()
    if problems := retrieval.validate(questions, items):
        raise typer.BadParameter("dataset references missing sections:\n" + "\n".join(problems))

    modes = modes or list(Mode)
    needs_rerank = {Mode.HYBRID_RERANK, Mode.DENSE_RERANK} & set(modes)
    retriever = HybridRetriever(
        items,
        dense=DenseIndex(DEFAULT_INDEX_PATH),
        reranker=Reranker(reranker) if needs_rerank else None,
        weights={"bm25": bm25_weight},
    )
    report = {}
    for mode in modes:
        results = retrieval.run(retriever, questions, mode, k=k)
        summary = retrieval.summarize(results)
        report[mode.value] = summary
        o = summary["overall"]
        typer.secho(
            f"{mode.value:<14} R@1={o['recall@1']:.2f} R@3={o['recall@3']:.2f} "
            f"R@5={o['recall@5']:.2f} R@10={o['recall@10']:.2f} MRR={o['mrr']:.3f} "
            f"p50={summary['latency_ms']['p50']:.0f}ms",
            bold=True,
        )
        for qtype, m in summary["by_type"].items():
            typer.echo(
                f"    {qtype:<11} n={m['n']:<3} R@1={m['recall@1']:.2f} "
                f"R@5={m['recall@5']:.2f} MRR={m['mrr']:.3f}"
            )
        if show_misses:
            for r in results:
                if not r.first_relevant_rank or r.first_relevant_rank > 5:
                    typer.echo(f"    MISS {r.question.id} {r.question.query!r} → {r.retrieved[:3]}")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        typer.echo(f"Wrote {out}")


if __name__ == "__main__":
    app()
