# DungeonMind

**An agentic RAG Game Master copilot for D&D (SRD 5.2.1 / 2024 rules).**

DungeonMind answers rules questions with citations, looks up spells and monsters
from structured data, does dice and encounter maths with deterministic tools, and
keeps campaign notes isolated per campaign. It is a grounded, tool-using assistant,
not a chat-with-a-PDF demo.

## Status

| Milestone | State |
| --- | --- |
| M1 — Corpus: SRD 5.2.1 ingestion with page/section provenance | ✅ |
| M2 — Hybrid retrieval, benchmark, cited answers | 🟡 retrieval + benchmark done; answers need an API key |
| M3 — Structured tools (dice ✅, probability ✅, Open5e, encounter budget) | 🟡 |
| M4 — LangGraph orchestrator with bounded workflows | ⬜ |
| M5 — Encounter builder | ⬜ |
| M6 — Isolated campaign memory | ⬜ |
| M7 — 200+ question eval suite and CI regression gates | ⬜ |
| M8 — FastAPI, Docker, tracing, metrics, CI/CD | ⬜ |

## Quickstart

```bash
uv sync
uv run python scripts/download_srd.py      # fetches the CC-BY SRD 5.2.1 PDF
uv run dungeonmind ingest                   # → data/processed/srd_5.2.1_chunks.jsonl
uv run dungeonmind index                    # embeds chunks into a local Qdrant index (~2.5 min)
uv run dungeonmind search "can I grab an enemy so it can't run away?"
uv run dungeonmind eval-retrieval           # Recall@K / MRR for every retrieval mode
uv run pytest
```

Cited answers need an Anthropic API key (never commit it; `.env` is git-ignored):

```powershell
setx ANTHROPIC_API_KEY "sk-ant-..."         # Windows; open a new terminal afterwards
uv run dungeonmind ask "How does grappling work?" --show-evidence
```

## Retrieval benchmark (v0, 43 questions)

`src/dungeonmind/evals/datasets/retrieval_v0.jsonl` has exact-term, paraphrase,
entity and multi-section questions, each labelled with the SRD sections that answer
it, plus unanswerable questions for the abstention eval.

| Mode | Recall@1 | Recall@5 | MRR | p50 latency |
| --- | --- | --- | --- | --- |
| BM25 | 0.51 | 0.65 | 0.58 | 1 ms |
| **Dense (bge-small-en-v1.5)** | **0.72** | **0.91** | **0.80** | 6 ms |
| Hybrid, RRF (BM25 weight 1.0) | 0.58 | 0.84 | 0.70 | 8 ms |
| Hybrid, RRF (BM25 weight 0.3) | 0.67 | 0.86 | 0.75 | 9 ms |
| Dense + MiniLM-L12 rerank | 0.58 | 0.88 | 0.71 | 1.4 s |
| Dense + bge-reranker-base | 0.60 | 0.88 | 0.72 | 3.9 s |

Dense retrieval alone is the default. BM25 is strong on exact rules terms
(MRR 0.92) but weak on how players phrase things (paraphrase MRR 0.20), and an
equal-weight fusion lets it outvote the dense ranking. The off-the-shelf rerankers
are trained on web search (MS MARCO) and don't transfer to rules text. At n=43 one
question moves a score by about 0.02, so these are re-run on the 200+ question set
(M7) and after LLM query rewriting.

## Citation contract

`dungeonmind ask` sends the retrieved chunks to Claude as numbered evidence blocks
(`E1`, `E2`, …) and requests JSON output: answer, cited ids, confidence and an
`insufficient_evidence` flag. Code then validates the result. Ids that weren't
supplied are removed from the text and logged as violations, uncited answers are
downgraded to low confidence, and evidence text is treated as data rather than
instructions (preparation for user-uploaded campaign notes).

## How ingestion works

The SRD PDF has a consistent typographic hierarchy, so chunks follow the document's
own structure rather than fixed token windows:

- 26 / 18 / 14 / 12 pt GillSans-SemiBold → chapter / section / subsection / entry
  (spells, glossary terms); 14.8 pt → stat block.
- Two-column reading order, with the running footer removed and table rows kept as
  `cell | cell` lines.
- Each chunk carries `heading_path`, `page_start`/`page_end`, `entity_type` and
  `edition`, so every answer can cite e.g.
  *SRD 5.2.1, Rules Glossary › Rules Definitions › Grappled [Condition], p. 182*.
- Chapter boundaries come from the 26 pt titles. The PDF outline's page numbers
  are off by one, so the outline isn't used.

## Layout

```
src/dungeonmind/
  rag/        ingestion, BM25, dense (Qdrant), RRF fusion, reranking, cited answers
  tools/      dice expressions, D20 Test probabilities
  schemas/    Chunk and shared models
  graph/      LangGraph state and nodes (M4)
  memory/     per-campaign memory (M6)
  evals/      retrieval benchmark dataset and runner
scripts/      data acquisition
tests/        unit + fixture tests against the real SRD
```

## Legal

This work includes material from the System Reference Document 5.2.1 ("SRD 5.2.1")
by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1
is licensed under the Creative Commons Attribution 4.0 International License,
available at https://creativecommons.org/licenses/by/4.0/legalcode.

DungeonMind is an unofficial project, compatible with fifth edition. Only SRD content
is ingested; no other rulebooks are included.
