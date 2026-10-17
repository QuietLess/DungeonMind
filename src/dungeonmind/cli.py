"""DungeonMind developer CLI."""

from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path
from typing import Annotated, Any

import typer

from dungeonmind.rag.dense import DEFAULT_INDEX_PATH, DenseIndex
from dungeonmind.rag.hybrid import DEFAULT_RERANKER, HybridRetriever, Mode, Reranker
from dungeonmind.rag.ingest import ingest as ingest_pdf
from dungeonmind.rag.ingest import read_jsonl, write_jsonl
from dungeonmind.schemas import EntityType

app = typer.Typer(no_args_is_help=True, add_completion=False)

DEFAULT_PDF = Path("data/raw/srd/SRD_CC_v5.2.1.pdf")
DEFAULT_CHUNKS = Path("data/processed/srd_5.2.1_chunks.jsonl")
CONVERSATIONS = Path("data/state/conversations.json")

NO_CREDENTIALS = "No valid Anthropic credentials. Set ANTHROPIC_API_KEY (see README)."
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


MODELS_HELP = (
    "Per-step models, e.g. 'route=claude-haiku-5-5,agent=claude-sonnet-5-5:low' "
    "(default: $DUNGEONMIND_MODELS)"
)


PROVIDER_HELP = "claude (paid API) or ollama (free local model); default: $DUNGEONMIND_PROVIDER"


def resolve_provider(value: str | None) -> str:
    """`--provider`, else $DUNGEONMIND_PROVIDER, else claude (M8b contract P1)."""
    from dungeonmind.graph.providers import check_provider

    try:
        return check_provider(value or os.environ.get("DUNGEONMIND_PROVIDER") or "claude")
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


def model_config(spec: str | None, provider: str = "claude"):
    """`--models`, else $DUNGEONMIND_MODELS, else the provider's default (M8 contract M2)."""
    from dungeonmind.graph.models import ModelConfig

    try:
        return ModelConfig.parse(
            spec if spec is not None else os.environ.get("DUNGEONMIND_MODELS"), provider=provider
        )
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


def require_backend(provider: str, config) -> None:
    """Stop before running when the chosen backend can't answer (P5)."""
    if provider == "claude":
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise typer.BadParameter(NO_CREDENTIALS)
        return
    import dungeonmind.graph.ollama as ollama

    problems = ollama.check_ollama(None, config)
    if problems:
        raise typer.BadParameter("\n".join(problems))


def clean_for_terminal(text: str) -> str:
    """Drop control characters (keeping newline and tab) so output can't drive the terminal."""
    return _CONTROL_CHARS.sub("", text)


def _say(text: str, **style: Any) -> None:
    """Print model-, tool- or exception-derived text, cleaned for the terminal."""
    typer.secho(clean_for_terminal(text), **style)


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
            _say(f"  E{i}  {ev.chunk.citation}")
        typer.echo()
    try:
        answer = AnswerGenerator().answer(question, evidence)
    except anthropic.AuthenticationError as e:
        raise typer.BadParameter(NO_CREDENTIALS) from e
    except TypeError as e:  # the SDK raises this when no credential is configured at all
        if "authentication method" not in str(e):
            raise
        raise typer.BadParameter(NO_CREDENTIALS) from e

    _say(answer.text + "\n")
    label = "insufficient evidence" if answer.insufficient_evidence else answer.confidence
    typer.secho(f"Confidence: {label}", bold=True)
    for c in answer.citations:
        _say(f"  [{c.evidence_id}] {c.citation}")
    if answer.violations:
        _say(f"Citation contract violations removed: {answer.violations}", fg="yellow")
    typer.echo(
        f"\n{answer.model} · {answer.usage['input_tokens']} in / "
        f"{answer.usage['output_tokens']} out tokens · {answer.latency_ms:.0f} ms"
    )


