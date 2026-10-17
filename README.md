# DungeonMind

**A campaign hub for D&D groups (SRD 5.2.1 / 2024 rules), with a grounded chatbot.**

A DM and their players share a campaign. The DM keeps the world's lore (NPCs, places,
factions, secrets) on a lore board; each player keeps a storyboard for their character; everyone
writes in the journal. Every entry is private, shared with the DM, or visible to the party, and
the chatbot answers each person only from the entries *they* may read, plus the rules with SRD
page citations. Dice, odds and encounter maths are done in code. It is a grounded, tool-using
assistant, not a chat-with-a-PDF demo.

## Status

| Milestone | State |
| --- | --- |
| M1 — Corpus: SRD 5.2.1 ingestion with page/section provenance | ✅ |
| M2 — Hybrid retrieval, benchmark, cited answers | ✅ |
| M3 — Structured tools: spells, monsters, dice, probability, encounter budget | ✅ |
| M4 — LangGraph orchestrator with bounded workflows | ✅ |
| M5 — Encounter builder | ✅ |
| M6 — Campaign hub backend: boards, visibility, per-campaign storage, API | ✅ |
| M7 — Campaign hub web UI (React) | ✅ |
| M8 — 209-case eval suite, offline gates, live runner, per-step models | ✅ (live run pending an API key) |
| M9 — Production: CI, Docker, backups ✅ · accounts, UI, hosting, tracing ⬜ | 🔶 |

## Quickstart

```bash
uv sync
uv run python scripts/download_srd.py      # fetches the CC-BY SRD 5.2.1 PDF
uv run dungeonmind ingest                   # → data/processed/srd_5.2.1_chunks.jsonl
uv run dungeonmind index                    # embeds chunks into a local Qdrant index (~2.5 min)
uv run dungeonmind search "can I grab an enemy so it can't run away?"
uv run dungeonmind eval-retrieval           # Recall@K / MRR for every retrieval mode
uv run dungeonmind sync-open5e              # SRD 5.2 spell + creature snapshot
uv run dungeonmind tool list                # the structured tools the agent can call
uv run dungeonmind tool search_monsters '{"creature_type": "undead", "can_fly": false}'
uv run pytest
```

Answers need an Anthropic API key (never commit it; `.env` is git-ignored):

```powershell
setx ANTHROPIC_API_KEY "sk-ant-..."         # Windows; open a new terminal afterwards
uv run dungeonmind chat "What is the probability my +7 attack hits AC 18 with advantage?" --trace
uv run dungeonmind chat "Which undead between CR 3 and CR 5 can't fly?"
uv run dungeonmind chat "How does the Ready action work?"
uv run dungeonmind chat "I have four level-5 characters. Build a hard ruined-temple encounter with undead, but no flying creatures." --conversation temple
uv run dungeonmind chat "make it easier" --conversation temple
```

`chat` is the main entry point. `ask` is the simpler rules-only path from M2. A `chat`
question costs roughly 2–5¢ on Claude Opus 5.5, and an encounter build about 8–10¢ (two calls
plus a longer write-up). `--conversation NAME` remembers the last encounter request so
"make it harder/easier" revises it; it's stored in `data/state/` (git-ignored).

### Campaign hub (web app)

```bash
uv run npm --prefix web install             # once: Node comes from the nodejs-wheel dev dependency
uv run npm --prefix web run build           # builds the UI into web/dist
uv run dungeonmind serve                    # open http://127.0.0.1:8000, data in data/hub/
uv run dungeonmind serve --host 0.0.0.0     # let players on your network join (plain HTTP)
uv run dungeonmind hub reset-password USER  # set a forgotten password
uv run dungeonmind hub backup --out ~/dungeonmind-backups  # safe while serving
uv run dungeonmind eval-notes               # note-search benchmark (local, no API calls)
```

