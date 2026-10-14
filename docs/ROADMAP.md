# DungeonMind — Roadmap

| Milestone | Deliverable | Exit criteria | State |
| --- | --- | --- | --- |
| M1 Corpus | SRD 5.2.1 ingestion with metadata | Searchable chunks with page/section provenance | ✅ |
| M2 RAG baseline | Retrieval modes, benchmark, cited answers | Benchmark + initial Recall@K | ✅ |
| M3 Structured tools | Open5e search, dice, probability, encounter budget | Tool schemas + tests | ✅ |
| M4 Orchestrator | Intent routing + bounded LangGraph workflow, `chat` CLI | No uncontrolled agent loop; contract tests pass | 🔨 |
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

## Known limitations

- Occasional out-of-order sentence near PDF sidebars; scrambled links on the legal page.
- No monster habitat data in the 2024 SRD, so environment can't be a hard filter.
- When abstaining, `ask` still lists the irrelevant evidence it checked as citations
  (M4's verify step handles this uniformly).
- Retrieval benchmark is small (n=43); differences under ~0.05 are noise.

## Next

- M4 (in progress), then M5 encounter builder.
