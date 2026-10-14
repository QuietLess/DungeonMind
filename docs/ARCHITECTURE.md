# DungeonMind — Architecture

## Stack and why

| Choice | Why |
| --- | --- |
| Python 3.12, `uv` | Standard for ML/AI work; `uv` gives fast, locked installs |
| PyMuPDF | Font-level access to the SRD PDF, needed for heading-aware chunking |
| fastembed (`bge-small-en-v1.5`) | Free local ONNX embeddings, no GPU or API key; best on our benchmark |
| Qdrant (embedded mode) | Vector store with payload filters; runs as a folder now, a server in M8 without code changes |
| rank-bm25 | Lexical baseline and hybrid option |
| Open5e v2 snapshot (`srd-2024`) | Structured spell/creature data, pinned to the 2024 SRD |
| Anthropic SDK, `claude-opus-5-5` | Answer generation, routing, tool use; structured JSON outputs |
| LangGraph | Explicit, bounded state machine for orchestration (blueprint §5) |
| pytest, ruff | Tests and lint |

## Components

```
src/dungeonmind/
  rag/ingest.py      SRD PDF → heading-aware chunks with page provenance
  rag/lexical.py     BM25 index
  rag/dense.py       fastembed + Qdrant index
  rag/hybrid.py      BM25 / dense / RRF fusion / rerank → Evidence
  rag/answer.py      citation-constrained answer generation + validation
  tools/catalog.py   spell & monster search, get_entity (Open5e snapshot)
  tools/dice.py      dice expressions, exact distributions
  tools/probability.py  D20 Test odds (2024 rules)
  tools/encounter.py XP budgets, difficulty, SRD warnings
  tools/registry.py  allow-listed tools, typed schemas, error → {"error": …}
  graph/state.py     orchestrator contract (state, intents, LLM port)
  graph/build.py     LangGraph graph and run_chat()          (M4)
  graph/llm.py       ClaudeLLM: the real LLM port            (M4)
  evals/             retrieval benchmark
  cli.py             developer CLI
```

## Key decisions

| Decision | Reason | Revisit when |
| --- | --- | --- |
| Dense retrieval is the default, not hybrid + rerank | v0 benchmark: dense R@5 0.91 vs hybrid 0.84–0.86; rerankers (MS MARCO) no gain at 1–4 s | 200+ question set (M7), after query rewriting |
| Chapters from 26 pt titles, not the PDF outline | Outline page numbers are off by one | — |
| Open5e used as a local snapshot, not live | Fast, offline, reproducible tests/evals | Snapshot refresh via `sync-open5e` |
| Tool errors returned as data | The model can read and retry; the loop never crashes on bad arguments | — |
| Router + bounded tool loop, single orchestrator | Blueprint §8: no agent-per-box; specialised nodes only where they help | M5 adds a planner for encounters |
| LLM behind a small port (`LLM` protocol) | Graph logic is tested offline with a scripted fake; only `ClaudeLLM` touches the API | — |

## M4 contract — orchestrator behaviour

Public API (`dungeonmind.graph.build`):

```python
@dataclass
class Deps:
    llm: LLM                                   # graph/state.py
    retrieve: Callable[[str], list[Evidence]]  # question/query → ranked evidence
    registry: ToolRegistry                     # tools/registry.py
    max_steps: int = 6                         # agent turns before stopping
    timeout_s: float = 60.0                    # wall-clock budget for the agent loop
    clock: Callable[[], float] = time.monotonic

def build_graph(deps: Deps) -> CompiledGraph
def run_chat(question: str, deps: Deps, conversation_id: str | None = None,
             campaign_id: str | None = None) -> ChatState
```

Behaviour (each line is an acceptance criterion with a test):

1. **Route.** `llm.route(question)` sets `intent`, `route_reason`, `other_edition`.
2. **Rules Q&A** (`rules_qa`): `retrieve(question)` → `evidence`; then
   `llm.answer(question, evidence)` → final answer. The agent loop is not used.
