# L16 Phase D — Product integration and release readiness

Status: **STORAGE ACCEPTED; remaining production release gates pending.** Local implementation and automated acceptance are recorded below. No Phase D application deployment or milestone tag has occurred.

Latest production release work: authoritative host/database verification and live private SSE-C storage acceptance passed. The protected production environment now contains the authorized storage settings; media remains disabled pending deployment. Manual browser UX acceptance is expressly waived as a release blocker.

Validation date: 2026-09-07.

## Protected storage configuration and live acceptance — 2026-09-07

The user created private bucket `waffleberry-legarya-media-prod` in the existing **WaffleBerry** project, Helsinki **hel1**, and generated dedicated S3 credentials. The backend and personality worker currently load `/home/waffleberry/WaffleBerry-Backend/backend/.env`; the accepted media-worker unit uses that exact same file. Its owner/group are `waffleberry:waffleberry`, mode **0600**. The media unit is not installed yet.

A root-only server helper at `/root/legarya-l16-secrets.py` prepared bucket/endpoint/region settings with `MEDIA_ENABLED=false`, generated 32 random bytes for SSE-C without printing them, and atomically wrote the protected environment while preserving ownership/mode. The user entered credentials directly through hidden terminal prompts; no values appeared in chat, command arguments, Git or helper output. The second invocation retained the existing SSE-C key. No service was restarted. This is an authorized production configuration change; the earlier no-mutation preflight below is historical.

The environment key uses `base64:<encoded random bytes>`. A necessary narrow adapter fix decodes that explicit prefix to exactly 32 bytes before boto3's own request encoding, avoiding double encoding. Invalid prefixed encodings/lengths fail closed; existing unprefixed configurations retain their prior behavior. The example environment documents the format. Six new tests cover actual botocore header encoding, put/read/head use, malformed values and compatibility. No database, processing, authorization or promotion semantics changed.

Live storage smoke ran on the authoritative production server against `https://hel1.your-objectstorage.com`, using a temporary isolated Python environment, one random synthetic object key and non-private fixture text. **9/9 checks passed**:

- Bucket ACL grants no public/anonymous group access.
- Anonymous bucket listing is denied.
- Upload confirms SSE-C AES256.
- Authenticated encrypted read returns exactly the synthetic payload.
- Authenticated read without the SSE-C key is denied.
- Authenticated read with a wrong 32-byte key is denied.
- Anonymous read even with the correct SSE-C key is denied.
- Anonymous read without the key is denied.
- The synthetic object is deleted and a subsequent read returns 404.

Only sanitized booleans/bucket/region were recorded in `backups/l16-phase-d/production-storage-result.json`; no secrets, signed URLs or source content were emitted. The cleanup journal was removed after confirmed deletion. The SSE-C key remains in the protected environment and must be retained securely for future reads.

Validation after the adapter change: **58 focused tests passed**, then **821 backend tests passed, 37 skipped, 2 warnings** in 220.69 seconds. Evidence: `sse-focused.log`, `sse-backend-full.log`, `sse-backend-final.xml` under `backups/l16-phase-d/`. Skips are opt-in PostgreSQL suites; the earlier 23-test live L16 PostgreSQL acceptance remains applicable because this fix changes storage key decoding only.

Production application revision, services and database remain unchanged at this checkpoint. Fresh backup, migration, worker installation, application/frontend deployment and production API acceptance remain outstanding. No L16 tag exists.

## Historical production preflight before storage provisioning — 2026-09-07

The user authorized production release/tagging after every automatable gate passes, and waived manual visual/browser/microphone UX as a blocker. No manual browser/product test was attempted or requested. Exact desktop/mobile layout, native file-picker interaction, keyboard/focus UX, microphone permission/capture, spoken L12/L15 interactions and subjective polish are recorded as **Not manually browser-verified in Phase D**. They are post-release observation items under the revised policy, not blockers.

Authoritative production identity was re-established through strict-known-host SSH, provider machine metadata, repository/service configuration, database queries, DNS and public HTTPS:

