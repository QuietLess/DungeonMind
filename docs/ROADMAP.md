# DungeonMind — Roadmap

| Milestone | Deliverable | Exit criteria | State |
| --- | --- | --- | --- |
| M1 Corpus | SRD 5.2.1 ingestion with metadata | Searchable chunks with page/section provenance | ✅ |
| M2 RAG baseline | Retrieval modes, benchmark, cited answers | Benchmark + initial Recall@K | ✅ |
| M3 Structured tools | Open5e search, dice, probability, encounter budget | Tool schemas + tests | ✅ |
| M4 Orchestrator | Intent routing + bounded LangGraph workflow, `chat` CLI | No uncontrolled agent loop; contract tests pass | ✅ |
| M5 Encounter builder | End-to-end planning workflow | Repeatable, cited, constraint-aware results | ⬜ |
| M6 Campaign memory | Isolated campaign RAG/state | No cross-campaign retrieval in tests | ⬜ |
| M7 Evaluation | 200+ question suite + regression gates | Metrics tracked per version | ⬜ |
| M8 Production | FastAPI, Docker, tracing, metrics, CI/CD | Deployable demo + monitored runs | ⬜ |

## Done

- **M1:** 2,036 heading-aware chunks; fixes for off-by-one outline pages, summoned
  stat blocks parenting spells, and small-caps headings.
- **M2:** BM25 / dense / hybrid / rerank compared on a 43-question benchmark.
  Dense is the default (R@5 0.91, MRR 0.80). Citation-constrained answers with
  code-side validation; correct abstention on out-of-SRD questions.
- **M3:** 339 spells and 331 creatures (Open5e `srd-2024`) with PDF page links;
  2024 encounter budgets verified against the SRD examples and all stat blocks;
  six tools behind a typed registry.

- **M4:** `dungeonmind chat` routes each question (rules / lookup / probability / encounter /
  campaign memory / session prep / out of scope) through a LangGraph DAG. Rules questions use
  retrieve + cited answer; tool questions use a tool loop bounded by step, call and time limits.
  Every answer passes a citation check. Built test-first: 61 contract tests written before the
  code, then backend and security review. The fix batch (dice limits, input/output caps,
  per-call timeouts, failed-tool and malformed-citation checks, evidence escaping, terminal
  sanitising, linear-time citation scan) also went in test-first. 293 tests; coverage of the
  M4 code: llm.py 100%, build.py 96%.

## Tracked follow-ups (from M4 security review, accepted as LOW for a local CLI)

| Id | Item | Do by |
| --- | --- | --- |
| N2 | Total tool-output budget per run (input cost grows with turns × output size) | M8 |
| N3 | One wall-clock deadline across route + agent; deadline-aware retries; timeout for `ask` | M8 |
| N4 | Check the deadline between tool calls within a turn (≤ ~8 s today) | M8 |
| M6-a | Escape evidence in every formatter incl. user notes; label note citations separately; re-run forged-block probe | M6 |
| M6-b | Clean `ask --show-evidence` and `search` output | M6 |
| M8-a | Per-user rate and spend limits; server-side `max_steps`; request size limit | M8 |
| M8-b | Hide exception/trace text from API clients; sanitise rendered answers, strip bidi characters | M8 |
| Q1 | `"` in evidence text is escaped as `&quot;` (only the source attribute needs it); the model may echo it | when convenient |

## Known limitations

- Occasional out-of-order sentence near PDF sidebars; scrambled links on the legal page.
- No monster habitat data in the 2024 SRD, so environment can't be a hard filter.
- When abstaining, `ask` still lists the irrelevant evidence it checked as citations.
  `chat` uses the same citation check, so this applies to both.
- Retrieval benchmark is small (n=43); differences under ~0.05 are noise.
- Routing adds one model call (about 1.7–4.8 s observed) before any work starts.
- Retrieval sometimes misses a relevant rule for unusual phrasings (e.g. a 2014-framed
  grappling question didn't retrieve Unarmed Strike).

## Next

- M5 encounter builder.
