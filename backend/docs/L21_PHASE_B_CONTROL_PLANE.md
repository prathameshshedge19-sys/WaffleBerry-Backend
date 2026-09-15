# L21.2 Voice Control Plane, Schema, Contracts, and Fake Providers

Status: implementation complete; feature-disabled by default

Baseline date: 2026-09-15

Scope: backend control plane only; no real media preparation, model, GPU, frontend, L15 seam, production migration, or deployment

## Baseline and worktree

Both remotes were fetched immediately before implementation. The exact accepted tips were verified:

- backend `origin/l20/android-support`: `15658a9fed2104fbfd635aa3c433024352435c36`
- frontend `origin/l20/android-foundation`: `94b560c91bacc30009901a42f785af6cd654532f`

Implementation is on uncommitted branch `l21/voice-cloning` in the isolated worktree `tmp/l21-phase-b-backend`. The stale dirty backend and frontend `main` worktrees were not reset, stashed, cleaned, checked out, or edited. Frontend is unchanged.

## Migration and schema

`0024_voice_profiles` has parent `0023_plan_shadow_usage`; no historical migration was edited. It creates:

- `voice_profiles`: one non-deleted aggregate per Legacy, revision/CAS state, current and desired pointers, revoke/delete timestamps.
- `voice_profile_versions`: immutable enrollment intent, monotonically numbered attempts, consent binding, nullable L21.3 preparation fields, manifest/config digests, reference binding, and per-version operation generation.
- `voice_consent_receipts`: scoped human declaration evidence with versioned copy/policy, authority basis, source category, accepted/revoked timestamps, and no inferred identity or voice content.
- `voice_assets`: private exact-object registry for `original`, `reference`, and `generated` objects. It stores only backend/key/version/key-ID metadata, hashes, byte/sample metadata, writer/expiry/purge evidence, and never encryption bytes or worker paths.
- `voice_jobs`: durable `prepare`, `synthesize`, and `purge` work with priority, retries, lease token/expiry, writer deadline, operation generation, idempotency key/digest, and safe error code.

Composite foreign keys bind profile pointers, versions, consent, assets, and jobs through `legacy_id` plus their parent IDs. UUID validity alone grants no scope. A partial unique index permits only one profile whose state is not `deleted` per Legacy. Database checks enforce all enumerated states, revision/generation/attempt bounds, lease pairs, digest lengths, Marathi production language, ready-reference completeness, and non-selection after revoke/delete.

Migration-installed PostgreSQL and SQLite triggers add the cross-row invariants that a selected active version is `ready`, a selected version cannot leave `ready`, and consent evidence is immutable. Consent permits only a one-way `revoked_at` transition; physical removal is allowed only under the marked Legacy-erasure workflow.

The real Alembic CLI and the automated PostgreSQL acceptance both verified existing chain -> `0024`, populated `0024` -> `0023`, and `0023` -> `0024`. Existing User/Legacy rows survive. Downgrade is schema acceptance only, never an erasure strategy.

## Lifecycle

Profile states are `empty`, `processing`, `active`, `revoked`, `deleting`, and `deleted`. Version states are `uploading`, `queued`, `preparing`, `ready`, `failed`, `superseded`, `purge_pending`, and `purged`.

An enrollment intent creates a new consent receipt and `uploading` candidate. It uploads nothing and invents no transcript or asset. If current A is active, A remains selected while B is desired/processing. A ready B requires explicit owner activation with expected revision and binding digest. Activation swaps the pointer atomically and queues A for purge. Revoke/delete clear both pointers in the same transaction, revoke receipts, advance operation generations, cancel preparation/synthesis, and queue retryable purge work.

## Authorization and API

Every management command freshly locks and authorizes the Legacy through the existing owner identity rule. Collaborators, visitors, outsiders, and cross-Legacy callers receive non-enumerating 404 behavior. The internal speech boundary returns immediately for `mode == "rya"` without accessing voice tables.