@app.command()
def chat(
    question: str,
    trace: Annotated[bool, typer.Option(help="Show routing, tool calls and timings")] = False,
    max_steps: Annotated[
        int, typer.Option(min=1, max=10, help="Agent tool turns before stopping")
    ] = 6,
    conversation: Annotated[
        str | None,
        typer.Option(help="Conversation name; remembers the last encounter for follow-ups"),
    ] = None,
    models: Annotated[str | None, typer.Option(help=MODELS_HELP)] = None,
    provider: Annotated[str | None, typer.Option(help=PROVIDER_HELP)] = None,
) -> None:
    """Ask anything: rules Q&A, lookups and odds, or building encounters, with citations."""
    import dungeonmind.graph.providers as providers
    from dungeonmind.graph.build import Deps, run_chat
    from dungeonmind.graph.store import JsonFileStore
    from dungeonmind.tools.catalog import Catalog
    from dungeonmind.tools.registry import ToolRegistry

    if conversation is not None and not 1 <= len(conversation.strip()) <= 100:
        raise typer.BadParameter("--conversation must be 1–100 characters")
    backend = resolve_provider(provider)
    config = model_config(models, backend)
    require_backend(backend, config)
    retriever = load_retriever(Mode.DENSE)
    chunks = list(retriever.by_id.values())
    # Without --conversation, Deps keeps its in-memory store and nothing persists.
    extra = {"store": JsonFileStore(CONVERSATIONS)} if conversation else {}
    deps = Deps(
        llm=providers.make_llm(backend, config),
        retrieve=lambda q: retriever.retrieve(q, k=6, mode=Mode.DENSE),
        registry=ToolRegistry(Catalog.load(chunks=chunks)),
        max_steps=max_steps,
        timeout_s=providers.provider_timeout(backend),
        **extra,
    )
    state = run_chat(question, deps, conversation_id=conversation.strip() if conversation else None)
    if state.get("error_type") == "AuthenticationError":  # run_chat never raises; ask doesn't
        raise typer.BadParameter(NO_CREDENTIALS)

    _say(state["answer"] + "\n")
    label = "insufficient evidence" if state["insufficient_evidence"] else state["confidence"]
    _say(f"Confidence: {label}", bold=True)
    for c in state["citations"]:
        _say(f"  [{c['id']}] {c['label']}")
    if state.get("violations"):
        _say(f"Citation contract violations removed: {state['violations']}", fg="yellow")
    _say(f"Stop reason: {state['stop_reason']}")

    if trace:
        _say("\nTrace", bold=True)
        if "intent" in state:
            _say(f"  route: {state['intent'].value} ({state['route_reason']})")
        for r in state.get("tool_calls", []):
            output = json.dumps(r["output"], ensure_ascii=False, default=str)
            short = output if len(output) <= 160 else output[:160] + "…"
            arguments = json.dumps(r["arguments"], ensure_ascii=False, default=str)
            _say(f"  {r['id']} {r['name']} {arguments}")
            _say(f"     → {short}")
        for e in state["trace"]:
            _say(f"  {e['node']:<11} {e['ms']:8.0f} ms  {e['detail']}")


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
        _say(f"[{ev.rank}] {ev.score:7.3f}  {ev.chunk.chunk_id}   ({ranks})", bold=True)
        _say(f"     {ev.chunk.citation}")
        body = ev.chunk.text if full else ev.chunk.text[:300].replace("\n", " ") + "…"
        _say(f"     {body}\n")


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
    _say(json.dumps(registry.call(name, args), indent=2, ensure_ascii=False))


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


# --- campaign hub (M6) ------------------------------------------------------------------------

hub_app = typer.Typer(no_args_is_help=True, help="Campaign hub administration.")
app.add_typer(hub_app, name="hub")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to bind; 0.0.0.0 shares on your network")] = (
        "127.0.0.1"
    ),
    port: int = 8000,
    root: Annotated[Path, typer.Option(help="Where the hub keeps its data")] = Path("data/hub"),
    daily_limit: Annotated[
        int, typer.Option(min=1, max=10_000, help="Bot messages per user per day")
    ] = 50,
    models: Annotated[str | None, typer.Option(help=MODELS_HELP)] = None,
    provider: Annotated[str | None, typer.Option(help=PROVIDER_HELP)] = None,
) -> None:
    """Run the campaign hub API (accounts, campaigns, boards, chat)."""
    import uvicorn

    from dungeonmind.hub.api import create_app
    from dungeonmind.hub.app import build_hub

    if host not in ("127.0.0.1", "localhost", "::1"):
        _say(
            f"Warning: serving on {host}. Anyone on your network can reach the hub, and traffic "
            "isn't encrypted (plain HTTP). Use it only on a network you trust.",
            fg="yellow",
        )
    backend = resolve_provider(provider)
    config = model_config(models, backend)
    try:
        require_backend(backend, config)
    except typer.BadParameter as e:  # the hub still works for notes; only the bot can't answer
        _say(f"Note: the chatbot can't answer yet. {e.message}", fg="yellow")
    hub = build_hub(root, daily_message_limit=daily_limit, models=config, provider=backend)
    # No access log: paths and query strings carry search text (F6).
    uvicorn.run(create_app(hub), host=host, port=port, log_level="info", access_log=False)


