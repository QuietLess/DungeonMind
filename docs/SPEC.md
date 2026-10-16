# DungeonMind — Product Spec

## Goal

A **campaign hub** for groups playing D&D under the 2024 rules (SRD 5.2.1). A DM and their
players share a campaign. Each keeps notes that are private, shared with the DM, or visible to
the party, and each can ask a chatbot that knows the campaign (from the notes they're allowed
to see) and the rules (with SRD page citations). Dice, odds and encounter maths are done in
code. It started as a portfolio project showing RAG engineering, tool calling, bounded agent
orchestration and measured evaluation, and those standards still apply.

Source of scope: *Tolga_DungeonMind_Agentic_RAG_Blueprint.pdf* (approved plan), extended on
2026-10-16 with the campaign hub (web chat UI, notes with visibility, DM and player roles).

## Users

- **Game masters** preparing or running a session, who need fast, trustworthy
  rulings, stat blocks, and a place for their campaign notes and secrets.
- **Players** checking a rule or their odds, keeping their own notes, and sharing some
  with the party or only with the DM.
- **Recruiters / reviewers** reading the repo, who need to see measured quality,
  not screenshots.

## User journeys

| Journey | Example | Status |
| --- | --- | --- |
| Grounded rules Q&A | "How does grappling work?" → answer + SRD page citations | ✅ M2 |
| Correct abstention | "Stats of a Beholder?" → "not in the SRD" instead of invented stats | ✅ M2 |
| Structured lookup | "Level-3 fire spells", "undead CR 3–8 that can't fly" | ✅ M3 + M4 |
| Odds and dice | "+7 vs AC 18 with advantage?" → exactly 75% | ✅ M3 + M4 |
| One chat entry point | Any question → routed to the right path, cited, traced | ✅ M4 |
| Encounter planning | "Hard undead encounter for four level-5s, no flyers" → 2–3 options | ✅ M5 |
| Campaign memory | "What did we promise Elara?" → answered only from notes the asker may read | M6 |
| Session prep | "Turn my notes into 5 scenes" | M6 |
| Private notes | A player's secret backstory, shared with the DM only; the bot never shows it to other players | M6 |
| Join a campaign | DM creates a campaign and an invite code; players join from their own devices | M6 (API), M7 (UI) |
| Lore board | The DM writes NPCs, places, factions and secrets on the lore board; the bot treats it as canon and reveals an entry to players only once the DM sets it to Party | M6 |
| Character board | A player keeps their character's backstory, goals and bonds; shared with the DM by default | M6 |
| Campaign hub UI | Notes page and chat panel in the browser; citations link to the note or SRD page | M7 |

## Requirements (from the blueprint)

- Rules knowledge comes only from SRD 5.2.1 (CC-BY-4.0) with the required
  attribution; no other copyrighted books.
- Every factual rules answer cites the SRD section/page or the tool it came from;
  the model may cite only evidence it was given.
- Insufficient or conflicting evidence → say so; don't fill gaps from memory.
- Never silently mix 2014 and 2024 rules.
- Dice, probability and encounter arithmetic are done in code, never by the LLM.
- Campaign data is isolated per campaign: one database file each (M6).
- Note visibility (private / shared with DM / party) is enforced before retrieval: the model
  only ever receives notes the asking user may read. The DM can't read players' private notes.
- Chat threads are private to the user who started them.
- Quality is measured with a versioned eval set (retrieval now; 200+ questions in M7).

## Acceptance criteria by milestone

- **M1–M3:** met. See README for the commands and results.
- **M4 (orchestrator):** met. See `ARCHITECTURE.md` › M4 contract and M4 hardening; each
  criterion has an offline test using a scripted fake LLM.
- **M5 (encounter builder):** met. See `ARCHITECTURE.md` › M5 contract.
- **M6 (campaign hub backend):** see `ARCHITECTURE.md` › M6 contract. Exit: the visibility matrix
  and cross-campaign tests pass, and a recording fake LLM never receives an unreadable note.

## Out of scope for now

Voice, non-SRD content, homebrew validation (blueprint phase 2 ideas); real-time
co-editing; a mobile app. Hosting on the internet waits for M9.