Added endpoints:

- `GET /api/v1/legacies/{legacy_id}/voice-profile`
- `POST /api/v1/legacies/{legacy_id}/voice-profile/enrollments` (metadata/consent intent only)
- `POST /api/v1/legacies/{legacy_id}/voice-profile/activate`
- `POST /api/v1/legacies/{legacy_id}/voice-profile/revoke`
- `DELETE /api/v1/legacies/{legacy_id}/voice-profile?expected_revision=...`

Responses expose safe lifecycle/capability metadata only. They never serialize reference transcripts, object keys, bucket/key identifiers, encryption metadata, filesystem paths, or provider secrets. Validation failures are sanitized and all responses use private/no-store and nosniff headers. Deletion remains available after feature disablement so cleanup cannot be gated off.

## Feature flags

All new settings default to false:

- `VOICE_CLONING_ENABLED=false`
- `VOICE_ENROLLMENT_ENABLED=false`
- `VOICE_MESSAGE_PLAYBACK_ENABLED=false`
- `VOICE_LIVE_ENABLED=false`

Existing `User.voice_preference`, Marin/Cedar L12 behavior, Rya, and L15 `create_response` are unchanged.

## Services and provider contracts

- `VoiceEnrollmentService`: owner-only, consent-per-version metadata reservation and enrollment idempotency.
- `VoiceProfileService`: status, atomic activation/replacement, revoke/delete, and Legacy deletion fencing.
- `VoiceJobService`: digest-bound enqueue, PostgreSQL Legacy-first `FOR UPDATE ... SKIP LOCKED` claim, lease/heartbeat/retry/terminal failure, generation-fenced publication, and purge finalization.
- `LegacySpeechOrchestrator`: accepts already-authoritative text and an authorized context, resolves an active Legacy version server-side, and admits a digest-bound synthesis job. It imports/calls no memory, personality, timeline, story, current-information, or answer provider. It is not connected to L15 in this phase.
- `VoiceStorage`: a narrow facade over the existing exact-key/all-version erasure implementation. It performs no SQL lock during I/O, rechecks the immutable registry identity afterward, and records positive absence before marking `purged`.
- `ReferencePreparationProvider` and `ClonedSpeechProvider`: dependency-free protocols.
- `FakeReferencePreparationProvider` and `FakeClonedSpeechProvider`: deterministic test-only metadata/PCM with injected success, failure, timeout, cancellation, malformed output, and stale completion. They are not production fallbacks.

## Job, lease, and idempotency rules

The canonical idempotency rule is enforced for both enrollment attempts and jobs: same operation/key/digest returns the same logical row; same operation/key with a changed digest returns conflict. Synthesis admission digests include Legacy, profile/version, frozen authoritative-text digest, reference binding, model manifest digest, and purpose.

Claims use short caller-owned transactions, priority ordering, lease tokens, expiry takeover, bounded prepare/synthesis attempts, and unlimited retryable purge. Heartbeats and publication require the current token. Publication re-locks Legacy -> profile -> versions -> consents -> jobs -> assets and rechecks deletion/revocation, selected/desired role, lease expiry, and the version operation generation. A stale worker cannot publish merely because it was once authorized.

## Deletion and account hook

Legacy deletion now clears voice selection, fences running/new non-purge jobs, advances generations, marks all assets `purge_pending`, and queues purge in the same transaction as the Legacy deletion marker. Readiness blocks while any voice version or asset is not positively purged. Final deletion removes voice rows child-first only after the existing media/visual and new voice readiness checks pass.

The accepted repository has no account-wide deletion orchestrator. L21.2 therefore adds the transaction-neutral internal `request_account_voice_purge(db, owner_user_id)` hook and tests that rollback remains controlled by its future parent. Wiring that hook into a real account deletion parent remains an explicit pre-production gate; this document does not claim account deletion exists.

## Security, isolation, and zero effects