| Check | Observed result |
| --- | --- |
| Hetzner project | `WaffleBerry`, confirmed by the user's Hetzner Console observation; no alternate project is authorized. |
| Hostname / metadata hostname | `WaffleBerry-server` / `WaffleBerry-server`. |
| Public IPv4 / metadata | **89.167.14.211**. The incomplete old `167.14.211` text is not a release target. |
| Instance / location | Instance `157705364`; `hel1-dc2`; network zone `eu-central`. |
| Repository | `/home/waffleberry/WaffleBerry-Backend`; production Git status clean. |
| Running production revision | `4e6d13cdf35edf0ece97036467181e284e846ba9`. |
| Backend service | `waffleberry-backend.service`: loaded, active/running; working directory `/home/waffleberry/WaffleBerry-Backend/backend`. |
| Personality worker | `waffleberry-personality-worker.service`: loaded, active/running; same application working directory. |
| Active database | Configured database and `SELECT current_database()` both **legarya**. |
| Current migration | **0016_realtime_sessions**, as required before L16. |
| Public backend DNS | `89-167-14-211.sslip.io` resolves to **89.167.14.211**. |
| Public health | HTTPS `/health`: 200, expected `legarya-backend` service identity. |
| Existing frontend | `https://waffleberry.app/chat.html` redirects to `https://www.waffleberry.app/chat.html`, HTTP 200; new L16 media script not yet present. |

The host is not ambiguous. The SSH probe used a per-command Git safe-directory exception because root was reading the existing service-user repository; no global Git configuration was changed.

Object Storage target: **Helsinki `hel1`**, endpoint **https://hel1.your-objectstorage.com**, matching the confirmed backend city. This is the documented Object Storage location, not the server zone `hel1-dc2`. [Hetzner's official endpoint list](https://docs.hetzner.com/storage/object-storage/overview/) confirms Helsinki availability. Project-specific access/quota and actual bucket availability remain unverified without authenticated provisioning access. [Hetzner's S3 credential workflow](https://docs.hetzner.com/storage/object-storage/getting-started/generating-s3-keys/) generates credentials within the selected Console project. [Hetzner's SSE-C guidance](https://docs.hetzner.com/storage/object-storage/howto-protect-objects/encrypt-with-sse-c/) requires a securely retained 32-byte key and the same key for reads.

Blocking evidence:

- Production `MEDIA_ENABLED=false`; all seven media S3 endpoint/bucket/region/access-key/secret-key/SSE-C-key/key-ID settings remain absent.
- No Hetzner/S3 provisioning credentials were found in the checked process/user/machine environment or standard local and server hcloud/S3/AWS configuration locations. No credential values were printed or copied into reports.
- No applicable installed Hetzner connector/API tool was available. The fallback Console connection was checked solely for infrastructure provisioning: selection reported unavailable and discovery returned `[]`. No session/cookie/profile bypass or alternate account was used. This is lack of authenticated infrastructure access, not a manual UX gate.
- Consequently no private bucket, scoped server credentials or SSE-C configuration was provisioned. The required encrypted upload / authorized read / anonymous denial / delete / deleted-object denial smoke could not run. Privacy/encryption is **unverified**, not passed.

Release stopped before any production mutation. No fresh backup was made because migration is not imminent; a successful fresh, non-empty `legarya` backup with timestamp/path/size remains mandatory immediately before migration after storage is ready. No Alembic migration, backend restart/deploy, media unit installation, Vercel deploy, production test account/Legacy, review action, provider production smoke or cleanup mutation occurred. The media unit remains not installed (`not-found`, inactive). No new realtime/WSS/provider smoke is claimed. No L16 tag was created.

Accepted local application SHAs remain backend **634a4a04129fd99dedc5d5b687e67aa4cb01d21d** and frontend **66a4162c03b6c86b88b06162b3c2b038dfa26658**. Their required ancestor commits are present. No intended application source changed since the recorded final tests, so regression was not redundantly rerun during this read-only preflight. Only this release report changed in the repositories. Known pre-existing realtime edits and the untracked Phase A document are still excluded. Current authoritative preflight evidence is outside Git at `backups/l16-phase-d/production-release-preflight.json`; the read-only probe script is beside it.

