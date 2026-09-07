# L16 Phase D — Product integration and release readiness

Status: **RELEASE BLOCKED — L16 is not complete.** Local implementation and automated acceptance are recorded below. No Phase D production deployment or milestone tag has occurred.

Validation date: 2026-09-07.

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

## Formats and provider readiness

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

Smoke fixtures were synthetic, with no database connection or canonical-memory write path. No private family data was submitted. A non-disclosing comparison verified that the configured local and production provider credential matched. No credentials, key hashes or provider response contents are included here. Provider credit must be restored and representative document/photo/injection acceptance must pass before deployment. Automated regression uses deterministic providers and does not depend on live API availability.

## Production storage and infrastructure verification

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

Remaining release blockers: verified existing Hetzner project identity and private S3/SSE-C configuration; operational funded provider and live supported-format/injection acceptance; connected-browser manual acceptance; then fresh backup, production migrations/deployments, worker/log/storage checks and all production product/L12/L15 gates. Unsupported OCR and audio/video intelligence remain explicitly excluded. Existing Phase B/C bounds and production hardening assumptions still need verification; this report does not assert malware sandboxing, complete-document understanding or infrastructure erasure reconciliation that has not been demonstrated.

Production was accessed read-only for configuration/identity checks and was **not modified**. L16 remains open and untagged.
