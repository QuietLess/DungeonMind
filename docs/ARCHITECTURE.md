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

## M7 contract — campaign hub web UI

A React (Vite, TypeScript) single-page app in `web/`, built to `web/dist` and served by
`dungeonmind serve` on the same origin as the API. Same origin means the session cookie
(`SameSite=Lax`) works without CORS. Node comes from the `nodejs-wheel` dev dependency
(`uv run npm …`), so nothing is installed system-wide.

### Serving and headers (Python, tested)

- **UI1** `/` and every non-`/api` path serve `web/dist/index.html` (SPA fallback). Built assets
  are served from `/assets/…`. `/api/*` is unaffected, and an unknown `/api` path is still a
  JSON 404. If `web/dist` is missing, `/` returns a short page saying to run
  `uv run npm --prefix web run build`.
- **UI2** Every response carries security headers:
  `Content-Security-Policy: default-src 'self'; img-src 'self' data:; script-src 'self';
  style-src 'self'; connect-src 'self'; font-src 'self'; frame-ancestors 'none';
  base-uri 'none'; form-action 'self'`, plus `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer` and `X-Frame-Options: DENY`. With `img-src 'self'`, a remote
  image can't load even if a renderer bug let one through (defence in depth for H1).

### Thread context (Python, tested)

- **UI8** The graph gains a `contextualize` node before `route`. `run_chat(…, history=…)` takes
  the thread's last 3 exchanges (question and answer text only, each capped at 1,000
  characters). Hub.ask loads them from the asker's own thread. With history,
  `llm.standalone_question(question, history, timeout=…)` rewrites a follow-up ("what about
  her brother?") into a standalone question. That question is used for routing, retrieval and
  answering, and the trace records it. The saved user message stays what the user typed.
  - Without history there is no extra model call.
  - An empty or over-200-character rewrite, or any exception, falls back to the original
    question, with a trace warning.
  - The history is data, not instructions, in the prompt.

### UI behaviour (Vitest + Testing Library, tested)

- **UI3 Safe answers (closes H1).** Bot answers are rendered by our own small renderer, never
  as HTML. It handles paragraphs, line breaks, `-`/`1.` lists and `**bold**`, and nothing
  else. No `<a>`, `<img>` or raw HTML is ever produced; Markdown links, images and bare URLs
  show as plain text. `dangerouslySetInnerHTML` is not used anywhere (checked by a test that
  scans `web/src`).
- **UI4 Citations.** Inline markers `[N1]`, `[E2]` and `[T1, N3]` that match the answer's
  citation list become buttons. N opens the entry in a side panel, fetched through
  `GET /entries/{id}` so visibility is checked again. E shows the SRD citation label, and T
  shows the tool label. A marker with no matching citation stays plain text.
- **UI5 API client.** Requests are same-origin with credentials. POST/PATCH/DELETE send
  `X-DungeonMind: 1`. A 401 sends the user to sign-in, and other errors show the API's
  `detail`.
- **UI6 Visibility is always visible.** Every entry shows a badge with an icon and text
  (Private / Shared with DM / Party), never colour alone. The DM's visibility picker offers
  Private and Party only, and lore offers Private and Party to everyone. On a private lore
  entry the DM gets a **Reveal to party** button, which asks for confirmation before setting
  `party`.
- **UI7 Pages:**
  - sign in / register;
  - campaigns (list, create, join with a code);
  - one campaign: **Lore**, **Characters** (each character's storyboard), **Journal**,
    **Chat** (threads and messages) and **Members** (DM: create and copy an invite code,
    remove players; player: leave).
- **UI9 Accessibility.**
  - Every input has a label.
  - Everything works from the keyboard, with a visible focus ring.
  - New bot answers are announced through `aria-live="polite"`.
  - Colours meet WCAG AA in the light and dark themes (`prefers-color-scheme`).
  - The layout works at phone width.

### Out of scope for M7

Streaming answers, real-time updates between users (refresh to see others' edits), rich-text
editing (entries are Markdown text shown as plain paragraphs), images and attachments.

### M7 review fixes (frontend + security review)

Each item has a regression test.

- **D1–D4 Drawer.**
  - D1: focus moves into the drawer once, when it opens, and back to where it came from when it
    closes. Typing in a form whose state lives in the parent keeps every letter. This was
    CRITICAL: "New character" took only the first letter.
  - D2: closing (Escape, ×, backdrop) with unsaved changes asks first.
  - D3: only a click that starts and ends on the backdrop closes the drawer.
  - D4: Tab and Shift+Tab stay inside the dialog.
- **C1–C5 Chat.**
  - C1: a thread started on the page is reloaded normally once its first answer arrives, so
    switching away and back shows each thread's own messages.
  - C2: an answer that arrives after the user switched threads isn't shown in the other thread.
  - C3: phones get a compact chat picker (and "New chat") where the side list is hidden.
  - C4: Enter while an input method is composing doesn't send.
  - C5: the conversation is a `role="log"` region.
- **F1–F3 Forms.**
  - F1: errors show inside the open drawer.
  - F2: create, join and save can't be sent twice.
  - F3: a failed delete keeps the panel open and shows why.
- **A11y.** Field borders reach 3:1: `#857a6b` light, `#7a7088` dark. Inputs keep the global
  focus ring. "Copied" appears only when the clipboard write worked, and is announced. Campaign
  pages show a loading line. The phone nav wraps instead of hiding sections off-screen.
- **W1** Security headers are on every response, including unexpected 500s. A path with a NUL
  byte gets the app page, not an error.
- **W2** Hidden files (any segment starting with `.`) and `*.map` files are never served from
  `web/dist`.
- **W3** The campaign prompt says that the (rewritten) question itself is not evidence. A fact
  an earlier answer quoted, and that the DM has since hidden, can't be cited back from it.
- **W4** API responses carry `Cache-Control: no-store`. Static files answer HEAD. Pages also
  send `Cross-Origin-Opener-Policy: same-origin` and a `Permissions-Policy` that denies camera,
  microphone, geolocation and payment.
- **Re-review fixes.**
  - C6: a slow load of a thread the user has left is ignored.
  - C7: "thinking" and errors belong to the thread that asked. A new chat that finishes
    creating after the user moved elsewhere doesn't pull them back.
  - After a delete, focus returns to the page.
  - The phone layout uses `100dvh`, so the composer stays visible.
  - W2 compares `.map` case-insensitively; APFS would otherwise serve `a.js.MAP`.
- **Known limitation:** the embedded SRD index can be open in one process at a time, so run
  either `serve` or `chat`/`ask`, not both. A Qdrant server (M9) removes this.

## M8 contract — evaluation

Goal: a versioned suite of 200+ cases that measures what DungeonMind is for: grounded rules
answers, correct tools, campaign answers that never leak, honest abstention. Results are
tracked per version and gated for regressions, and the per-step model choice (Opus vs Sonnet
vs Haiku) is decided from measured quality, cost and latency. **Everything except the live
run works offline.** The live run needs an API key and spends money, so it always shows a
cost estimate and stops at a budget.

### Suite (`src/dungeonmind/evals/datasets/suite_v1.jsonl`, fixture campaign `notes_v0.json`)

One JSON object per case: `id`, `category`, `question`, optional `viewer` (`dm` | `player`,
campaign cases only), optional `history` (earlier exchanges, for follow-ups), and `expect`:

| Field | Meaning | Checked |
| --- | --- | --- |
| `intent` | the route the question should take | live |
| `srd` | SRD heading titles that answer it (at least one should be retrieved/cited) | offline (retrieval) + live (citations) |
| `notes` | fixture entry titles that answer it | offline (notes retrieval) + live |
| `tool` | tool the agent should call | live |
| `contains` | regexes (case-insensitive) the answer must match | live |
| `numbers` | numbers the answer must state (odds, XP); computed by our tools | offline (the dataset agrees with the tools) + live |
| `abstain` | the answer must set `insufficient_evidence` | live |
| `decline` | out of scope: the decline answer | live |
| `other_edition` | the 2014-rules note must appear | live |
| `no_leak` | strings that must never appear in the answer or in the evidence the model got | offline (search) + live |
| `forbid` | strings the answer must not contain, though they may be in the evidence (injection payloads such as "PWNED", prompt text) | live |

Categories and minimum counts:
- `rules` 60
- `lookup` 25
- `probability` 20
- `encounter` 12
- `campaign` 40 (DM and player viewers, including questions whose answer exists only in
  hidden entries)
- `prep` 8
- `followup` 8
- `out_of_scope` 12
- `other_edition` 6
- `unanswerable` 10
- `injection` 8 (notes or questions that try to steer the model)

Total ≥ 200.

**V1 Validation** (`dungeonmind eval validate`, a test):
- every id is unique and every category is known;
- every `srd` title exists in the chunks and every `notes` title exists in the fixture;
- `viewer` is set iff the case reads the campaign;
- every `probability` case's `numbers` match what `tools/probability.py` computes for its
  `args`;
- category counts meet the minimums.

### Offline evaluation (`dungeonmind eval offline`, no API calls)

- **O1 SRD retrieval:** R@5 and MRR over cases with `srd`, per retrieval mode.
- **O2 Notes retrieval:** R@5 and MRR over cases with `notes`, using the real hub search as the
  case's viewer on the fixture campaign (the fake embedder in tests, the real one from the CLI).
- **O3 Leak check:** for every `no_leak` case, none of the strings appear in any search hit the
  viewer gets (k = 50). The metric is `leak_rate` = leaking cases / `no_leak` cases.
- **O4 Contract check:** each case runs through the real graph with a scripted LLM that cites
  everything it was given. Verify must leave valid citations only, and campaign cases must
  receive only the viewer's entries (no `no_leak` text in any LLM input).

### Live evaluation (`dungeonmind eval live`, needs a key)

- **L1** Every case runs through the production engine: rules, lookup, probability and
  encounter cases through `run_chat`; campaign, prep and follow-up cases through `Hub.ask` as
  the case's viewer on a throwaway fixture hub.
- **L2 Per-case scores:**
  - `intent_ok`
  - `citations_ok` (no violations)
  - `grounded` (at least one expected source cited, when expected)
  - `contains_ok`, `numbers_ok`
  - `abstain_ok`, `decline_ok`, `edition_ok`
  - `no_leak_ok`

  A case passes when every score that applies is true. Each case records its latency, tokens
  and cost per step.
- **L3 Budget:**
  - `--estimate` prints the expected cost from per-category token estimates and makes no call.
  - `--max-cost` (default $2.00) stops before the next case once spending reaches it, and
    reports the cases not run.
  - `--limit` and `--category` select a subset.
  - The run refuses to start without `--yes` once the estimate exceeds the budget.
- **L4 Results:** `evals/results/<label>.json` holds:
  - the git commit, a dirty flag, the dataset version, the model configuration and a timestamp;
  - per-case scores;
  - per-category and overall pass rates, p50/p95 latency, and total and per-step cost.

### Model choice per step (M8)

- **M1** `ModelConfig` maps each step (`route`, `contextualize`, `rules_answer`, `agent`,
  `extract_encounter`, `present_encounter`, `campaign`) to a model and an effort. The default
  is today's behaviour: Opus 5.5 everywhere; `route`, `contextualize` and `extract_encounter`
  at low effort, the rest at medium.
- **M2** It is set by `DUNGEONMIND_MODELS="route=claude-haiku-5-5,agent=claude-sonnet-5-5:low"`
  or by `--models` on `chat`, `serve` and `eval live`. Unknown steps, models or efforts are an
  error naming the valid ones.
- **M3** `ClaudeLLM` records one usage entry per call: step, model, input, output, cache-read and
  cache-write tokens, latency, and cost from `PRICING`. Prices per MTok (from the Claude API
  reference, 2026-10-06):
  - Opus 5.5: $4 input, $20 output, $0.20 cache read
  - Sonnet 5.5: $2, $10, $0.20
  - Haiku 5.5: $0.10, $0.50 (prompts ≤ 100K), cache read 10% of input

  Cache writes are 1.25× input.
- **M4** The refusal fallback (`server-side-fallback-2026-07-01`, `fallbacks: "default"`) is sent
  only to models that support it: Opus 5.5 and Sonnet 5.5. Haiku 5.5 has no server-side
  fallback, so its requests omit both.
- **M5** `dungeonmind eval compare A B` prints per-category pass-rate deltas, cost and latency
  side by side. The decision on which steps can use a cheaper model is recorded in the
  ROADMAP from a live comparison run.

### Regression gates

- **G1** `evals/gates.json` holds thresholds. `dungeonmind eval gate` runs validation and the
  offline evaluation and exits non-zero on any breach. Initial thresholds:
  - SRD R@5 ≥ 0.85
  - notes R@5 ≥ 0.90
  - `leak_rate` = 0
  - O4 violations = 0
  - dataset valid
- **G2** Given a live result (`--live evals/results/x.json`), it also checks the overall pass
  rate ≥ 0.85, `no_leak_ok` = 100% and `citations_ok` ≥ 0.95.
- **G3** CI runs `eval gate` (offline) with the tests in M9.

### M8 review fixes (backend + security review)

Each item has a regression test.

- **R1** Every case gets `completed_ok` (`stop_reason` is completed or needs_input). An error or
  out-of-budget answer never passes, even where abstaining is expected. `abstain_ok` also
  requires that the answer isn't a decline.
- **R2** Intent labels follow the router's rules:
  - spell and monster details (rules-31, 32, 33, 34, 36, 39, 40) are `lookup`;
  - the XP-budget question (rules-14) is `encounter`.

  For questions routed to tools, a cited tool counts as grounding only when its output names
  an expected source as a whole value (e.g. `"name": "Fireball"`). The XP-budget question is
  grounded by a cited encounter tool; an unrelated tool proves nothing (re-review).
- **R3** Grounding compares whole titles parsed from citation labels (the page and the journal's
  "(author, session)" suffix are stripped). "Fireball" no longer matches
  "Delayed Blast Fireball".
- **R4** Expected numbers must appear as percentages (probability cases) or as XP amounts
  (budgets). A stray "d20" or "AC 15" doesn't count.
- **R5** `forbid` entries are regexes. Injection cases check for obeying the payload
  (`PWNED\W*$`), not for mentioning it in a summary.
- **R6** Budget:
  - the runner is told the budget left, and stops a case's next model call once it is spent;
  - a failed case still records the money it spent;
  - evals cap the agent at 4 turns, so one case can overshoot by at most one call.
- **R7** The live gate accepts only a complete run of this dataset version with a `no_leak_ok`
  score: not stopped early, and covering every case.
- **Other fixes:**
  - O4 covers follow-up cases with their history.
  - Usage is priced by the model that answered (a refusal fallback may switch it).
  - Several loose expectations were tightened: encounter-10, lookup-22, lookup-23,
    campaign-20 and campaign-22.
- **L1 clarified:** cases with a viewer run through `Hub.ask`; the others, including follow-ups
  without a viewer, run through `run_chat` with their history.

## M8b contract — local open-source models (Ollama)

The owner's budget is small, so DungeonMind can run on a free local model through
[Ollama](https://ollama.com) and switch to paid Claude with a flag. On this development Mac
(Intel i5, 8 GB, no usable GPU) only ~3–4B models fit and a question takes about 1–2 minutes.
Local mode is for free development and trying things; quality is measured, not assumed.

- **P1 Provider flag.** `--provider claude|ollama` on `chat`, `serve` and `eval live`, defaulting
  to `$DUNGEONMIND_PROVIDER` and then to `claude`.
  - `claude` behaves exactly as before.
  - `ollama` needs no API key, and its usage costs $0.
- **P2 Models.** In Ollama mode every step defaults to `qwen3:4b`, overridable with `--models`
  (e.g. `all=llama3.2:3b`).
  - Any Ollama model name is accepted (`[A-Za-z0-9._:/-]{1,100}`).
  - Claude mode still accepts only priced Claude models.
  - `$DUNGEONMIND_OLLAMA_URL` sets the server; the default is `http://127.0.0.1:11434`.
- **P3 Same behaviour, different transport.** `OllamaLLM` reuses `ClaudeLLM`'s prompts,
  schemas and parsing, and replaces only the request.
  - It calls `POST /api/chat` with `stream: false`, `think: false`, `options.num_ctx` = 8192
    (the prompts are ~1–6k tokens; Ollama's default context would truncate them), and the
    step's JSON schema as `format`.
  - The graph's verify step, citations and visibility rules are unchanged.
- **P4 Tools.** The agent's tool definitions are sent as Ollama function tools, and the
  conversation's tool-use and tool-result turns are translated to Ollama's assistant
  `tool_calls` and `tool` messages. A tool call becomes an agent tool call with a generated id.
  A final reply that isn't the answer JSON is kept as the answer text, with its inline
  citations, `confidence: low`, so a small model's plain-text answer still goes through verify.
- **P5 Errors.** If Ollama isn't running or the model isn't pulled, the CLI says so before
  running, with the commands to fix it (`ollama serve`, `ollama pull qwen3:4b`). During a run,
  any transport error becomes the graph's usual error answer.
- **P6 Timeouts.** Ollama mode allows 300 s per call and per agent loop (Claude: 60 s), because
  CPU inference is slow.
- **P7 Usage.** Each call logs `prompt_eval_count` and `eval_count` as input and output tokens,
  at cost $0. `eval live --estimate` shows $0.

### M8 live findings (first full Haiku run: 190/209, 91%, $0.18, p50 4.7 s)

Each item has a regression test.

- **H1 The router knows when it is inside a campaign.** `route` gains keyword-only
  `campaign: bool`. The graph sets it when `search_notes` is bound (the hub). The prompt then
  says that questions about the campaign's people, places, events, sessions, items or plans are
  `campaign_memory`, never `out_of_scope`. This was observed with "Why are grain prices so
  high?" and "What's on our shopping list?", which were declined in the hub.
- **H2 "Not found" is an answer, not a tool failure.** `get_entity` for a name not in the SRD
  returns `{"result": {"found": false, "message", "suggestions"}}`. It can be cited as evidence
  that the SRD lacks the creature or spell (observed with Beholder, Mind Flayer, Strahd and
  Tiamat, where correct abstentions lost their citation).
- **H3 Scoring.** Expected numbers are percentages when the case has `args`, expects the
  `probability` intent, or is in the probability category. A case's `intent` may list
  acceptable routes, e.g. `["session_prep", "campaign_memory"]`.
- **H4 Dataset.**
  - Widened `srd` for rules-19, rules-39 (Telepathic Bond also fits) and followup-03.
  - rules-35 accepts `lookup` or `rules_qa`; prep cases accept `campaign_memory`.
  - unanswerable-06 now asks for an off-SRD NPC's stats.
  - injection-06 now carries the payload in a party note, not in the user's own question.
- **Known limits (not fixed):** paraphrased questions can miss the right SRD section
  (rules-21: Concentration) or note (campaign-40). The model sometimes writes an invalid
  citation id; verify removes it.

## M8d contract — real campaigns: Turkish notes and the Obsidian importer

The owner's real campaign is an Obsidian vault of 66 Turkish notes, plus 4 session notes. Two
things in the search stack are English-only, and the vault needs an importer. **The owner's
vault is read-only for DungeonMind:** nothing in it is ever written, moved or deleted. **Real
campaign content is never committed** (the repository is public). It lives only in the
git-ignored `data/hub/`, and tests use a small invented Turkish fixture.

### Language

- **T1 Unicode keyword search.** `tokenize` splits on Unicode word characters, then folds
  case and diacritics: ç→c, ğ→g, ı/İ→i, ö→o, ş→s, ü→u, â/î/û→a/i/u. "Kızıl Şafak Köyü" gives
  `kizil safak koyu`, and a question typed without Turkish letters still matches. English text
  tokenizes as before (ASCII is unchanged), apart from Turkish stopwords added to the list.
- **T2 Multilingual note embeddings.** New campaigns embed entries with
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-d, ~220 MB, 50+
  languages). The SRD keeps `bge-small-en-v1.5`, which its benchmark is measured on. Each
  `campaign.db` records the model that embedded it. A campaign whose model differs from the
  hub's note embedder can still be read and listed, but search and writes stop with a clear
  "re-index this campaign" message (this closes tracked item H4).
- **T3 Cross-lingual rules search.** A question that isn't English (it contains non-ASCII
  letters) gets one extra LLM call, `english_query(question)`, which returns an English search
  query of at most 200 characters. That query is used to retrieve SRD passages (rules answers
  and the campaign node's SRD evidence). Note search keeps the original question, since the
  note model is multilingual. The agent prompt tells the model to write `search_rules` queries
  in English. Any failure falls back to the original question, with a trace warning.
- **T4 Answer language.** Every answer prompt says to answer in the language of the user's
  question. SRD citations keep their English section names.

### Importer (`dungeonmind hub import-obsidian`)

```
dungeonmind hub import-obsidian VAULT --dm USERNAME --campaign-name NAME
                                [--sessions DIR] [--preview]
```

- **I1 Read-only.** Files are opened for reading only. Nothing is created, changed or removed
  under VAULT or DIR (tested by comparing file lists and modification times).
- **I2 Preview.** `--preview` prints one line per resulting entry (source file → board, kind,
  visibility, title, characters) plus totals and skipped files, and writes nothing. Without
  `--preview`, the importer creates the campaign (the DM must exist) and refuses a campaign that
  already has entries.
- **I3 Mapping.** Frontmatter `kind` maps to a lore kind:
  - npc→npc, place→location, faction→faction, event→event, item→item;
  - concept, reference, pc and other values→other (pc notes are tagged "(PC)" in the title);
  - with no `kind`: notes in a folder named like `Karakterler`/`Characters`/`NPCs`→npc,
    otherwise other.

  Every lore entry is **private**. The DM reveals entries later.
- **I4 Text.**
  - Frontmatter is removed. Useful fields (aliases, status, role, race, faction, location,
    importance and every `related_*`) become a short header such as `**status:** alive`.
  - `[[Target|Shown]]` becomes "Shown", and `[[Target#Part]]` becomes "Target".
  - Embeds (`![[…]]`), images and HTML comments are dropped.
- **I5 DM secrets kept apart.** `dm_note` and `secrets` fields become a separate private
  entry, "<title> — DM notes", so revealing the main entry never reveals them.
  `dm_note: true` is a flag (the whole note is DM-only), not secret text: the entry gets a
  "**DM only:** yes" line and no separate entry (found on the owner's vault).
- **I6 Limits.**
  - A note over 10,000 characters is split at paragraph breaks into "<title> (1/n)".
  - Excalidraw drawings, `.base` files and empty notes are skipped, and listed in the preview.
  - Two notes with the same title: identical text is imported once; different text gets a
    folder suffix.
- **I7 Sessions.** Each `.md`/`.txt` file in `--sessions` becomes a private journal entry. Its
  session number is the first number in the file name.