Tests cover unauthenticated, owner/collaborator/visitor/outsider, stale revision, malformed/overlong idempotency metadata, sanitized errors, raw-key non-disclosure, cross-Legacy profile pointer/version/consent/asset/job links, consent non-reuse/immutability, invalid active selection, feature-off behavior, and Rya separation. PostgreSQL and SQLite both reject scoped-link violations at the database boundary.

The zero-effects snapshot proves enrollment, replacement, activation, and revoke create or change no Memory/MemoryRevision, LifeEvent, Story/StoryVersion, Conversation/Message, or BuilderActivity rows. No personality, relationship, or progression service is called by the implementation.

## PostgreSQL and test evidence

A disposable PostgreSQL 17.11 cluster was initialized under the workspace, bound only to `127.0.0.1:55432`, and used with database name `l21_test_phase_b`. Six PostgreSQL tests use independent transactions/connections and per-test schemas. They cover:

- duplicate enrollment (same and changed digest) and simultaneous different candidates;
- candidate preparation publication versus revoke;
- candidate activation versus profile delete and simultaneous activation CAS;
- lease expiry and second-worker takeover;
- stale-token publication and replacement versus old completion;
- purge/deletion fence versus late preparation completion;
- Legacy deletion versus a running voice job;
- profile delete versus synthesis publication;
- populated migration downgrade/re-upgrade, model-column parity, active-ready guard, consent immutability, and composite cross-Legacy failures.

Results at completion:

- L21 unit/service/API/migration/PostgreSQL suite: `24 passed`.
- PostgreSQL-only L21 suite: `6 passed`.
- focused auth/authorization/deletion/L12/L15/media/storage/plan regression: passed (other milestones' explicitly opt-in PostgreSQL tests skipped without their separate URLs).
- full isolated backend regression: `1419 passed, 205 skipped`; skips are existing opt-in provider/PostgreSQL suites whose separate milestone environment variables were not enabled. L21 PostgreSQL tests were enabled.
- `python -m compileall`, `git diff --check`: passed.

The two initially failing full-suite cases were stale L13 assertions that hard-coded `0023_plan_shadow_usage` as Alembic head. The compatibility test now correctly expects the additive `0024_voice_profiles` head; its three cases pass.

## Files added or modified

Application/migration:

- `backend/alembic/versions/0024_voice_profiles.py`
- `backend/app/api/routes/voice_profile.py`
- `backend/app/config.py`
- `backend/app/main.py`
- `backend/app/models/__init__.py`
- `backend/app/models/voice_profile.py`
- `backend/app/schemas/voice_profile.py`
- `backend/app/services/legacy_deletion.py`
- `backend/app/services/voice_profiles.py`
- `backend/app/services/voice_providers.py`
- `backend/app/services/voice_storage.py`

Tests/documentation:

- `backend/tests/test_personality_migration_l13.py`
- `backend/tests/test_voice_api_l21.py`
- `backend/tests/test_voice_control_plane_l21.py`
- `backend/tests/test_voice_deletion_l21.py`
- `backend/tests/test_voice_models_l21.py`
- `backend/tests/test_voice_postgresql_l21.py`
- `backend/tests/voice_l21_helpers.py`
- `backend/docs/L21_PHASE_B_CONTROL_PLANE.md`

## Deferred to L21.3 and later

L21.3 owns consent/product copy approval, audio/video upload reservation and validation, browser/Android recording or picker changes, isolated ffmpeg/VAD, exact reference selection, Whisper transcription of that exact segment, real protected original/reference retention, and real preparation-worker publication.

IndicF5, Vocos, pinned weight manifests, GPU deployment, real cloned speech, message playback, L15 answer/speech separation, cloned Live Call audio, frontend UI, and production rollout remain later gated phases. No model/library/weight was installed, no `HF_TOKEN` was used, no production resource was accessed, no production migration ran, and no commit, push, tag, or deployment was performed.
