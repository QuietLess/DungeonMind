# DungeonMind — Roadmap

| Milestone | Deliverable | Exit criteria | State |
| --- | --- | --- | --- |
| M1 Corpus | SRD 5.2.1 ingestion with metadata | Searchable chunks with page/section provenance | ✅ |
| M2 RAG baseline | Retrieval modes, benchmark, cited answers | Benchmark + initial Recall@K | ✅ |
| M3 Structured tools | Open5e search, dice, probability, encounter budget | Tool schemas + tests | ✅ |
| M4 Orchestrator | Intent routing + bounded LangGraph workflow, `chat` CLI | No uncontrolled agent loop; contract tests pass | ✅ |
| M5 Encounter builder | End-to-end planning workflow | Repeatable, cited, constraint-aware results | ✅ |
| M6 Campaign hub backend | FastAPI: accounts, campaigns, invites, boards with visibility, per-campaign storage, campaign-aware chat | Visibility matrix + cross-campaign tests pass; the model never receives an unreadable note | ✅ |
| M7 Campaign hub UI | React (Vite, TS): login, campaigns, notes, chat with linked citations; thread context | Main journeys work in the browser for DM and player; frontend + accessibility review | ✅ |
| M8 Evaluation | 200+ question suite (rules + campaign notes) + regression gates; model choice per step | Metrics tracked per version | ✅ offline · live run pending key |
| M9 Production | CI, Docker, backups; account management; UI for real campaigns; hosting + TLS, tracing | Deployable demo + monitored runs | 🔶 M9a–c done · hosting pending |

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

- **M6:** DungeonMind became a campaign hub (`dungeonmind serve`). Each campaign has a lore
  board (DM only, canon), a storyboard for each character, and a journal. Every entry is
  private, shared with the DM, or visible to the party. Each campaign lives in its own SQLite
  file. Search combines meaning and keyword matching, and the visibility filter is part of the
  SQL, so a user's chat never contains an entry they can't read. The graph gained a campaign
  node that cites notes as N ids next to SRD E ids, and canon conflicts are explained. Built
  test-first:
  - the visibility table checked across list, get, search and what the chat engine receives;
  - a recording fake model that never sees hidden text;
  - cross-campaign tests.

  The backend review found two HIGH issues: lost concurrent edits, and 3.7 s searches at the
  limit. The security review found no leaks, but limit and logging gaps. Both fix batches went
  in test-first (F1–F12), with a security re-review:
  - Keyword statistics now come from visible chunks only, which closes a side channel FTS5
    would have opened. Warm searches at the limit take 0.2–0.3 s.
  - Login limits are atomic and per client.
  - Password hashing runs at most 4 at a time.
  - Invite codes are no longer in URLs, and the access log is off.
  - Only failures that never reached the model are refunded.

  The notes benchmark (`eval-notes`, 20 questions) scores hybrid at R@5 1.00, MRR 0.93, and
  dense-only at R@5 0.97, MRR 0.93; n is small. 631 tests; M6 code coverage 96% (the
  production wiring in `hub/app.py` is checked by a live smoke run instead).

- **M7:** `dungeonmind serve` now serves a React web app (Vite, TypeScript) on the API's
  origin. It has sign-in, campaigns with invite codes, the lore board with Reveal, character
  storyboards, the journal, members, and chat. Chat citations open the note (visibility is
  checked again) or show the SRD source. Follow-ups are rewritten into standalone questions
  using the thread's last 3 exchanges.
  - Answers are rendered without HTML, links or images (closes H1). A strict CSP and security
    headers cover every response.
  - Node comes from a `uv` dev dependency, so nothing is installed system-wide.
  - Built test-first: Python tests for serving and thread context; Vitest + Testing Library
    for rendering, the API client, visibility, the drawer, chat and forms.
  - A real Chrome walk-through found two bugs, both fixed with regression tests: an effect
    that returned `scrollIntoView()` (now a Promise in Chrome) crashed chat, and a new chat
    lost its first question.
  - The frontend review found one CRITICAL bug, fixed: typing in a drawer lost focus after one
    letter. It also found HIGH chat thread mix-ups, no chat switching on phones, and drafts
    lost by a backdrop click. All fixed (D1–D4, C1–C7, F1–F3), and re-reviewed.
  - The security review found no leaks; its four LOWs are fixed (W1–W4) and re-reviewed.
  - 663 Python + 46 frontend tests.

- **M8:** A versioned 209-case suite (`suite_v1`) covers 11 categories, including campaign
  questions as a DM and as a player on a fixture campaign with secrets, follow-ups, and prompt
  injection.
  - `dungeonmind eval validate | offline | gate | live | compare`.
  - Offline, with no API calls: SRD R@5 0.87 / MRR 0.81, notes R@5 0.98 / MRR 0.88, leak rate
    0, and 0 citation-contract violations over 140 cases. `eval gate` passes and exits non-zero
    on any breach of `evals/gates.json`.
  - Live runner:
    - every case goes through the production engine (`Hub.ask` as its viewer, or `run_chat`);
    - per-case scores, per-step usage and cost;
    - a cost estimate up front (about $10 Opus-only, $0.26 Haiku-only), and a budget that
      stops between cases and inside a case.
  - Per-step models via `--models` / `$DUNGEONMIND_MODELS`. Haiku requests omit the refusal
    fallback, which Haiku doesn't support.
  - The backend + security review found that error answers could pass and that some intent
    labels contradicted the router. Both are fixed test-first (R1–R7), and the reviewer
    checked the fixes again.
  - 754 tests.
  - **Pending:** the live run and the model decision, which need the API key (see "Next").