Required next dependency: authenticated provisioning access to the **existing WaffleBerry project**, either through an available Console connection or that project's S3 credentials in the existing secure environment mechanism. Only a configuration location/connection label was requested; secrets must not be pasted into chat. The storage smoke and all remaining automatable production gates must then pass before deploy/tag. Production remains unchanged.

Latest provider-only follow-up: the structured-output failure has been reproduced, diagnosed and corrected locally. Live PDF, synthetic PNG and injection processing now pass with zero canonical writes. Full follow-up regression and fix commit are recorded below. No browser acceptance was attempted in this follow-up; the user deferred browser/product acceptance to production later. Storage and production release acceptance remain outstanding.

## Scope and product experience

Phase D adds Media & Sources to the existing active Legacy builder sidebar in `chat.html`, using the existing archival colours, typography, authentication and Legacy selection. The destination is a native dialog, not a new application/router. Homepage, SEO, domains, visitor chat, dictation and realtime implementations are outside this change.

The library shows filename, type, uploader, date and human-readable lifecycle state. Upload uses the picker or one-file drag/drop, advertised MIME/extension/size validation, idempotent reservation, interrupted-upload retry and continuation. Processing uses bounded polling (5 seconds initially, then 15 seconds, at most 60 polls); it stops on terminal state, close, page hide or Legacy change. Progress is indeterminate. No memory is represented as saved before the backend transaction succeeds.

Source detail separates private original access, extracted content and suggestions. Images and UTF-8 text have authenticated blob previews; PDFs use extracted text/page references and authenticated download. Blob URLs are revoked and private DOM content is cleared on close. Late preview responses cannot populate a closed/reopened source. Untrusted text is rendered with textContent.

Owners can inspect supporting evidence, Preserve, prepare an edited wording preview and explicitly Preserve edited memory, or Skip. Candidate version/request keys reconcile duplicate clicks and conflicts. Completion counts come from current server receipts. Skip retains the original. Source deletion requires a separate confirmation explaining that preserved canonical memories remain and provenance becomes unavailable. No fictional undo is offered.

Collaborators see only their own contributions and cannot approve, edit, skip, retry or delete sources. The UI explains owner review. Visitors have no entry or management controls; backend authorization remains authoritative. Tests exercise HTTP rejection and cross-Legacy isolation.

Existing memory cards show a restrained source/page affordance when authorized, a generic private-source label otherwise, and unavailable-source tombstones after deletion. Changed canonical wording is marked historical using the approved text hash. Existing personality evidence navigation already opens canonical memories, where this provenance is available; reads do not initiate personality rebuilds.

Mobile CSS stacks library/detail, wraps long filenames and gives review actions 44px targets. Native dialog semantics, keyboard buttons, visible focus, labelled inputs, status announcements, Escape dismissal and return focus are implemented. Real browser layout, mobile, keyboard and assistive-technology acceptance remain pending.

## Integration files and backend changes

Frontend: `chat.html`, `css/media-sources.css`, `js/media-client.js`, `js/media-sources.js`, `js/auth-api.js`, `js/auth-config.js`, `js/memory-dashboard.js`, two new L16 test files and the existing personality asset-version assertion.

Backend changes are narrow product integration:

- Authorized capabilities and extracted-evidence reads; uploader display name in the existing source DTO.
- Private/no-store source and review responses; authorize content requests before reading the body and enforce the reservation's size limit. End that read-only preflight transaction before network I/O so completion rechecks fresh state under the existing source lock.
- RFC 5987 UTF-8 Content-Disposition fixes downloads of non-Latin filenames.
- Read-only, permission-filtered canonical memory provenance. No source contents or private identifiers are returned to an unauthorized collaborator.
- Separate durable media-worker CLI and `deploy/waffleberry-media-worker.service`, with secure environment loading, restart policy, resource limits and sanitized cycle logging. This unit has not been installed or exercised under production systemd.
- Replace the invalid default source model `gpt-5.6-mini` with existing production model `gpt-5.5`; bound requests to 60 seconds, no automatic SDK retries and 4096 output tokens. No production environment value was changed.

