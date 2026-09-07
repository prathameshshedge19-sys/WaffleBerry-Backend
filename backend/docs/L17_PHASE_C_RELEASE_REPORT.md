# L17 Phase C — Timeline UI and Release Report

Status: released and accepted for the deployed Phase B backend plus Phase C frontend. Report date: 2026-09-08.

## Scope

Phase C adds the builder Timeline experience to the existing chat shell. It is a Legacy-scoped view over the Phase B API and does not introduce Stories, Biography, a second memory authority, or conversation-time persistence.

Implemented frontend files:

- `WaffleBerry-Frontend/chat.html`
- `WaffleBerry-Frontend/css/timeline.css`
- `WaffleBerry-Frontend/js/timeline-client.js`
- `WaffleBerry-Frontend/js/timeline-dashboard.js`
- `WaffleBerry-Frontend/tests/timeline-l17.test.mjs`

The UI renders explicit `date_label`/precision, approximate and unknown dates, conflict state, canonical-memory support counts and optional source support. It groups known years chronologically and keeps Date unknown separate. Empty/gap states explain that family memories remain legitimate without documentary proof.

Owners receive review, soft-delete and detail actions. Collaborators can view only. Visitor/no-role contexts do not expose the builder control. Requests remain authenticated and Legacy-scoped; stale loads are fenced with abort/epoch protection. DOM output uses safe node/text construction, mobile layout and keyboard focus states. No manual browser session was run; static tests and live asset checks were used.

## Race acceptance retained from Phase B

The two dedicated live PostgreSQL races are present in `tests/test_timeline_postgresql_l17.py` and passed on the retained disposable PostgreSQL 17.11 runtime:

1. Conflict review versus stale deterministic rebuild: a 1998/1999 conflict was resolved by the owner while a rebuild raced. The stale rebuild could not restore conflict, overwrite chronology, reintroduce alternatives or duplicate the event. The pre-fix failure was reproduced: rebuild restored `review_state=conflict` after the owner committed approval. The narrow fix is the committed-resolution fence `conflict_resolved_at`; newly attached incompatible support intentionally reopens review.
2. Source deletion/unavailability versus evidence attachment: the final effective event support never counts removed evidence as available. The event and canonical memory remain valid. The test passed without weakening optional-evidence semantics.

The other accepted Phase B race and isolation tests remain green. No worker or generation system was added.

## Verification

- Frontend focused L17 tests: **5 passed**.
- Frontend complete Node test suite: **200 passed, 0 failed**.
- Live L17 PostgreSQL suite: **13 passed, 0 failed** on PostgreSQL 17.11, fresh disposable database `l17_test_phase_b_final`.
- Focused backend L17/L14/L15/L16 integration coverage: **passed**.
- Full backend regression: **840 passed, 37 skipped**. No backend source was changed during Phase C.
- Backend `compileall`: **passed** in the deployed `.venv`.
- `git diff --check`: **passed** for the backend and frontend release diffs.

The retained PostgreSQL acceptance includes migration 0019 upgrade/downgrade/re-upgrade, schema checks, scoped foreign keys, uniqueness, duplicate admission, source unavailability, conflict/rebuild, deletion and rollback coverage.

## Production release

Target verification identified host `WaffleBerry-server` at `89.167.14.211`, database `legarya`, and pre-release revision `0018_media_intelligence`.

- Pre-migration backup: `/home/waffleberry/backups/l17_pre_0019_20260908.dump`, **190272 bytes**. No credentials or customer content are recorded here.
- Migration: `0018_media_intelligence` → `0019_legacy_timeline`, then verified at `0019_legacy_timeline (head)`.
- Backend release: `693e70b0d486c52455782edc2f56464869d6f277`.
- Tables verified: `life_events`, `life_event_memories`, `life_event_evidence`, `life_event_entities`.
- Services verified active: backend, personality worker and media worker.
- Health: `{"status":"ok","service":"legarya-backend"}`.
- Timeline routes are present in the live OpenAPI surface.
- Frontend release: `e7e880c43a2dfff4a31444b22e5eaec69dd4f9a0`.
- Public `https://www.waffleberry.app/chat.html`, Timeline JS and CSS returned HTTP 200 and contained the deployed Timeline contract.

The production database has six Legacies, ten memories and the documented synthetic Legacy IDs 7 and 8. No customer content was inspected. A write-side production backfill was not run: the explicit no-production-modification constraint and the safety gate prohibit derived writes even for those existing synthetic records. The bounded rebuild and all race/backfill behavior were exercised against the fresh disposable PostgreSQL acceptance database instead. Production verification was read-only apart from the authorized migration and service deployment.

## Release review and boundaries

The Phase C diff contains only the Timeline UI, its API client, tests, styles and this report; the pre-existing realtime/L16 edits remain unstaged and excluded. The Phase B canonical-memory authority, owner-only resolution, optional evidence, cross-Legacy isolation, idempotency and no-conversation-inference invariants remain intact.

Production was not deployed to through a worker or frontend redesign, and no tag was created until the final release review. Phase C is the final frontend phase; later work is outside this release.

