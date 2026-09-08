# L18 Phase C — Stories / Biography Release Report

Status: deployed and migrated, but final L18 acceptance/tagging is blocked because authenticated disposable production Story smoke data/session was unavailable. Report date: 2026-09-08.

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

Production preflight verified `WaffleBerry-server` at `89.167.14.211`, all three required services active, health 200, database exactly `legarya`, and revision `0019_legacy_timeline`. The initial direct dump failed because the protected backup directory is mode 0700 and the `postgres` process could not write there. The safe staging resolution used restrictive `/var/tmp` staging, custom-format `pg_dump`, `pg_restore --list`, and privileged local `install` into the existing directory. Final verified backup: `/home/waffleberry/backups/l18_pre_0020_20260908T000945Z.dump`, timestamp `2026-09-08 00:09:45 UTC`, **211293 bytes**, owner `waffleberry:waffleberry`, mode `0600`; staging was removed.

Migration `0019_legacy_timeline` → `0020_legacy_stories` succeeded, and the four expected tables/schema objects were verified. Backend commit `a9f2104d8d870a06b94ffd0c7d2b4488b9ec011c` is deployed; all required services are active, health is 200, the checkout is clean, and recent logs contain no Story startup/error/private-content findings. Frontend commit `ef92033` is pushed and its builder/visitor HTML, CSS and JS assets return HTTP 200 with expected markers. Unauthenticated Story endpoints correctly return 401.

Final production Story acceptance remains required: an authenticated disposable owner/visitor setup must run memory-only, perspective, grounding, edit/version, regeneration, staleness, publication, visitor, collaborator and cross-Legacy checks, followed by synthetic cleanup. No disposable production Story data was created and no customer data was touched. The release is stopped before tagging because those authenticated smoke gates were not honestly verifiable without a disposable verified account/session; no direct database fixture or minted token was used.

Manual visual/browser/microphone acceptance is intentionally not a release blocker under the current policy and is recorded as **not manually browser-verified in Phase C**.

## Known limitations

No PDF/book export, claim-level provenance spans, dedicated Story worker, collaborator drafting, or visitor Story conversation integration was added. The existing unrelated active-worktree edits remain excluded: `backend/app/services/realtime_provider.py`, `backend/tests/test_realtime_l15.py`, and `backend/docs/L16_PHASE_A_ARCHITECTURE.md`.
