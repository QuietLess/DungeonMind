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
  graph/build.py     LangGraph graph, bounded agent loop, verify, run_chat()
  graph/llm.py       ClaudeLLM: the real LLM port (Anthropic SDK)
  hub/               campaign hub API: accounts, campaigns, notes, chats (M6)
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
| Campaign hub: FastAPI API + React (Vite, TS) UI, local first | A Python backend wraps the existing graph; React suits a chat + notes UI; no hosting cost until M9 | Hosting (M9) |
| One SQLite file per campaign; note vectors in SQLite, scored with numpy | Campaigns are physically separate; visibility is a SQL filter applied before ranking; ≤ 20k chunks need no vector server | Notes per campaign ≫ 20k |
| Visibility enforced before retrieval, never by prompt | A player's chat has no DM secret in context, so injection can't leak one | — |

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
- A `retrieve()` exception in rules_qa gives `stop_reason="error"`. A `retrieve()` exception
  inside `search_rules`, or any exception from `registry.call`, becomes a tool error
  (`{"error": …}`, `is_error: true`) and the loop continues. An agent turn with neither tool
  calls nor a final answer gives `stop_reason="error"`.

### M4 hardening (after backend + security review)

Each item has a regression test.

- **H1 Dice limits** (`tools/dice.py`). `parse` rejects (DiceError) expressions longer than
  64 characters, more than 10 terms, or more than 100 dice in total. `distribution` (and so
  probability's expected damage) raises DiceError "too large for exact odds" when the total
  support (Σ count × sides over all dice terms, after crit doubling) exceeds 1,000 (about
  1 s of exact arithmetic), or a
  keep-highest/lowest term would enumerate more than 100,000 outcomes. Probability calls with
  such damage return a tool error rather than computing.
- **H2 Input and output size.** `RollInput.expression` and `ProbabilityInput.damage`:
  max 64 characters. `EncounterInput.monsters`: max 20 entries. Free-text fields (catalog `text`,
  `EntityQuery.name`, encounter monster `name`, `search_rules` `query`): max 200 characters
  (`search_rules` over the limit → tool error). A tool output whose JSON exceeds 20,000
  characters is replaced by `{"error": "tool result too large"}` with `is_error: true`.
- **H3 Bounds.** `Deps.max_steps` outside 1–10 raises ValueError when `Deps` is created; the
  CLI caps `--max-steps` at 10. At most 8 tool calls run per turn; any further calls in that
  turn get a tool error ("too many tool calls in one turn") without running.
- **H4 Per-call timeouts.** `route` and `answer` receive `timeout=deps.timeout_s`. Each
  `agent_step` receives `timeout` = the remaining agent budget (deadline − `clock()`), at
  least 1 second. `ClaudeLLM` passes `timeout` to `messages.create` (omitting it when None,
  so the SDK default applies), builds its client with
  `max_retries=2` explicitly, and uses `max_tokens=1024` for `route`.
- **C1 Failed tools can't be cited.** Citing the T-id of a record with `is_error: true` is a
  violation `cited-error-T#`. The id is removed from the text and list, and it doesn't satisfy
  the no-citations check.
- **C2 Malformed markers.** Any bracketed marker that starts with an E/T id but isn't the
  canonical `[E3]` / `[E1, T2]` form (e.g. `[E1-E9]`, `[E1 ]`, `[T2;E1]`) is removed from the
  text, with violation `malformed-citation`. Removing any marker also removes the whitespace
  before it ("Yes [E9]." → "Yes.").
- **E1 error_type.** On `stop_reason="error"`, `error_type` is the exception class name
  (otherwise None). The CLI shows "No valid Anthropic credentials…" when neither
  `ANTHROPIC_API_KEY` nor `ANTHROPIC_AUTH_TOKEN` is set (checked before running) or when
  `error_type == "AuthenticationError"`; it doesn't search trace text.
- **E2 Tool list.** Building the tool list (`registry.definitions()` + `search_rules`) can't
  raise out of `run_chat`.
- **S1 Evidence escaping.** `rag.answer.format_evidence` escapes `&`, `<`, `>` and `"` in each
  block's text and source, so stored text can't close a block or forge another id.
- **S2 Terminal output.** `dungeonmind.cli.clean_for_terminal(text)` removes control
  characters (U+0000–U+001F except `\n` and `\t`, and U+007F–U+009F). The CLI passes every
  model-, tool- and exception-derived string through it before printing.
- **D1 One request path.** `AnswerGenerator` and `ClaudeLLM` share one request builder
  (model, max_tokens, output_config, fallback beta), and one inline-citation regex is used
  everywhere.
- `citations` lists each id once, in order of first appearance (list, then inline markers).
- The timeout check runs before every agent turn, including the first.

## M5 contract — encounter builder

Blueprint §9. The model extracts the request and writes the presentation; **code builds the
encounters**, so no XP arithmetic is done by the model.

### Builder (`dungeonmind.tools.encounter_builder`, deterministic, no LLM)

```python
class EncounterRequest(BaseModel):          # extra="forbid"
    party_levels: list[int]                 # 1–12 entries, each 1–20
    difficulty: Difficulty = Difficulty.MODERATE
    creature_types: list[str] = []          # catalog CREATURE_TYPES; empty = any type
    exclude_flying: bool = False
    exclude_names: list[str] = []           # ≤ 20 entries, each ≤ 200 chars
    max_stat_blocks: int = 3                # 1–3 (SRD p. 203: two or three is easiest to run)
    theme: str | None = None                # ≤ 200 chars; flavour for the write-up, never a filter

def roles(monster: Monster) -> set[str]     # capabilities, see below
def primary_role(monster: Monster) -> str   # "boss" | "controller" | "ranged" | "frontline"
def build_options(request: EncounterRequest, catalog: Catalog, max_options: int = 3) -> BuildResult
def revise(request: EncounterRequest, direction: Literal["easier", "harder"]) -> EncounterRequest
```

`BuildResult` has `options: list[EncounterOption]`, `budget: int` (target difficulty's total
budget), `band: tuple[int, int]` (exclusive low, inclusive high) and `note: str | None`.
`EncounterOption` has `monsters` (each: name, cr as text, xp, count, role, citation),
`total_xp`, `rating`, `warnings` (from `encounter.evaluate`) and `roles` (distinct primary roles).
Both are plain dataclasses and serialise to JSON with `dataclasses.asdict`.

Roles, computed from the stat block's traits, actions, bonus actions, reactions and legendary
actions:

- capabilities: `melee` if any text contains "Melee Attack Roll"; `ranged` if any contains
  "Ranged Attack Roll"; `controller` if any text containing "Saving Throw" also names a
  condition (Blinded, Charmed, Deafened, Frightened, Grappled, Incapacitated, Paralyzed,
  Petrified, Poisoned, Prone, Restrained, Stunned, Unconscious); `legendary` if it has
  legendary actions.
- primary role: `boss` if legendary; else `controller` if controller; else `ranged` if ranged
  and not melee; else `frontline`.

Rules for options (each one is an acceptance criterion):

1. **Candidates:** CR > 0; CR ≤ the party's highest level (so the SRD's "Powerful Creatures"
   caution never applies); type in `creature_types` if given; no fly speed if
   `exclude_flying`; name not in `exclude_names` (case-insensitive).