@hub_app.command("import-obsidian")
def import_obsidian(
    vault: Annotated[Path, typer.Argument(help="Your Obsidian vault folder (only read)")],
    dm: Annotated[str, typer.Option(help="Your hub username (register in the web app first)")],
    campaign_name: Annotated[str, typer.Option(help="Campaign to create and fill")],
    sessions: Annotated[
        Path | None, typer.Option(help="Folder of session notes (.md/.txt), only read")
    ] = None,
    preview: Annotated[
        bool, typer.Option(help="Show what would be imported; write nothing")
    ] = False,
    root: Annotated[Path, typer.Option(help="Where the hub keeps its data")] = Path("data/hub"),
) -> None:
    """Import an Obsidian vault (and session notes) as DM-private entries. Never changes VAULT."""
    import threading

    from dungeonmind.hub.app import NoteEmbedder
    from dungeonmind.hub.errors import HubError
    from dungeonmind.hub.obsidian import apply_import, plan_import
    from dungeonmind.hub.service import Hub

    for folder in filter(None, (vault, sessions)):
        if not folder.is_dir():
            raise typer.BadParameter(f"{folder} is not a folder")
    plan = plan_import(vault, sessions)
    for e in plan.entries:
        where = f"session {e.session}" if e.session else e.kind
        _say(f"  {e.board:<7} {where:<10} {e.visibility:<8} {len(e.body):>6}  {e.title}")
    for path, reason in plan.skipped:
        _say(f"  skipped  {path}  ({reason})", fg="yellow")
    lore = sum(e.board == "lore" for e in plan.entries)
    _say(
        f"{len(plan.entries)} entries ({lore} lore, {len(plan.entries) - lore} journal), "
        f"{len(plan.skipped)} skipped. Everything is private until you reveal it."
    )
    if preview:
        _say("Preview only: nothing was written.")
        return
    try:
        hub = Hub(root, embedder=NoteEmbedder(threading.Lock()))
        camp, count = apply_import(hub, plan, dm, campaign_name)
    except (ValueError, HubError) as e:
        raise typer.BadParameter(str(e)) from e
    _say(f"Imported {count} entries into {camp.name!r}. Open the hub and reveal what players know.")


@hub_app.command("reindex")
def reindex(
    campaign_id: str,
    root: Annotated[Path, typer.Option(help="Where the hub keeps its data")] = Path("data/hub"),
) -> None:
    """Re-embed a campaign's entries with the current note model (after a model change)."""
    import threading

    from dungeonmind.hub.app import NoteEmbedder
    from dungeonmind.hub.errors import HubError
    from dungeonmind.hub.service import Hub

    try:
        n = Hub(root, embedder=NoteEmbedder(threading.Lock())).reindex(campaign_id)
    except HubError as e:
        raise typer.BadParameter(str(e)) from e
    _say(f"Re-indexed {n} entries.")


@hub_app.command("reset-password")
def reset_password(
    username: str,
    root: Annotated[Path, typer.Option(help="Where the hub keeps its data")] = Path("data/hub"),
) -> None:
    """Set a new password for a hub user (signs them out everywhere)."""
    from dungeonmind.hub.errors import HubError
    from dungeonmind.hub.service import Hub

    password = typer.prompt("New password", hide_input=True, confirmation_prompt=True)
    try:
        Hub(root).reset_password(username, password)
    except HubError as e:
        raise typer.BadParameter(str(e)) from e
    _say(f"Password for {username} updated.")