- **M9a ops:** GitHub Actions CI (ruff, full pytest on freshly built SRD data, `eval validate`,
  offline `eval gate`, web type check/tests/build; no secrets, no paid calls); release workflow
  (image build on pull requests, GHCR publish only for a semver tag); Dockerfile (non-root,
  SRD data built in, hub data on a volume, health check) and compose (localhost only);
  `GET /api/health`; `hub backup` (SQLite online backup, private 0700/0600, never partial).
  Review found no CRITICAL/HIGH; the MEDIUMs (URI escaping, partial folders, Docker restore
  ownership) are fixed test-first. `data/hub/` is now git-ignored.
- **M9b accounts:** `/api/account` (usage, sessions), password change (other sessions end;
  wrong passwords count as failed sign-ins), sign out other devices, account deletion
  (private entries and chats removed in every campaign, shared entries kept; DMs delete their
  campaigns first). Account page in the web app.
- **M9c UI:** lore kind chips and the DM's secret/revealed filter, a 300 ms debounced text
  filter, notes rendered as safe Markdown, journal by session, chat rename/delete (a thread
  deleted mid-answer is a 404, not a 500).

- **M9d sharing:** `dungeonmind share` runs a free Cloudflare quick tunnel plus the hub
  (`--proxy cloudflare`: real client addresses from `CF-Connecting-IP`, trusted from loopback
  only; Secure cookie on forwarded HTTPS) with invite-only sign-up (`--signup invite`; the
  first account on an empty hub is the owner's). Checked end to end through a real tunnel.

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
| H1 | ~~Bot answers in the UI must not render remote images or auto-link URLs~~ (done in M7: UI3 + CSP) | ✅ M7 |
| U1 | Real bot answers haven't been seen in the UI yet (no API key in the dev shell); one live check with a key | testing phase |
| E1 | First live step: a 1-case smoke run per model (`eval live --limit 1 --models all=<model>`), checking that each model accepts the request shape (effort + JSON schema; Haiku without fallback) | testing phase |
| E2 | Re-fit `STEP_TOKENS` (the cost estimate) from the first live run's measured usage | testing phase |
| E3 | Leak check O3 inspects each entry's best chunk; a hidden string in another chunk of a long entry could be missed (fine for the fixture) | when convenient |
| U2 | Only one process can open the embedded SRD index: run `serve` or `chat`, not both | M9 |
| H2 | Behind a reverse proxy every client looks like 127.0.0.1: an explicit forwarded-IP setting, a global per-username login limit, IPv6 /64 grouping | M9 |
| H3 | The DM runs the server, so `campaign.db` is readable by them in plain text; "private" is enforced by the app, not cryptography. Document clearly, or add per-user encryption | M9 |
| H4 | Store the embedding model name and dimension per campaign; a model change today makes every search fail | M8 |
| A5 | Nobody can edit or delete what a deleted (or removed) player shared; the DM has no moderation for others' party entries | when convenient |
| A6 | Rename uses `window.prompt` (no length hint; a 422 shows in the conversation pane) | when convenient |
| A7 | An unusable campaign folder blocks account deletion until fixed (fail-safe; ops note) | when convenient |
| T1 | Hub owner = oldest account; if the owner deletes all campaigns and their account, ownership (who may invite new accounts) moves to the next account silently. Store an explicit owner instead | when convenient |
| T2 | First account on an empty hub isn't restricted to this machine (sign up before sharing the address); `__Host-` cookie prefix; HSTS on 500 responses | when convenient |
| H5 | Worst-case search (every query term in every chunk) takes ≈ 1.5 s at the limit; cap rows per term if it matters | when convenient |

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

- Thread context covers the last 3 exchanges; older turns are not used.
- No live updates between users: refresh to see others' new entries.
- One DM per campaign; no co-DMs, no sharing an entry with selected members only.

## Next

- **Testing phase (needs the Anthropic API key):**
  1. Smoke run per model (E1), about $0.05.
  2. Full live run on Opus (`eval live --label opus --max-cost 12`).
  3. A cheaper mix, e.g. Haiku for `route`/`contextualize` (`--label mixed`).
  4. `eval compare`, record the per-step model decision here, then `eval gate --live`.

  Also check real answers in the web UI (U1).
- M9 remaining: hosting + TLS (owner's decision: recurring cost), tracing/metrics, N2–N4,
  S3–S7, H2 (forwarded IP behind a proxy), U2 (one process per SRD index).