No migrations, canonical promotion semantics, source/job lock order, generation fences or personality domain logic were changed in Phase D. The upload route's transaction lifetime was corrected and fresh PostgreSQL acceptance was rerun. Existing `0017_media_sources` and `0018_media_intelligence` remain the release migrations.

## Formats and initial provider readiness (historical)

The UI advertises only PDF/UTF-8 TXT (configured default 50 MiB) and JPEG/PNG/WebP (20 MiB). Limits come from the authenticated capabilities endpoint. Existing Phase B storage admission for other formats is not advertised as working intelligence. Audio/video transcription and scanned-PDF OCR are explicitly unavailable. Document coverage is capped at 32 text sections; it is not represented as complete document understanding.

| Acceptance path | Result |
| --- | --- |
| Synthetic digital PDF deterministic extraction | Ran through installed pypdf 5.9.0 and supplied page evidence to the real source provider. |
| Initial configured source-model smoke | HTTP 404 `model_not_found` for `gpt-5.6-mini`; corrected the local code/example default. |
| Existing production-compatible model smoke | HTTP 429 `credit_balance_exhausted` for `gpt-5.5`; stopped further provider calls. |
| Photo/image understanding | Adapter exists, but live output/quality acceptance remains unverified because of provider credit. |
| Scanned PDF | No OCR adapter; explicitly unsupported in the UI. |
| Audio/video | No production transcription adapter; explicitly unadvertised and unavailable in the capabilities response. |
| Prompt-injection quality | Deterministic containment/schema tests pass; planned live malicious-instruction fixture was not executed after the provider failure. |

Those initial smoke fixtures were synthetic, with no database connection or canonical-memory write path. No private family data was submitted. A non-disclosing comparison verified that the configured local and production provider credential matched. No credentials, key hashes or provider response contents are included here. The credit failure above is historical: the follow-up below reached the provider and completed bounded acceptance. Automated regression uses deterministic providers and does not depend on live API availability.

## Provider structured-output fix and live acceptance — 2026-09-07

Scope: provider schema and sanitized diagnostics only. Starting backend checkpoint: `11f97c3e556811898d555513b8cbe6561734d579`. Read the separate local acceptance handoff at workspace `backups/l16-phase-d/LOCAL_BROWSER_ACCEPTANCE_2026-09-07.md`; its earlier failed response was not retained, so its exact fields cannot be reconstructed retroactively. One bounded diagnostic call with the same synthetic PDF reproduced `source_provider_invalid_response`, caused by Pydantic `ValidationError`, and established the following six rejected fields:

| Validation path | Error type | Received structure | Expected |
| --- | --- | --- | --- |
| `candidates[0].category` | `value_error` | String, length 15, outside enum | `MEMORY_CATEGORIES` string |
| `candidates[0].entities[1].entity_type` | `value_error` | String, length 5, outside enum | `ENTITY_TYPES` string |
| `candidates[1].category` | `value_error` | String, length 26, outside enum | `MEMORY_CATEGORIES` string |
| `candidates[1].entities[1].entity_type` | `value_error` | String, length 6, outside enum | `ENTITY_TYPES` string |
| `candidates[2].category` | `value_error` | String, length 15, outside enum | `MEMORY_CATEGORIES` string |
| `candidates[2].entities[2].entity_type` | `value_error` | String, length 4, outside enum | `ENTITY_TYPES` string |

Root cause: the hand-authored provider schema declared both fields merely as `type: string`. It omitted the enums enforced by `SourceCandidateProposal.valid_category` and nested `MemoryEntityCandidate.validate_entity_type`. Strict provider JSON output could therefore satisfy the supplied schema while failing the server DTO. This was schema/validator drift, not a confidence, page locator, null, date or location failure.