3. **Tool intents** (`lookup`, `probability`, `encounter`): the agent loop runs.
   Nothing is retrieved up front. Tools offered = `registry.definitions()` plus
   `search_rules` (`{"query": str}`), which calls `retrieve(query)`, appends the
   results to `evidence` (continuing E-numbering) and returns
   `[{"id": "E#", "citation": …, "text": …}]`.
4. **Agent loop.** `messages` starts as `[{"role": "user", "content": question}]`.
   Each `llm.agent_step(messages, tools)` turn either returns tool calls or a final
   answer. Tool calls run through `registry.call` (or `search_rules`), each recorded
   as a `ToolRecord` with the next T-id. Then the assistant turn (`raw_content`) and
   **one** user message holding every `tool_result` block are appended: for each call
   `{"type": "tool_result", "tool_use_id": call_id, "content": <json of {"id": "T#", **output}>,
   "is_error": <bool>}`.
5. **Bounded.** After `max_steps` turns without a final answer, stop with
   `stop_reason="max_steps"`. If `clock()` exceeds the start plus `timeout_s` before
   a turn, stop with `"timeout"`. Both give an honest "couldn't finish" answer,
   `confidence="low"`, `insufficient_evidence=True`. Otherwise `"completed"`.
6. **Errors are data.** A tool error (`{"error": …}`) goes back to the model with
   `is_error: true`, and the loop continues. An exception from the LLM gives
   `stop_reason="error"` and a short apology answer; `run_chat` does not raise.
7. **Unsupported** (`campaign_memory`, `session_prep`): no LLM call beyond routing.
   The answer says campaign memory arrives in a later milestone,
   `insufficient_evidence=True`.
8. **Decline** (`out_of_scope`): no LLM call beyond routing; a short answer saying
   DungeonMind covers running D&D with the 2024 SRD.
9. **Verify** (every path): valid ids are `E1…E{len(evidence)}` and
   `T1…T{len(tool_calls)}`. Cited ids come from `FinalAnswer.citations` plus inline
   `[E3]` / `[T2]` / `[E1, T2]` markers in the answer. Invalid ids are removed from the
   text and the list and added to `violations`. An answered (not insufficient)
   rules or tool response with no valid citations gets `violations += ["no-citations"]`
   and `confidence="low"`. `citations` holds `CitationRef`s: kind `srd` with the
   chunk's citation, or kind `tool` labelled `"<tool name> tool"`.
10. **Other editions.** If `other_edition` is true, the answer starts with a note that
    DungeonMind covers the 2024 rules (SRD 5.2.1) only.
11. **Trace.** Every node appends a `TraceEvent`. Node names: `route`, `retrieve`,
    `answer`, `agent`, `unsupported`, `decline`, `verify`.
12. **Untrusted text.** Evidence and tool output are data. The Claude adapter's system
    prompts say so; the graph never changes its control flow based on their text.

Clarifications (settled after the first test pass):

- The assistant turn is appended as `{"role": "assistant", "content": raw_content}`.
- `search_rules` is recorded like any tool: output `{"result": [hits]}`, so its
  `tool_result` content is `{"id": "T#", "result": [...]}`.
- The `agent` node may log one trace event per turn.
- An unknown tool name is a tool error (via `registry.call`), not an exception.
- An exception from `llm.route()` also gives `stop_reason="error"`; like every path,
  the run still ends in `verify`.
- `stop_reason` is `"completed"` for successful rules_qa, tool, unsupported and decline runs.
- Decline, unsupported and error answers have `confidence="low"` and
  `insufficient_evidence=True`.
- The other-edition note (behaviour 10) applies to successful rules_qa and tool answers
  only, not to error, timeout, max_steps, decline or unsupported answers.
- The run reads `clock()` once at the start of the agent loop and again before each turn.
- `citations` lists each id once, in order of first appearance (list, then inline markers).
- The timeout check runs before every agent turn, including the first.
