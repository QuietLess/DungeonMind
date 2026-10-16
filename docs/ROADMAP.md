# DungeonMind — Roadmap

| Milestone | Deliverable | Exit criteria | State |
| --- | --- | --- | --- |
| M1 Corpus | SRD 5.2.1 ingestion with metadata | Searchable chunks with page/section provenance | ✅ |
| M2 RAG baseline | Retrieval modes, benchmark, cited answers | Benchmark + initial Recall@K | ✅ |
| M3 Structured tools | Open5e search, dice, probability, encounter budget | Tool schemas + tests | ✅ |
| M4 Orchestrator | Intent routing + bounded LangGraph workflow, `chat` CLI | No uncontrolled agent loop; contract tests pass | ✅ |
| M5 Encounter builder | End-to-end planning workflow | Repeatable, cited, constraint-aware results | ✅ |
| M6 Campaign hub backend | FastAPI: accounts, campaigns, invites, notes with visibility, per-campaign storage, campaign-aware chat | Visibility matrix + cross-campaign tests pass; the model never receives an unreadable note | ⬜ |
| M7 Campaign hub UI | React (Vite, TS): login, campaigns, notes, chat with linked citations; thread context | Main journeys work in the browser for DM and player; frontend + accessibility review | ⬜ |
| M8 Evaluation | 200+ question suite (rules + campaign notes) + regression gates; model choice per step | Metrics tracked per version | ⬜ |
| M9 Production | Docker, hosting + TLS, tracing, metrics, CI/CD | Deployable demo + monitored runs | ⬜ |

Direction change (2026-10-16): DungeonMind becomes a campaign hub — a web chat UI plus notes
that are private, shared with the DM, or visible to the party. Old M6–M8 became M6–M9, and
items previously due "M8" are now due M9.

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
  sanitising, linear-time citation scan) also went in test-first. 294 tests; coverage of the
  M4 code: build.py and llm.py 100%; tools 95–100% except the network-only Open5e sync (32%).

- **M5:** encounter requests go to a dedicated node. Claude extracts the request (and asks when
  party levels are missing); a deterministic builder picks 2–3 options inside the requested
  2024 difficulty band with role variety and SRD page citations; Claude writes them up.
  `--conversation` remembers the request so "make it harder/easier" revises it. Built
  test-first. Backend review checked 8,700+ generated requests against the rules (0
  violations) and against an uncapped search, which found two ranking gaps (name cap, class
  cap), both fixed with regression tests. Security review covered the file store (no path
  traversal; damaged-file handling, backups and temp-file cleanup added). Two presentation
  bugs from the live check (revisions not explained; defaults described as the user's
  request) are fixed. 474 tests; M5 code coverage 99% (builder 100%).

## Tracked follow-ups (from M4 security review, accepted as LOW for a local CLI)

| Id | Item | Do by |
| --- | --- | --- |
| N2 | Total tool-output budget per run (input cost grows with turns × output size) | M9 |
| N3 | One wall-clock deadline across route + agent; deadline-aware retries; timeout for `ask` | M9 |
| N4 | Check the deadline between tool calls within a turn (≤ ~8 s today) | M9 |
| M6-a | Escape evidence in every formatter incl. user notes; label note citations separately; re-run forged-block probe | M6 |
| M6-b | Clean `ask --show-evidence` and `search` output | M6 |
| M8-a | Per-user rate and spend limits; server-side `max_steps`; request size limit | M9 |
| M8-b | Hide exception/trace text from API clients; sanitise rendered answers, strip bidi characters | M9 |
| Q1 | `"` in evidence text is escaped as `&quot;` (only the source attribute needs it); the model may echo it | when convenient |
| Q2 | Truncate very long invalid citation ids in `violations` (a 20k-digit id is reported in full) | when convenient |
| S3 | Cap the conversation store's size or evict old entries | M9 |
| S4 | Scope conversation keys to a user/session with server-generated ids (shared namespace today) | M6 (hub: per-thread state) |
| S5 | Locking for simultaneous writers to the conversation store (lost updates possible) | M9 |
| S6 | Conversation store keeps one `.bak`; a second damage event overwrites it (timestamped backups) | M9 |
| S7 | Unlink an existing `.bak` before copying (a pre-placed symlink would be followed) | M9 |

## Known limitations

- Occasional out-of-order sentence near PDF sidebars; scrambled links on the legal page.
- No monster habitat data in the 2024 SRD, so environment can't be a hard filter.
- When abstaining, `ask` still lists the irrelevant evidence it checked as citations.
  `chat` uses the same citation check, so this applies to both.
- Retrieval benchmark is small (n=43); differences under ~0.05 are noise.
- Routing adds one model call (about 1.7–4.8 s observed) before any work starts.
- An encounter build takes about 20 s and 8–10¢ on Opus (extraction plus a long write-up).
  Model choice per step is a pending decision (Haiku would be much cheaper; needs measuring).
- Encounter options may share their lower stat blocks; variety is only enforced on each
  option's highest-XP stat block. Tactics in the write-up are suggestions from roles, not rules.
- Roles are a heuristic from stat-block text ("Melee or Ranged" attacks count as ranged).
- Retrieval sometimes misses a relevant rule for unusual phrasings (e.g. a 2014-framed
  grappling question didn't retrieve Unarmed Strike).

## Next

- M6 campaign hub backend (contract in `ARCHITECTURE.md` › M6).