@app.command("eval-notes")
def eval_notes(
    k: int = 5,
    out: Annotated[Path | None, typer.Option(help="Write the report as JSON")] = None,
) -> None:
    """Benchmark campaign-note search (dense / bm25 / hybrid) on the fixture campaign."""
    import json
    import threading

    from dungeonmind.evals import notes
    from dungeonmind.hub.app import NoteEmbedder

    report = notes.run(NoteEmbedder(threading.Lock()), k=k)
    for mode, rows in report.items():
        line = "  ".join(
            f"{t}: R@{k} {v[f'recall@{k}']:.2f} MRR {v['mrr']:.2f}"
            for t, v in rows.items()
            if t != "misses"
        )
        typer.echo(f"{mode:<7} {line}  misses: {', '.join(rows['misses']) or '-'}")
    if out:
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")


# --- evaluation (M8) ------------------------------------------------------------------------

eval_app = typer.Typer(
    no_args_is_help=True, help="The M8 evaluation suite: validate, offline, gate, live, compare."
)
app.add_typer(eval_app, name="eval")


def _srd_titles() -> set[str]:
    return {c.title for c in read_jsonl(DEFAULT_CHUNKS)}


def _validated_suite(
    categories: list[str] | None = None,
    limit: int | None = None,
    per_category: int | None = None,
):
    from dungeonmind.evals import suite
    from dungeonmind.evals.fixture import note_titles

    if not DEFAULT_CHUNKS.exists():
        raise typer.BadParameter(f"{DEFAULT_CHUNKS} not found — run `dungeonmind ingest` first")
    cases = suite.load()
    problems = suite.validate(cases, _srd_titles(), note_titles())
    return suite.select(cases, categories, per_category, limit), problems


def _offline_report():
    import tempfile
    import threading

    from dungeonmind.evals.fixture import build_fixture
    from dungeonmind.evals.offline import run_offline
    from dungeonmind.hub.app import NoteEmbedder
    from dungeonmind.tools.catalog import Catalog
    from dungeonmind.tools.registry import ToolRegistry

    cases, problems = _validated_suite()
    retriever = load_retriever(Mode.DENSE)
    registry = ToolRegistry(Catalog.load(chunks=list(retriever.by_id.values())))
    with tempfile.TemporaryDirectory() as root:
        fixture = build_fixture(Path(root), NoteEmbedder(threading.Lock()))
        report = run_offline(
            cases, fixture, retriever=retriever, modes=(Mode.DENSE,), registry=registry
        )
    report["valid"] = not problems
    report["problems"] = problems
    return report


@eval_app.command("validate")
def eval_validate() -> None:
    """Check the suite: unique ids, real SRD sections and notes, numbers that match the tools."""
    cases, problems = _validated_suite()
    for p in problems:
        _say(f"  {p}", fg="red")
    if problems:
        raise typer.Exit(1)
    _say(f"{len(cases)} cases, valid.")


def _print_offline(report: dict) -> None:
    for mode, m in report.get("srd", {}).items():
        _say(f"SRD retrieval ({mode}): R@5 {m['recall@5']:.2f}  MRR {m['mrr']:.2f}  (n={m['n']})")
    n = report["notes"]
    _say(f"Notes retrieval: R@5 {n['recall@5']:.2f}  MRR {n['mrr']:.2f}  (n={n['n']})")
    _say(f"Leak rate: {report['leak_rate']:.3f}  {report['leaks'] or ''}")
    c = report["contract"]
    _say(f"Contract check: {len(c['violations'])} violations over {c['n']} cases")
    for v in c["violations"][:10]:
        _say(f"  {v}", fg="red")


@eval_app.command("offline")
def eval_offline(
    label: Annotated[str | None, typer.Option(help="Save the report under evals/results/")] = None,
) -> None:
    """Retrieval, leak and citation-contract checks. No API calls."""
    from dungeonmind.evals.results import save

    report = _offline_report()
    _print_offline(report)
    if label:
        _say(f"Saved {save({**report, 'meta': {'kind': 'offline'}}, label)}")


@eval_app.command("gate")
def eval_gate(
    live: Annotated[Path | None, typer.Option(help="Also check a saved live result")] = None,
) -> None:
    """Regression gate: exits non-zero when any threshold in evals/gates.json is breached."""
    import json

    from dungeonmind.evals.results import gate, load_gates

    report = _offline_report()
    _print_offline(report)
    from dungeonmind.evals import suite

    live_report = json.loads(live.read_text(encoding="utf-8")) if live else None
    breaches = gate(report, load_gates(), live_report, suite_size=len(suite.load()))
    for b in breaches:
        _say(f"GATE: {b}", fg="red")
    if breaches:
        raise typer.Exit(1)
    _say("Gate passed.", fg="green")