In the browser: create an account, start a campaign (you're its DM), write lore, and share an
invite code from **Members**. Players join with the code, keep a storyboard for their
character, write in the journal, and chat. Answers cite the notes they used (click to open)
and the SRD pages. Follow-up questions ("what about her brother?") use the thread's earlier
turns. The lore board filters by kind and, for the DM, by secret or revealed; notes render
their Markdown (headings, bold, lists, quotes) without links or images. Chats can be renamed
or deleted. Your username in the top bar opens **Account**: questions used today, signed-in
devices, password change, "sign out other devices", and account deletion (your private
entries and chats are deleted; what you shared stays). For UI development, run `uv run npm --prefix web run dev` (Vite on :5173, proxying `/api`
to the hub on :8000) and `uv run npm --prefix web test`.

The API lives under `/api` (accounts, campaigns, invites, characters, entries, search, chats).
Browsers must send the header `X-DungeonMind: 1` on POST/PATCH/DELETE. Each campaign is
stored in its own `data/hub/campaigns/<id>/campaign.db`.

| Board | Who writes | Default visibility | Used for |
| --- | --- | --- | --- |
| Lore | DM only | private (revealing = set to party) | canon: NPCs, places, factions, secrets |
| Character | that character's player | shared with DM | backstory, goals, bonds, secrets |
| Journal | anyone | party | session notes, recaps, rumours |

Private = only the author (the DM can't read players' private entries); shared with DM = the
author and the DM; party = every member. Chat threads are private to whoever started them.
Each user can ask 50 questions a day by default (`serve --daily-limit`) and run 20 searches a
minute.

Design docs: [SPEC](docs/SPEC.md) · [ARCHITECTURE](docs/ARCHITECTURE.md) · [ROADMAP](docs/ROADMAP.md)

## How a question is answered

```
question → route ─┬─ rules_qa ──────────────→ retrieve → answer ─┐
                  ├─ lookup / probability ───→ agent ────────────┤
                  ├─ encounter ──→ extract → builder → present ──┤→ verify → answer + citations
                  ├─ campaign_memory / session_prep → "later" ───┤
                  └─ out_of_scope ───────────→ decline ──────────┘
```

- **Encounter:** Claude extracts the request (party levels, difficulty, creature types, no
  flyers, theme) and asks if the party's levels are missing. Then a deterministic builder
  picks the monsters. It applies the 2024 XP budget for the requested difficulty band, uses at
  most 3 stat blocks and 2 creatures per character, keeps CR at or below the party's highest
  level, gives each option a variety of roles (boss / controller / ranged / frontline), and
  cites each monster's SRD page. Claude only writes the result up. The same request always
  gives the same options, a build takes under 0.2 s, and "make it harder/easier" revises the
  remembered request one step.

- **Route:** Claude classifies the question and flags when the user is asking about older
  editions (the answer then says it covers 2024 rules only).
- **Agent:** a tool loop with hard limits: at most 6 turns (max 10), 8 tool calls per turn,
  and a 60 s budget passed to every API call as a timeout. The tools are the six below plus
  `search_rules`. A tool error goes back to the model as data, so a bad argument never
  crashes the run.
- **Verify:** every cited `E#` (rules passage) or `T#` (tool result) must exist in this run.
  Invented ids, citations of failed tool calls and malformed markers are removed and
  reported, and an uncited answer is downgraded to low confidence.
- **Untrusted text:** retrieved passages and tool output are treated as data. Evidence is
  escaped in prompts, and terminal output is stripped of control characters.

Built test-first: contract tests are written against a scripted fake LLM before each
implementation, followed by backend and security review. All 474 tests run
offline in about 17 s.

## Structured tools

Questions like "level-3 fire spells" or "undead around CR 5 that can't fly" need
filters, not text similarity. BM25 ranked Flaming Sphere (level 2) first for the
first one, while `search_spells` returns exactly Fireball. These tools are
deterministic, so the model never does the arithmetic itself:

| Tool | Does |
| --- | --- |
| `search_spells` | Filter 339 SRD spells by level, school, class list, damage type, concentration, ritual, casting time, text |
| `search_monsters` | Filter 331 SRD creatures by CR range, type, size, flying, text |
| `get_entity` | Full spell or stat block by name; unknown names get suggestions ("Beholder" → "Did you mean: Behir?") |
| `dice_roll` | Server-side roll with every die returned for auditing |
| `probability` | Exact hit/save odds with advantage, crit range, expected damage (2024 nat-20/nat-1 rules) |
| `encounter_budget` | 2024 XP budgets, difficulty rating and the SRD's troubleshooting warnings |

Spell and monster data is a snapshot of the [Open5e API v2](https://open5e.com/api-docs),
pinned to the `srd-2024` document so 2014 rules and third-party books can't leak in.
Each record links to its page in the SRD PDF for citations (668 of 670). The encounter
rules are transcribed from SRD pp. 202–203 and 256, and tests check them against the
SRD's own worked examples and against the XP of all 331 stat blocks.

The 2024 SRD has no habitat data for monsters, so "ruins" or "forest" can't be a
hard filter. The encounter builder (M5) chooses thematically fitting creature types
and says that it did.

## Free local mode (Ollama) or paid Claude

DungeonMind runs on a free local open-source model through [Ollama](https://ollama.com), or
on Claude. The prompts, citations and visibility rules are the same in both; only the model
changes.

```bash
brew install ollama            # or download the app from ollama.com
ollama serve                   # keep running in its own terminal (the app starts it for you)
ollama pull qwen3:4b           # ~2.5 GB; fits in 8 GB of RAM

uv run dungeonmind chat "How does grappling work?" --provider ollama
uv run dungeonmind serve --provider ollama
uv run dungeonmind eval live --provider ollama --limit 10 --label local
export DUNGEONMIND_PROVIDER=ollama   # make local the default
```

Switch back with `--provider claude` (needs `ANTHROPIC_API_KEY`). Pick another local model with
`--models all=llama3.2:3b`. On a small laptop, local answers take about a minute and are weaker
than Claude's. The evaluation suite measures how much weaker.

## Import your Obsidian campaign

```bash
uv run dungeonmind hub import-obsidian "/path/to/vault" --dm YOUR_USERNAME \
    --campaign-name "My Campaign" --sessions "/path/to/session-notes" --preview
```

`--preview` lists every entry it would create and changes nothing; drop it to import. The
vault is only read. Notes become private lore, with `kind` mapping to NPC, location, faction,
event or item. `dm_note`/`secrets` text is kept in separate DM-only entries, and session notes
become private journal entries. Reveal what players know in the web app. Turkish and other
non-English notes work: search folds diacritics, notes use a multilingual model, and answers
come in the language you ask in.

## Share with your players (Cloudflare Tunnel, free)

```bash
uv run dungeonmind share          # prints https://<random words>.trycloudflare.com
```

`share` starts a Cloudflare quick tunnel (no account needed) and the hub behind it. Players
open the printed HTTPS address from anywhere; your data never leaves your computer, and the
hub is reachable only while `share` runs. Press Ctrl-C to stop both.

- **Sign-up needs an invite code** while sharing: in **Members**, make a code and send it with
  the address. Signing up with it joins your campaign. Without a code nobody can create an
  account, so strangers who find the address can't spend your model budget. Only your own
  campaigns' codes create accounts (the hub's first account is its owner); a player's
  campaign can invite people who already have an account. (Your own account
  should exist first; on an empty hub the first sign-up needs no code.)
- **The address changes every run.** For a fixed address you need a Cloudflare account and a
  domain (about $10/year): see Cloudflare's "named tunnel" guide, then run
  `dungeonmind serve --proxy cloudflare --signup invite` behind it.
- Install `cloudflared` once: download `cloudflared-darwin-amd64.tgz` (Intel) or `-arm64`
  (Apple silicon) from [its releases](https://github.com/cloudflare/cloudflared/releases),
  unpack it and put `cloudflared` in `~/.local/bin`, or `brew install cloudflared`.
- Behind the tunnel the hub uses Cloudflare's `CF-Connecting-IP` for its sign-in limits, so one
  player's typos don't lock out the table. It trusts that header only with `--proxy cloudflare`
  and only from this machine.
- Wrong passwords are capped per client and per account (50 per 15 minutes per account, from
  any number of addresses). The hub's first account is its owner: only the owner's campaigns'
  codes create accounts, and the owner's account can't be deleted.
- Run either `share` or `serve`, not both: they use the same SRD index.

## Check search on your own campaign

```bash
uv run dungeonmind eval campaign data/evals/my_cases.jsonl --campaign <id>
```

One JSON object per line: `{"id": "t01", "question": "Madalyonu kim buldu?", "viewer":
"<username>", "expect": ["Madalyon"]}`, optionally `"no_leak": ["secret phrase"]`. It
reports R@5, MRR and every miss, and exits 1 if a `no_leak` phrase reaches that viewer's
search. No model calls; `data/evals/` is git-ignored because the cases quote your campaign.
The Obsidian importer moves "## DM notu"-style sections into the DM-notes entry; add
`--secret-heading Gerçek` (repeatable) for other secret headings.

## Backups

`dungeonmind hub backup --out DIR` writes `DIR/dungeonmind-backup-<UTC time>/` with `app.db`
(accounts) and every `campaigns/<id>/campaign.db`. It uses SQLite's backup API, so it's safe
while `serve` runs. The folder is private to your user (0700/0600), and it holds real campaign
text, so keep it out of the repo and out of shared folders. A failed run leaves nothing behind
(it writes to a hidden `.partial` folder first).

To restore: stop the server, move the old `data/hub/` aside, then copy the backup's contents
into an empty `data/hub/`. Restoring into the old folder would mix in leftover `-wal`/`-shm`
files and campaigns that aren't in the backup. Accounts and campaigns are copied one after
another, so a campaign created or deleted during the backup may be missing or orphaned; back
up when nobody is editing.

## Docker

```bash
cp .env.example .env              # add ANTHROPIC_API_KEY, or set DUNGEONMIND_PROVIDER=ollama
docker compose up --build         # open http://127.0.0.1:8000; health: /api/health
```

The image holds the web app, the API and the SRD data (built at image build time). It runs as a
non-root user and keeps hub data in the `hub-data` volume, never in the image. Compose
publishes the port on `127.0.0.1` only. With Ollama, the container reaches the Ollama app on
your Mac at `host.docker.internal:11434`.

Backups and restores with Docker (the container runs as uid 10001):

```bash
# back up: write inside the container, then copy out
docker compose exec dungeonmind dungeonmind hub backup --root /data/hub --out /tmp/backups
docker compose cp dungeonmind:/tmp/backups ./backups

# restore (or move an existing data/hub into the volume): stop, copy, fix ownership, start
docker compose stop
docker compose run --rm --user root --entrypoint sh dungeonmind -c 'rm -rf /data/hub/*'
docker compose cp ./backups/dungeonmind-backup-<time>/. dungeonmind:/data/hub
docker compose run --rm --user root --entrypoint chown dungeonmind -R 10001 /data/hub
docker compose start
```

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every push and pull request: ruff, the
full pytest suite on freshly built SRD data, `eval validate`, the offline `eval gate`, and the
web type check, tests and build. It uses no secrets and makes no paid model calls. The release
workflow builds the Docker image on pull requests and publishes it to GHCR only when you push
a `v*` tag.

## Evaluation (M8)

A versioned suite of 209 cases (`src/dungeonmind/evals/datasets/suite_v1.jsonl`) covers rules,
lookups, odds, encounters, campaign questions for a DM and a player on a fixture campaign,
session prep, follow-ups, out-of-scope, other-edition, unanswerable and prompt-injection cases.

```bash
uv run dungeonmind eval validate          # dataset checks (titles exist, numbers match the tools)
uv run dungeonmind eval offline           # retrieval, leak check, citation contract; no API calls
uv run dungeonmind eval gate              # exits non-zero if evals/gates.json is breached
uv run dungeonmind eval live --estimate   # expected cost of a live run; no API calls
uv run dungeonmind eval live --label opus --max-cost 12          # real model; stops at the budget
uv run dungeonmind eval live --label mixed --models route=claude-haiku-5-5 --max-cost 8
uv run dungeonmind eval compare evals/results/opus.json evals/results/mixed.json
```

Offline results (`evals/results/offline-v1.json`): SRD retrieval R@5 0.87 / MRR 0.81 (n=68),
notes retrieval R@5 0.98 / MRR 0.88 (n=41), leak rate 0, citation-contract violations 0 over 132
cases. Estimated live cost of the full suite: about $10 with Opus 5.5 everywhere, $0.26 with
Haiku 5.5 everywhere. Every step's model is configurable with `--models` or
`$DUNGEONMIND_MODELS` (`chat`, `serve`, `eval live`).

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
(M8) and after LLM query rewriting.

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
  tools/      spell/monster catalog (Open5e), dice, probability, encounters, tool registry
  schemas/    Chunk and shared models
  graph/      orchestrator: contract (state.py), LangGraph graph (build.py), Claude adapter (llm.py)
  hub/        campaign hub: storage, accounts, boards, visibility-filtered search, chats, API
  evals/      retrieval benchmark dataset and runner
web/          campaign hub UI (React + Vite + TypeScript), built into web/dist
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
