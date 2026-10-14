# DungeonMind — Product Spec

## Goal

A grounded, tool-using assistant for people running D&D games under the 2024
rules (SRD 5.2.1). It answers rules questions with page citations, looks up
spells and monsters with exact filters, does dice, odds and encounter maths in
code, and (later) remembers each campaign separately. It is a portfolio project
built to show RAG engineering, tool calling, bounded agent orchestration and
measured evaluation.

Source of scope: *Tolga_DungeonMind_Agentic_RAG_Blueprint.pdf* (approved plan).

## Users

- **Game masters** preparing or running a session, who need fast, trustworthy
  rulings and stat blocks.
- **Players** checking a rule or their odds.
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
| Encounter planning | "Hard undead encounter for four level-5s, no flyers" → 2–3 options | M5 |
| Campaign memory | "What did we promise Elara?" → only that campaign's notes | M6 |
| Session prep | "Turn my notes into 5 scenes" | M6 |

## Requirements (from the blueprint)

- Rules knowledge comes only from SRD 5.2.1 (CC-BY-4.0) with the required
  attribution; no other copyrighted books.
- Every factual rules answer cites the SRD section/page or the tool it came from;
  the model may cite only evidence it was given.
- Insufficient or conflicting evidence → say so; don't fill gaps from memory.
- Never silently mix 2014 and 2024 rules.
- Dice, probability and encounter arithmetic are done in code, never by the LLM.
- Campaign data is isolated per campaign (M6).
- Quality is measured with a versioned eval set (retrieval now; 200+ questions in M7).

## Acceptance criteria by milestone

- **M1–M3:** met. See README for the commands and results.
- **M4 (orchestrator):** met. See `ARCHITECTURE.md` › M4 contract and M4 hardening; each
  criterion has an offline test using a scripted fake LLM.

## Out of scope for now

Web UI, user accounts, voice, non-SRD content, homebrew validation (blueprint
phase 2 ideas).