@eval_app.command("live")
def eval_live(
    label: Annotated[str, typer.Option(help="Name for the saved result")] = "live",
    models: Annotated[str | None, typer.Option(help=MODELS_HELP)] = None,
    provider: Annotated[str | None, typer.Option(help=PROVIDER_HELP)] = None,
    max_cost: Annotated[
        float, typer.Option(min=0.01, max=100.0, help="Stop once spending reaches this (USD)")
    ] = 2.0,
    limit: Annotated[int | None, typer.Option(min=1, help="Run only the first N cases")] = None,
    per_category: Annotated[
        int | None,
        typer.Option(min=1, help="Run the first N cases of every category (a spread sample)"),
    ] = None,
    category: Annotated[list[str] | None, typer.Option(help="Only these categories")] = None,
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="Print the expected cost; no API calls")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="Run even if the estimate exceeds the budget")
    ] = False,
) -> None:
    """Run the suite against the real model. Spends money: estimates first, stops at --max-cost."""
    import tempfile
    import threading

    import dungeonmind.graph.providers as providers
    from dungeonmind.evals.fixture import build_fixture
    from dungeonmind.evals.live import estimate, make_runner, run_live
    from dungeonmind.evals.results import save
    from dungeonmind.hub.app import NoteEmbedder
    from dungeonmind.tools.catalog import Catalog
    from dungeonmind.tools.registry import ToolRegistry

    backend = resolve_provider(provider)
    config = model_config(models, backend)
    cases, problems = _validated_suite(category, limit, per_category)
    if problems:
        raise typer.BadParameter("the suite is invalid; run `dungeonmind eval validate`")
    expected = estimate(cases, config)
    _say(f"{len(cases)} cases, estimated cost ${expected:.2f} (budget ${max_cost:.2f}).")
    if estimate_only:
        return
    require_backend(backend, config)
    if expected > max_cost and not yes:
        raise typer.BadParameter(
            f"the estimate ${expected:.2f} exceeds --max-cost ${max_cost:.2f}; raise it, "
            "narrow with --limit/--category, or pass --yes to run until the budget stops it"
        )
    retriever = load_retriever(Mode.DENSE)
    registry = ToolRegistry(Catalog.load(chunks=list(retriever.by_id.values())))
    llm = providers.make_llm(backend, config)
    with tempfile.TemporaryDirectory() as root:
        fixture = build_fixture(Path(root), NoteEmbedder(threading.Lock()))
        runner = make_runner(
            fixture,
            llm,
            lambda q: retriever.retrieve(q, k=6, mode=Mode.DENSE),
            registry,
            timeout_s=providers.provider_timeout(backend),
        )
        report = run_live(cases, runner, max_cost=max_cost)
    report["meta"] = {
        "kind": "live",
        "provider": backend,
        "models": config.describe(),
        "max_cost": max_cost,
    }
    path = save(report, label)
    o = report["overall"]
    _say(
        f"Pass rate {o['pass_rate'] * 100:.1f}% ({o['passed']}/{o['n']}), "
        f"cost ${report['cost']['total']:.2f}, p50 {report['latency_ms']['p50']:.0f} ms"
    )
    for cat, row in sorted(report["categories"].items()):
        _say(f"  {cat:<14} {row['pass_rate'] * 100:5.1f}%  (n={row['n']})")
    if report["failures"]:
        _say("Why cases failed:")
        for reason, n in report["failures"].items():
            _say(f"  {n:>3}  {reason}")
    if report["stopped_early"]:
        _say(f"Budget reached: {len(report['not_run'])} cases not run.", fg="yellow")
    _say(f"Saved {path}")


@eval_app.command("compare")
def eval_compare(a: Path, b: Path) -> None:
    """Compare two saved results: pass rate per category, cost, latency."""
    import json

    from dungeonmind.evals.results import compare

    for line in compare(
        json.loads(a.read_text(encoding="utf-8")), json.loads(b.read_text(encoding="utf-8"))
    ):
        _say(line)
