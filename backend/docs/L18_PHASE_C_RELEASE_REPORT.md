# L18 Phase C — Stories / Biography Release Report

Status: local implementation and automated acceptance complete; production release preflight pending. Report date: 2026-09-08.

## Scope

Phase C adds the native Stories product experience on top of accepted L18 Phase B. It does not implement L19 Visual Speaking Companion.

## Product

- Added Stories beside Memories, Media & Sources and Timeline in the builder navigation.
- Added an owner/collaborator Stories library with human-facing scope, perspective, visibility and stale labels; UUIDs and provider/audit internals are not shown.
- Added private-draft creation with the Phase B scope enum and explicit Legacy Story (first person) versus Biography (third person) selection.
- Added bounded synchronous generation, clear preparing/ready/failure status, duplicate-click fencing and request-key idempotency.
- Added chapter reader, chapter tabs, owner chapter editing as a new version, regeneration, stale review notice, publication/unpublication and archive controls.
- Added “What is this chapter based on?” support labels, including memory-only and unavailable-source states. Missing media never blocks a Story.
- Added a visitor-only published Story read path. It requires existing persona authorization, returns active published Stories only, and redacts builder provenance/audit details. Drafts and mutations are unavailable.
- Added responsive mobile layout, semantic buttons, keyboard focus states, accessible status updates and text-safe rendering of generated/user-authored content.
- Collaborators receive view-only Story UI; owner controls are not presented.

## Safety and semantics

The accepted Phase B engine remains authoritative for grounding, quotes, causality, uncertainty, perspective, zero canonical writes, optional evidence and staleness. Phase C does not write Memory, MemoryRevision, LifeEvent, SourceEvidence, Timeline or Personality from the UI. Story edits affect only Story versions.

The visitor endpoint is the only Phase C backend addition: `GET /api/v1/stories/published?legacy_id=...`. It uses `require_persona_legacy`, filters `published + active`, and strips support/audit details from the visitor response.

Full-biography generation remains bounded synchronous v1. The accepted Phase B limits and durable version/idempotency fencing are retained; no separate Story worker was introduced because the advertised v1 flow is bounded and no timeout evidence was found in local acceptance. Production latency remains an operational observation item.

## Tests

- Frontend direct Node regression: **203 passed, 0 failed**.
- Backend Story-focused tests: **11 passed, 6 skipped** (the six are the opt-in PostgreSQL cases when no live URL is configured in this invocation).
- Clean isolated backend L18 regression from accepted Phase B plus the intended visitor route: **888 collected, 832 passed, 56 skipped, 0 failed**. The temporary worktree was removed successfully after a Windows permission retry.
- Backend compileall: passed.
- `git diff --check`: passed for both repositories.
- Existing accepted Phase B PostgreSQL evidence remains **6/6 passed** on disposable PostgreSQL 17.11. No concurrency-sensitive Story domain behavior was changed in Phase C.
- Phase B live-provider synthetic smoke remains accepted: first/third person, causality, quote, conflict, prompt-injection and zero-write checks passed.

## Production release gates

No production migration, backup, deployment, Story smoke, synthetic production data, tag or production mutation has been performed in this checkpoint. The intended release SHAs are backend `8784118` (based on accepted Phase B `b993ac5e1b36a51e2d47ab926f4c3a5aae933130`) and frontend `ef92033` (based on production L17 frontend `e7e880c43a2dfff4a31444b22e5eaec69dd4f9a0`).

Required production steps remain: verify `WaffleBerry-server` / `89.167.14.211`, verify database exactly `legarya` at `0019_legacy_timeline`, create and verify a fresh non-empty backup, apply `0020_legacy_stories`, deploy the two intended committed releases without unrelated realtime edits, health-check all required services, run synthetic Story/publication/visitor/zero-write/staleness smoke, inspect sanitized logs, clean synthetic data, then create `legarya-l18-stories-biography` on the exact backend release commit.

Manual visual/browser/microphone acceptance is intentionally not a release blocker under the current policy and is recorded as **not manually browser-verified in Phase C**.

## Known limitations

No PDF/book export, claim-level provenance spans, dedicated Story worker, collaborator drafting, or visitor Story conversation integration was added. The existing unrelated active-worktree edits remain excluded: `backend/app/services/realtime_provider.py`, `backend/tests/test_realtime_l15.py`, and `backend/docs/L16_PHASE_A_ARCHITECTURE.md`.