Fix: `_analysis_schema()` now includes enums drawn directly from the same `MEMORY_CATEGORIES` and `ENTITY_TYPES` constants as the server validators. Document and image requests share that schema. No category remapping, coercion, fallback-to-other or weakening of Pydantic validation was introduced. Unsupported values still fail closed.

Contract comparison: candidate confidence remains a number in [0, 1]; evidence indexes remain bounded integers 0–31 and bool/negative/out-of-range variants are rejected. The provider returns evidence indexes, not locator objects. Page/text/image locators are server-produced during extraction and linked during persistence. Candidate date/location/locator fields are not allowed; date/place entities use existing entity types rather than new fields. Nullable uncertainty/summary remain supported; category/entities cannot be null. The provider requires all output fields for strict JSON mode, while internal DTO defaults continue to support existing callers. Entity alias string length is more restrictive in the request than in the shared DTO; it cannot permit the invalid enum values at issue. No unrelated DTO constraints were changed.

Diagnostics now retain/log up to 16 validation entries containing only schema-owned field paths, Pydantic error types, expected schema constraints, received JSON type/length and enum-membership boolean. Unknown keys are replaced with `<unknown_field>`. No value text, Pydantic message/context, entire response body, prompt or credentials are logged. Both document/image diagnostic branches have privacy regression tests.

Deterministic regression `test_request_schema_closes_all_six_observed_enum_gaps` preserves the observed paths, string types and lengths using redacted synthetic replacements. Before the fix: **1 failed, 14 passed**; the failure demonstrated that the request permitted a server-rejected category. After the fix the request excludes all six rejected shapes while the DTO still rejects them. Additional tests cover image/document request parity, valid nullable fields, malformed enums/nulls/confidence/indexes/extra date/location/locator fields, and diagnostics that cannot expose unknown property names or source values. New test module: `backend/tests/test_media_provider_schema_l16.py` (17 cases).

Live acceptance reused only the existing workspace disposable SQLite database `backups/l16-phase-d/browser-local-20260907/l16_browser_acceptance.sqlite3`, verified at `0018_media_intelligence`, and its local storage/fixtures. The old local worker was paused to avoid competing claims; each source was admitted/uploaded through the real source service and processed by the real `MediaIntelligenceWorker` with the configured `gpt-5.5` adapter. No production database, storage, browser or customer data was accessed.

| One-call live case | Source state | Evidence / candidates | Provenance | Canonical counts before / after upload / after processing |
| --- | --- | --- | --- | --- |
| Synthetic digital PDF | `ready` | 1 / 3 | Source/generation-linked page 1 | 0 / 0 / 0 |
| Synthetic garden PNG | `ready` | 1 / 1 | Source/generation-linked image locator | 0 / 0 / 0 |
| Malicious-instruction TXT | `ready` | 1 / 1 | Source/generation-linked text span | 0 / 0 / 0 |

All five resulting candidates remain pending/noncanonical, with no canonical-memory ID. The injection case proposed only supported benign garden information; tested instruction phrases did not contaminate candidates. No tool definitions were offered and no privileged tool-call output was emitted. No owner review/promotion was performed. The PNG is a synthetic illustration, not a real family photograph: it establishes operation of the supported image path, not broad photo-quality acceptance. Unsupported OCR/audio/video paths were not tested.

Live-call budget used: **4 total** — one diagnostic PDF call before the fix, then one post-fix PDF, one image and one injection call. SDK retries were disabled and each request was bounded to 60 seconds. No live calls are made by automated regression.

The existing disposable local worker was restored after acceptance with the new schema and observed idle with empty stderr. The disposable database/storage were retained for later product acceptance. No local API or frontend restart was needed; no browser interaction occurred.

Follow-up verification:

- Provider/media intelligence tests: **30 passed**, 2 existing warnings.
- Provider + L16 source/intelligence/product + canonical memory safety tests: **74 passed**, 0 skipped, 2 existing warnings in 8.29 seconds.
- Full backend regression: **815 passed, 37 skipped, 0 failed, 2 existing warnings in 228.32 seconds (3:48)** using `python -m pytest -o addopts= -q -ra`. The 37 skips are 23 L16 and 14 older L14/L15 opt-in PostgreSQL cases, whose separate test database URLs were not configured. The 17 new provider cases all pass. Counts reflect the worktree, including known pre-existing realtime edits that are excluded from this fix commit.
- Live PostgreSQL was not rerun: only the external provider JSON contract and validation-error diagnostics changed. No SQL, transaction, lock, generation fence, persistence, review, migration or database model behavior changed. The earlier 23-case PostgreSQL result remains historical evidence; it is not represented as a new run. Optional PostgreSQL tests will be reported as skipped in this follow-up regression.

Sanitized evidence outside Git: `provider-validation-diagnosis.json`, `provider-schema-before-fix.log`, `provider-schema-live-results.json`, `provider-schema-focused.log`, `provider-fix-focused.xml`, and `provider-fix-backend-final.xml` under workspace `backups/l16-phase-d/`. No provider response body or source text is stored in these diagnostic/result reports.

Fix checkpoint: the commit containing this follow-up, titled `fix(l16): align media provider structured output` (exact SHA in the handoff; resolve with `git log -1 --format=%H -- backend/tests/test_media_provider_schema_l16.py`). Full regression, live checks and final diff review passed before committing. Files are only `backend/app/services/media_intelligence.py`, `backend/tests/test_media_provider_schema_l16.py`, and this report. The existing realtime edits and untracked Phase A document remain excluded. No push, deployment, tag or production modification is authorized/performed in this provider-only follow-up.

## Initial production storage and infrastructure verification (historical)

Read-only SSH checks verified the backend host's metadata: instance `157705364`, availability zone `hel1-dc2`, metadata region `eu-central`. This is backend location evidence, not proof of an Object Storage account/project or bucket region. Phase A/B documentation does not establish a dedicated storage region.

The exact existing WaffleBerry Hetzner account/project label could not be verified. The host has no configured hcloud context/CLI/token providing that identity. The user explicitly authorized only the existing production account/project and instructed us to stop rather than guess when its exact identity is not verifiable. **Bucket provisioning is therefore stopped.** No alternative account or public bucket was created.

Production `MEDIA_ENABLED` is false. All required media S3 endpoint, bucket, region, access-key, secret-key and SSE-C key/key-ID settings are absent. Private bucket policy, SSE-C upload/read/delete, scoped credentials and object erasure have not been validated against production storage. Server-only configuration is implemented in Phase B; no credentials are exposed to the frontend.

Binary content transfer uses only the fixed existing backend HTTPS origin and the existing authorized content route. Token refresh remains on the normal same-origin API. JSON API routing and existing Vercel rewrites remain unchanged. Actual production CORS, proxy upload limits and private large-file transfers still require deployment readiness/acceptance.

## Automated validation

Commands use the repository Python environment and Node native tests. Local test outputs are retained outside Git under workspace `backups/l16-phase-d/`.

| Check | Exact result |
| --- | --- |
| Focused frontend: `node --test tests/media-sources-l16.test.mjs tests/media-auth-l16.test.mjs` | 64 passed, 0 failed, 0 skipped. |
| Full frontend: `node --test tests/*.test.mjs` | 195 passed, 0 failed, 0 skipped. |
| Static production assets: `python backups/l16-phase-d/validate_static.py` | Passed: syntax of 46 JavaScript files, resolution of 25 local chat assets and existing Vercel rewrite. |
| Static release bundle | `git archive` of frontend commit `66a4162`, restricted to HTML/CSS/JS/assets and deployment metadata: 89 files, 982577 bytes; ZIP integrity and required L16 assets verified. Saved outside Git as `backups/l16-phase-d/frontend-static-66a4162.zip`. Existing uncommitted realtime edits are excluded. |
| Build/lint/typecheck workflow | Static HTML/CSS/JS repository; no package.json or separate build/lint/typecheck command. No Vercel build/deployment was run. Static checks do not establish browser rendering acceptance. |
| New backend product HTTP tests | 11 passed, 0 failed, 2 existing deprecation warnings. |
| Focused L16 backend (six source/intelligence/product SQLite and PostgreSQL modules) | 58 passed, 0 skipped, 0 failed, 2 existing warnings in 16.57 seconds. |
| Full backend: `python -m pytest -o addopts= -q -ra` with disposable L16 PostgreSQL URL | 821 passed, 14 skipped, 0 failed, 2 existing warnings in 232.79 seconds (3:52). All 23 L16 PostgreSQL cases passed within this run. |
| Live PostgreSQL L16 concurrency | Original 22 cases passed in 11.89 seconds; final 23 cases all passed within the 58-case focused suite after the upload-route fix. |