2. **Band:** total XP > the next-lower difficulty's budget (0 for Low) and ≤ the requested
   difficulty's budget. Four level-5s at High: (3,000, 4,400].
3. **Shape:** 1 to `max_stat_blocks` distinct stat blocks, at most 2 creatures per character
   (SRD "Many Creatures"), each option's `rating` equals the requested difficulty.
4. **Variety:** options differ in their set of stat blocks, and no two share the same
   highest-XP stat block.
5. **Ranking:** more distinct primary roles first, then higher total XP, then fewer creatures,
   then alphabetical by monster names. Up to `max_options` options.
6. **Deterministic:** the same request and catalog give identical results.
7. **Bounded:** `build_options` finishes in under 1 s for any valid request on the full
   331-creature catalog.
8. **Honest when empty:** no options → `options == []` and `note` says why: no candidates
   after the filters, or no combination fits the band.
9. **Citations:** each monster's `citation` is its SRD 5.2.1 page citation from the catalog.
10. **Revise:** `revise` moves difficulty one step (low ↔ moderate ↔ high) and keeps every
    other field. Easier than Low or harder than High raises ValueError naming the limit
    (the SRD defines nothing outside Low–High).

### Graph changes (`graph/state.py`, `graph/build.py`)

- `Intent.ENCOUNTER` now routes to a new `encounter` node (it's removed from `TOOL_INTENTS`).
- New LLM port methods, both with keyword-only `timeout`:
  - `extract_encounter(question, previous: EncounterRequest | None) -> EncounterExtraction`
    with `kind: "build" | "revise" | "other"`, `request: EncounterRequest | None` (for
    build; None if load-bearing info is missing), `direction: "easier" | "harder" | None`
    (for revise), and `missing: list[str]` (e.g. `["party_levels"]`).
  - `present_encounter(question, request, record: ToolRecord) -> FinalAnswer`: writes the
    options up (difficulty rationale, roles, tactics) citing `T1`. It says that the SRD has
    no habitat data and that the theme only shaped the write-up.
- `Deps` gains `store: ConversationStore` (default: in-memory). `ConversationStore` has
  `get(conversation_id) -> EncounterRequest | None` and `put(conversation_id, request)`.
  `JsonFileStore(path)` persists to a JSON file for the CLI.
- `StopReason` gains `"needs_input"`.
- The `encounter` node, in order:
  1. `previous = store.get(conversation_id)` if a conversation id is given, else None.
  2. `llm.extract_encounter(question, previous, timeout=deps.timeout_s)`.
  3. `kind == "other"`: hand over to the `agent` node (e.g. "what's the XP budget for…?"),
     so the trace is route, encounter, agent, verify.
  4. `kind == "revise"`: with no previous request, answer that there's no earlier encounter in
     this conversation and ask for the party, with `stop_reason="needs_input"`. Otherwise
     `request = revise(previous, direction)`; a ValueError gives its message as the answer
     (`stop_reason="completed"`, `insufficient_evidence=True`).
  5. `kind == "build"` with `request` None or `missing` non-empty: ask for the missing
     information, with `stop_reason="needs_input"` and no builder call.
  6. Otherwise run `build_options`, record it as `ToolRecord` `T1` with name
     `"encounter_builder"`, arguments the request, and output `{"result": asdict(result)}`;
     `store.put(conversation_id, request)` when a conversation id is given; then
     `llm.present_encounter(question, request, record, timeout=deps.timeout_s)` and verify.
- `needs_input` answers have `confidence="low"`, `insufficient_evidence=False`, no citations,
  and are exempt from the no-citations check. LLM exceptions in this node follow behaviour 6.
- CLI: `chat --conversation NAME` uses `JsonFileStore("data/state/conversations.json")`;
  without it nothing persists.

### M5 clarifications (settled after the first test pass)

- Roles are a variety heuristic, not a rules claim. "Melee or Ranged Attack Roll" contains
  "Ranged Attack Roll" but not "Melee Attack Roll", so it grants `ranged` only. No SRD
  creature is purely ranged, and this is what makes e.g. the Guard and the Mage `ranged`.
- `EncounterRequest.creature_types` entries must be catalog `CREATURE_TYPES` (ValidationError
  otherwise); each party level is 1–20 and each `exclude_names` entry ≤ 200 characters.
- Options are chosen greedily in ranking order (rule 5), skipping any that would break the
  variety rule (rule 4). The final tie-break compares the sorted, lowercased monster names.
- The store lives in `dungeonmind.graph.store` (`InMemoryStore`, `JsonFileStore`).
  `JsonFileStore` creates missing parent directories, and with no file it starts empty.
- The `encounter` node uses `deps.registry.catalog`. T1 `arguments` is
  `request.model_dump(mode="json")`.
- The other-edition note (behaviour 10) also applies to completed encounter answers that
  present options, not to needs_input answers or revise-limit messages.

### M5 fixes from the live check

- **P1 Revisions are explained.** `present_encounter` takes keyword-only
  `revision: Literal["easier", "harder"] | None = None`. The encounter node passes the
  direction on a revise and None on a build. When it's set, the adapter's user message says the
  previous encounter was made easier/harder and the request already reflects that, so the
  write-up presents the new options (observed bug: "make it easier" built Moderate options but
  the write-up said it couldn't make it easier).
- **P2 Defaults aren't attributed to the user.** The presentation prompt says that difficulty
  Moderate and up to 3 stat blocks are defaults unless the user's message asked for them, and
  must not be described as the user's request (observed: "three stat blocks, as you asked").

### M5 review fixes

- **R1 Name cap respects variety.** When expanding names for a combination, names already used
  as an earlier option's highest-XP stat block are skipped *before* taking the first
  `NAMES_PER_CLASS`, so the cap can't hide a better-ranked option. Regression case: party
  `[1]*6`, High, `["beast"]`, `max_stat_blocks=3`; the result must equal an uncapped search.
- **R2 Out-of-range requests say why.** When the model's extracted request fails validation,
  `missing` describes the broken limit (e.g. "party of at most 12 characters", "at most 3
  stat blocks", "character levels 1–20") rather than only naming the field.
- **R3 Store after presenting.** `store.put` runs only after `present_encounter` succeeds, so
  a failed write-up never leaves a revision the user didn't see.
- **S1 Safer writes.** `JsonFileStore.put` writes to a unique temporary file in the same
  directory and retries the final replace briefly, which avoids failures when two processes
  write at once. A lost update between two simultaneous writers is still possible and is
  accepted for a local CLI (tracked for M8).
- **S2 Damaged state file.** If the store file can't be read or parsed, or an entry isn't a
  valid request, `get` returns None (treated as no earlier encounter). The encounter node adds
  a trace warning naming the file instead of failing. A file over 1 MB is treated the same way.
- S2 detail: `put` on a file it couldn't read copies it to `<name>.bak` first, then starts
  from an empty store (keeping other valid entries
  when only one entry was invalid). If `put` fails after a successful write-up, the answer is
  still returned and the trace notes "not saved".
- Builder caps: `MAX_CLASSES = 64` (the catalog has about 50 (XP, role) classes up to CR 20,
  so none are dropped in practice; 48 dropped the 25 XP top-up class), `NAMES_PER_CLASS = 4`
  after skipping already-used top stat blocks, `MAX_EXPANSIONS = 5,000`. Worst case measured
  0.15 s.

## M6 contract — campaign hub backend

Product direction (2026-10-16): DungeonMind becomes a **campaign hub**. A DM and players share
a campaign, each keeps notes with a visibility level, and each can ask a chatbot about the
campaign under the 2024 rules. Local first: the DM runs the server and players join over the
local network. Hosting comes later (M9). M6 is the HTTP API; M7 builds the React UI on it.

### The one rule everything else serves

> **The model only ever receives notes the asking user is allowed to read.**

Visibility is enforced in the storage query that selects candidate notes, *before* ranking
and before anything reaches the graph. Confidentiality doesn't depend on a prompt: a player's
chat run has no DM secret in its context, so no prompt injection can reveal one.

### Boards: where entries live

Every piece of campaign writing is an **entry** on one of three boards. Each is a retrieval
source for the bot under the same visibility rules.

| Board | Who writes | Kinds | Default visibility | What it's for |
| --- | --- | --- | --- | --- |
| **Lore** (one per campaign) | DM only | npc, location, faction, item, event, plot, other | private (a DM secret) | The campaign's canon: world, NPCs, secrets. Revealing an entry = changing it to `party`. |
| **Character** (one per character) | the character's owner only | backstory, goal, bond, secret, relationship, other | `dm` | A player's storyboard for their character. |
| **Journal** (one per campaign) | any member | note | `party` | Session notes, recaps, rumours, reminders. |

- Characters: any member can create characters (name ≤ 60, summary ≤ 500). A character's name
  and summary are visible to every member; its entries follow their own visibility.
- **Canon.** Lore entries are the campaign's canon. Journal and character entries are members'
  records and may be incomplete or wrong. When visible sources conflict, the bot gives the lore
  version and mentions the discrepancy. A player only ever sees revealed lore, so this can't
  leak a secret.
- "Learning" means retrieval at question time, not training. An edit or reveal affects the very
  next question, and a deleted entry is never cited again.

### Roles and visibility

Each campaign has exactly one DM (its creator) and any number of players.

| Note visibility | Who can read it | Label in the UI |
| --- | --- | --- |
| `private` | the author only (the DM can't read players' private notes) | Private |
| `dm` | the author and the DM | Shared with DM |
| `party` | every current member | Party |

- Visibility applies to entries on every board. Lore entries accept only `private` and `party`.
- For a DM-authored note, `dm` means the same as `private`. The API accepts it and stores it as
  `private`, and the UI offers only Private / Party to the DM.
- Only the author can edit or delete a note. The DM can't edit players' notes.
- Changing visibility takes effect on the next request. There are no caches of visible sets.
- A removed or departed member loses all access at once. Their notes stay in the campaign
  with their visibility unchanged, so their private notes become readable by no one. They are
  deleted with the campaign, or by the author before leaving.
- Chat threads with the bot are private to the user who started them. The DM can't read
  players' threads.

### Storage: one database file per campaign

```
data/hub/app.db                     users, sessions, campaigns, memberships, invites, usage
data/hub/campaigns/<campaign_id>/
  campaign.db                       characters, entries, entry chunks + embeddings,
                                    chat threads, messages, encounter state per thread
```

- `campaign_id` is server-generated (`uuid4().hex`) and fullmatches `[0-9a-f]{32}` before any
  path is built. The resolved folder must be a direct child of the campaigns root, and symlinks
  are refused.
- Notes for one campaign live only in its own `campaign.db`. No query, index or cache spans
  campaigns.
- Note retrieval is held inside SQLite: each chunk stores its embedding (float32 blob, 384-d
  bge-small, the SRD model), and search scores the *visible* chunks with cosine (numpy) and BM25
  (BM25 from a per-chunk term table), fused with RRF. At the M6 limit (5,000 entries ≈ 20,000
  chunks) a search takes 0.2–0.3 s warm (measured after F2), so notes need no vector server.
  Qdrant stays for the SRD.
- SQLite runs in WAL mode with one connection per request, which is enough for one local server.
- Passwords are hashed with `hashlib.scrypt` (n=2^15, r=8, p=1, 16-byte salt), so no new
  dependency is needed.

### Module layout (`src/dungeonmind/hub/`)

```
hub/db.py          open app.db / campaign.db (path checks, schema creation, WAL)
hub/auth.py        password hashing, session tokens, login rate limit
hub/entries.py     entries + characters in one campaign.db: CRUD, chunking, embeddings,
                   visibility filter, hybrid search (no permission logic)
hub/chats.py       threads, messages, per-thread encounter store, chat engine wiring
hub/service.py     Hub: the one place that checks membership, roles and authorship
hub/api.py         FastAPI app: routes, session dependency, error mapping, CSRF check
```

New dependencies: `fastapi` and `uvicorn` (runtime) and `httpx` (already present, for
TestClient). `dungeonmind serve [--host 127.0.0.1] [--port 8000]` starts the server.

### Data model

```python
Role = Literal["dm", "player"]
Visibility = Literal["private", "dm", "party"]
Board = Literal["lore", "character", "journal"]

class Entry:                     # campaign.db
    id: int; board: Board; character_id: int | None   # set iff board == "character"
    author_id: str; kind: str                         # kind must belong to the board
    title: str                                        # 1–120 chars
    body: str                                         # Markdown, 1–10,000 chars
    visibility: Visibility
    session: int | None                               # 1–999
    created_at: datetime; updated_at: datetime        # UTC, injectable clock

# Chunks: title + body split on blank lines into ≤ 1,500-char pieces (longer paragraphs
# split at sentence ends, then hard-wrapped); re-chunked and re-embedded on edit.

@dataclass
class NoteHit:                   # graph/state.py: what the graph sees of an entry
    entry_id: int
    source: str                  # e.g. "Lore › NPC › Elara", "Thorn's board › Backstory",
                                 # "Journal › Session 3 recap (Mia)"
    text: str                    # the best-matching chunk
    canon: bool                  # True for lore entries
    score: float
```

`Embedder` protocol (`embed(texts) -> list[list[float]]`): the default wraps fastembed. Tests
inject a deterministic fake.

### API (all JSON under `/api`; every route but register/login requires a session)

| Method and path | Who | Notes |
| --- | --- | --- |
| `POST /auth/register` `{username, password}` | anyone | username `[a-z0-9_]{3,32}`, password 10–200 chars |
| `POST /auth/login`, `POST /auth/logout`, `GET /me` | — | session cookie |
| `GET /campaigns`, `POST /campaigns` `{name}` | user | lists only the user's campaigns; the creator becomes DM |
| `GET /campaigns/{cid}`, `DELETE /campaigns/{cid}` | member / DM | delete removes the campaign folder |
| `POST /campaigns/{cid}/invites` → `{code, expires_at}` | DM | 12-char code, 7 days, single use, revocable |
| `POST /invites/{code}/accept` | user | joins as player |
| `GET /campaigns/{cid}/members`, `DELETE …/members/{uid}` | member / DM (or self = leave) | the DM can't leave; they delete the campaign instead |
| `GET/POST /campaigns/{cid}/characters`, `PATCH/DELETE …/characters/{chid}` | member / owner | |
| `GET /campaigns/{cid}/entries?board=&character_id=&session=&q=` | member | visible entries only |
| `POST /campaigns/{cid}/entries`, `GET/PATCH/DELETE …/entries/{eid}` | member / author (lore: DM) | |
| `GET/POST /campaigns/{cid}/chats`, `GET …/chats/{tid}` | member, own threads | |
| `POST /campaigns/{cid}/chats/{tid}/messages` `{text}` → answer | thread owner | text 1–2,000 chars |

### Behaviour (each line is an acceptance criterion with a test)

**Access control**

1. Every route except register and login returns 401 without a valid session (a parametrized
   test walks the route table, so a new route can't be added unprotected).
2. A non-member gets **404** for any campaign route, and a member gets **404** for a note or
   thread they can't read. A 403 would reveal that the thing exists. Role failures on things
   the caller *can* see (a player deleting the campaign, editing another member's party note)
   give 403.
3. **Visibility matrix:** for every combination of viewer (DM, author player, other player,
   removed member, non-member) × visibility × author role, the results of list, get, search
   and the evidence given to the chat graph match the table above exactly.
4. **The model's input never holds an unreadable note.** With a recording fake LLM, a player
   asks about a fact that exists only in a DM-private note, a `dm`-visibility note of another
   player, and another campaign's party note. None of that text appears in any LLM call, the
   answer abstains, and the trace shows how many notes were visible (not which were hidden). A DM asking the same
   question gets the DM-private lore. After the DM reveals it (`party`), the player's next
   question cites it.
5. Cross-campaign: two campaigns with conflicting facts about the same NPC. Every question in
   campaign A cites only A's notes, and the reverse, including after edits, deletions and
   member changes.

**Sessions and accounts**

6. Session tokens: 32 random bytes, stored as SHA-256 hashes, cookie `HttpOnly`, `SameSite=Lax`,
   `Secure` when served over HTTPS, 14-day expiry, deleted on logout.
7. Unsafe methods (POST/PATCH/DELETE) require the header `X-DungeonMind: 1` (a CSRF check that
   simple cross-site forms can't send). If it's missing → 403.
8. Login: same error and similar timing for an unknown user and a wrong password. After 5
   failures per username in 15 minutes, further attempts return 429 until the window passes.
9. `serve` binds 127.0.0.1 by default. `--host 0.0.0.0` prints a warning that traffic on the local
   network isn't encrypted.

**Boards**

B1. Only the DM can write lore. A player creating a lore entry, or an entry on another member's
    character board, gets 403. The kind must belong to the board (422 otherwise). A character
    entry needs a `character_id` in this campaign, and other boards must not have one.
B2. Lore visibility is `private` or `party` (`dm` → `private`). The defaults are lore
    `private`, character `dm` and journal `party`.
B3. Deleting a character requires that it has no entries (409 naming the count). The author
    deletes the entries first, so no one deletes another member's writing by accident.
B4. The canon rule is in the answer prompt, and lore hits are marked `canon` in the
    `<note … canon="true">` block.

**Notes** (entries on any board)

10. Create, edit and delete keep the chunks and embeddings in step with the note in one
    transaction. A note whose embedding fails isn't saved (500 with a plain message, no trace
    text).
11. Limits: title ≤ 120, body ≤ 10,000 characters, ≤ 5,000 notes per campaign, ≤ 200 members,
    request bodies ≤ 64 KB. Over the limit → 422 or 413 naming the limit.
12. Search over the visible notes is hybrid (cosine + BM25, RRF). A fixture-campaign benchmark
    (≈ 20 questions with expected note ids) records R@5 and MRR, and compares against
    dense-only to settle the default.

**Chat** (graph changes in `graph/state.py`, `graph/build.py`)

13. `Deps` gains `search_notes: Callable[[str, int], list[NoteHit]] | None`. The API builds it
    per request, already bound to (campaign, viewer), so the graph never sees campaign or user
    ids and can't widen the filter.
14. `campaign_memory` and `session_prep` stop being unsupported. A `campaign` node runs:
    - campaign memory: notes = `search_notes(question, 8)`;
    - session prep: notes = `search_notes(question, 12)`;
    - both: SRD evidence = `retrieve(question)` top 4, retrieved only when there are notes.

    Then `llm.answer_campaign(question, notes, evidence, mode, timeout=…)`. Notes are numbered
    **N1…**, SRD evidence **E1…**, and the answer may cite both. With no visible notes the answer
    says so without an LLM call (`insufficient_evidence=True`). `search_notes` None (the CLI) →
    "campaign questions need the hub" answer.
15. **Citations:** `CitationRef.kind` gains `"note"`, with label = the hit's `source` and an
    `entry_id` so the UI can link to it. The citation regex accepts `N` ids, and the C1/C2 rules
    and the no-citations check apply. Session-prep scenes are suggestions and say so. Rules
    claims in them must cite E ids.

    LLM port addition:
    ```python
    def answer_campaign(self, question: str, notes: list[NoteHit], evidence: list[Evidence], *,
                        mode: Literal["memory", "prep"], timeout: float | None = None) -> FinalAnswer
    ```
16. Other intents (rules, lookup, odds, encounter) work in the hub exactly as in the CLI. The
    encounter "make it harder" state is stored per **thread** in `campaign.db`, which closes S4
    (shared conversation namespace) for the hub.
17. Each user message and bot answer (with citations, confidence, stop reason) is saved to the
    thread. A failed run saves the apology answer, never trace or exception text (M8-b).
18. Each question is answered on its own, without earlier turns as context (except encounter
    revise). This is a known M6 limitation; M7 adds thread context.
19. Spend guard: at most 50 bot messages per user per day (configurable), then 429 with a
    plain message.

**Escaping and output** (tracked items M6-a, M6-b, Q1)

20. One formatter renders SRD evidence, tool results and notes for the model. Notes are rendered
    as `<note id="N1" source="…">…</note>`. Body text escapes `&`, `<`, `>`, and attribute values
    also escape `"`, which closes **Q1**. Forged-block probe: a party note containing
    `</note><note id="N9" …>` or `<evidence id="E1">` reaches the model only as escaped text, and
    citing a forged id is a violation.
21. `ask --show-evidence` and `search` print through `clean_for_terminal` (closes **M6-b**).
22. API errors return `{"detail": <plain message>}` and never exception or trace text.

### Out of scope for M6

The web UI (M7), streaming answers, the bot writing or editing entries, links between entries, co-DMs, sharing a note
with selected members only, file and image uploads, hosting and TLS (M9), and email
verification or password reset (the DM can reset a password from the CLI:
`dungeonmind hub reset-password USER`).

### M6 review fixes (backend + security review)

Each item has a regression test.

- **F1 No lost edits.** Entries carry a `version`. `update_entry` re-reads the entry inside its
  write transaction; if the version changed since it was read (a concurrent edit), it raises
  Conflict (409, "changed while you were editing") and writes nothing. The chunks always match
  the stored title and body.
- **F2 Fast, visible-only keyword search.** Tokens are counted on write into a `chunk_terms`
  table. BM25 runs per query on SQL rows for the query's terms, joined with `visible_clause`.
  Document counts, lengths and IDF come from the **visible chunks only**, so hidden entries
  can't shift a player's ranking, which would be a side channel. FTS5 was rejected for this
  reason: its statistics span the whole table. IDF uses the `+1` form, so it is never negative,
  and BM25 still contributes in campaigns with one or two entries. Dense scoring still loads
  the visible vectors. Measured at the limit (5,000 entries, 20,000 chunks, fake embedder):
  0.18–0.29 s per warm search, down from 3.6–4.7 s before F2. The first search on a cold disk
  cache can take 1–4 s. Writes take about 70 ms per 4-chunk entry.
- **F3 Search rate limit.** At most 20 searches per user per minute (429). This was lowered
  from 60 after the re-review: worst-case notes, with every term in every chunk, take ≈ 1.5 s
  per search at the limit. `ask` has its own
  daily limit.
- **F4 Login limits are atomic and per client.** Each attempt is reserved under a lock *before*
  the password check, so parallel attempts can't exceed the limit. Limits are 5 failures per
  (client IP, username) and 30 failures per client IP, per 15 minutes. Another IP can't lock a
  user out. Usernames that don't match the username pattern fail without being tracked. The
  tracked-key table is capped (oldest evicted).
- **F5 Password hashing is bounded.** At most 4 scrypt operations run at once. Registration is
  limited to 10 accounts per client IP per hour. Register issues its session directly (one
  scrypt, not two). A user can create at most 20 campaigns.
- **F6 No secrets in URLs or logs.** Invite codes travel in POST bodies
  (`POST /api/invites/accept {code}`, `POST /api/campaigns/{cid}/invites/revoke {code}`), and
  `serve` runs with uvicorn's access log off, so search text never reaches the console.
- **F7 Sessions.** `user_for_token` filters expired sessions in its query, with no write, so it
  can't fail with "database is locked". Expired sessions and invites are purged on login and
  on invite creation.
- **F8 Unbilled failures don't use the daily quota.** Only a run whose engine raised before
  producing a state is refunded, at most 3 per user per day. A graph run that ends with
  `stop_reason="error"` may already have been billed by the model (truncation, timeout), so it
  counts. Otherwise a user could get past the spend guard by provoking failures (re-review).
- **F9 Unicode list filter.** `?q=` compares case-folded text (Python `casefold`), so
  "élara" finds "Élara".
- **F10 Campaign creation is all-or-nothing.** A failure after the folder is made removes the
  folder again.
- **F11 One model, one lock.** `build_hub` loads the embedding model once, and every use of
  it (SRD search and note embedding) shares one lock.
- **F12 Sessions per user.** A user holds at most 20 sessions; a new login removes the oldest, by
  insertion order (not expiry, which a frozen or stepped-back clock would tie or reorder).
- **Schema.** `campaign.db` files created before these fixes (only on this branch) must be
  recreated; there are no released databases to migrate.
- **For M7 (from the security review):** bot answers must not render remote images or
  auto-link URLs. A party note could otherwise inject a link that sends DM-only lore out of
  the DM's own chat.