Frontend tests exercise controller logic, actual panel rendering through a lightweight test DOM, exact API contracts, upload retry, privacy, roles, review receipts, deletion confirmation and stale preview handling. They are not a substitute for a real browser. The full suite protects existing auth, builder/workspace, personality, L12 and L15 client behavior. The asset-cache version bump required updating the existing personality script-order test's expected memory-dashboard version; the load-order invariant remains unchanged.

Backend product tests cover the upload/content contract including a Unicode filename; worker processing without canonical writes; capabilities roles; private evidence and cross-Legacy denial; collaborator contribution without review rights; Preserve, versioned Edit + Preserve and Skip; canonical provenance redaction/tombstones; historical evidence and sanitized worker output.

The 14 full-suite skips are older opt-in PostgreSQL tests: 12 L15 realtime and 2 L14 turn cases whose separate disposable test URLs were not configured. Existing Starlette/httpx and AnyIO BlockingPortal deprecations account for the two warnings. Full regression includes canonical memory, personality, authorization, text and realtime unit/integration tests; live L12/L15 production smoke remains a separate unrun gate.

## Fresh live PostgreSQL acceptance

Environment: PostgreSQL **17.11**, x86_64 Windows/MSVC, using the retained verified runtime and a newly initialized disposable cluster. Binding: **127.0.0.1:55439** only. Database: **l16_test_phase_d**, dedicated random SCRAM credential. Production `legarya` was never used for tests.

Migration validation upgraded a fresh database through `0017_media_sources` then `0018_media_intelligence`, inspected tables/indexes/composite constraints, checked constraint validity, downgraded to `0017` and re-upgraded to `0018`. All passed. No migration code changes were needed.

Executed race/transaction scenarios:

- Source claim versus deletion; stale Phase B completion after deletion; retry versus deletion; duplicate processing-job admission/idempotency.
- Preserve versus Preserve, Preserve versus Skip, Edit + Preserve versus Preserve, and Preserve versus source deletion.
- Stale Phase C completion and deletion while provider work is in flight: no evidence/candidate/canonical resurrection.
- Duplicate worker publication/retry retains review state and publishes once; identical review request keys share one final receipt.
- Atomic rollback of promotion, including canonical memory, links and side effects.
- Composite cross-Legacy artifact/job/candidate-job/candidate-evidence/memory-provenance foreign keys (five parameter cases).
- Stale-generation failure callback; review after committed deletion; dashboard memory edit versus source Preserve.
- Delete during upload body arrival after the new preflight authorization read: commit deletion in an independent PostgreSQL session before releasing body bytes; reject completion with HTTP 409, retain deleting generation 2, and publish no original artifact.

Diff inspection identified an additional route-level race introduced by Phase D's early authorization read: SQLAlchemy could retain its earlier uploading source object while deletion committed during body arrival. The new PostgreSQL test reproduced acceptance of that stale upload (1 failed before the fix). Ending the read-only preflight transaction with rollback expires its snapshot and lets the existing locked receive operation read current state. The focused suite then passed all 58 tests, including all 23 PostgreSQL cases. No intended deletion or review semantics were weakened.

The deleted-source resurrection fences now pass, including the new upload race. Fresh evidence is `postgresql-final.xml`/`postgresql-tests.log` (original 22), `upload-race-before-fix.xml`, `focused-final.xml` (final 23 plus 35 SQLite cases), `migration-result.json` and `migration.log`.

Disposable cluster cleanup completed after final regression: pg_ctl stopped the dedicated server, the resolved workspace cluster path was checked before deletion, and cluster/temporary connection credentials were removed. Loopback port 55439 no longer responds. Sanitized test/migration logs, fixture and the previously verified runtime remain outside Git; no permanent database or service was introduced. Cleanup evidence: `backups/l16-phase-d/cleanup.txt`.

## Local browser and production release gates

No connected browser was available (`agent.browsers.list()` returned an empty list). The user was given connection setup instructions. Manual owner/collaborator/visitor flows, desktop/mobile preview/edit interaction, New Chat, Rya/Legacy text, L12 dictation, L15 voice entry, Memories and Personality browser smoke are **not yet accepted**. No browser pass is inferred from unit tests.

| Production gate | Verified state / action |
| --- | --- |
| Database identity | Read-only query confirmed exactly `legarya`. |
| Current migration | `0016_realtime_sessions`; unchanged. |
| Fresh pre-migration backup | Not created: release stopped before production mutation. A fresh verified, non-empty `legarya` backup remains mandatory immediately before migration. No backup path is claimed. |
| Migration `0017` / `0018` | Not applied to production. |
| Backend | Existing revision `4e6d13cdf35edf0ece97036467181e284e846ba9`; service active when checked. No restart/deploy. |
| Personality worker | Existing service active when checked; unchanged. |
| Media worker | Local entrypoint/service file prepared; not installed or started in production. |
| Frontend | No Vercel deployment. Authoritative L15 checkpoint is `65db0b172d42e8ed797ec75f9890a3849df95c73`; no new production frontend SHA is asserted. |
| HTTP health and production product smoke | Not run as Phase D release acceptance. |
| Source privacy / storage / deletion smoke | Pending real private storage configuration and deployment. |
| Production text, L12 and L15 WSS/provider connection | Not run as Phase D production acceptance; existing services were not modified. |
| Observability | Sanitized local worker logging tested; production upload/claim/retry/review/deletion/storage-event coverage and log inspection remain pending. |
| L16 tag | Not created. |

## Commit scope, limitations and final status

Frontend local implementation commit: `66a4162c03b6c86b88b06162b3c2b038dfa26658` (`feat(l16): add media sources and owner review UI`). Backend integration/report checkpoint is the commit containing this report, titled `feat(l16): integrate media product APIs and worker`, based on accepted Phase C `0b9943fd2f1b93279f955523003c97bc25cdd3ea`. Its exact SHA is reported in the handoff and can be resolved with `git log -1 --format=%H -- backend/docs/L16_PHASE_D_RELEASE_REPORT.md`. These are implementation checkpoints, not accepted production release commits.

Both intended diffs were inspected, including new API/client/worker/provenance/test files. Staged whitespace checks passed. One scoped local implementation commit per repository is used; nothing is pushed or deployed as part of this checkpoint.

Known pre-existing edits are excluded: backend `app/services/realtime_provider.py`, `tests/test_realtime_l15.py` and untracked `docs/L16_PHASE_A_ARCHITECTURE.md`; frontend `js/realtime-worklet.js` and `tests/realtime-playback-l15.test.mjs`. Regression results are for the local worktree including those existing edits. No secret, runtime cluster, synthetic fixture, temporary credential or backup is included in either repository's intended commit.

Current release blockers: authenticated Object Storage provisioning access within the now-confirmed WaffleBerry project and verified private S3/SSE-C configuration; fresh backup, production migrations/deployments, worker/log/storage checks and all automatable production product/L12/L15 gates. Manual visual/browser/microphone UX acceptance is waived as a release blocker by the latest user instruction and remains explicitly unverified. Bounded local document/image/injection provider acceptance passes; this does not establish production or broad photo quality. Unsupported OCR and audio/video intelligence remain explicitly excluded. Existing Phase B/C bounds and production hardening assumptions still need verification; this report does not assert malware sandboxing, complete-document understanding or infrastructure erasure reconciliation that has not been demonstrated.

Production was accessed read-only for configuration/identity checks and was **not modified**. L16 remains open and untagged.
