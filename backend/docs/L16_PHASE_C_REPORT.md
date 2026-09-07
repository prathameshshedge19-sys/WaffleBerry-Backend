# L16 Phase C - Intelligence and Memory Review

Acceptance date: 2026-09-07. **Accepted locally:** live PostgreSQL migration/races, focused tests and full backend regression pass; disposable cluster cleanup is complete. Production is unchanged. Phase D is unimplemented.

## Scope and architecture

Phase C adds source evidence, noncanonical candidates, owner review and canonical provenance. It reuses Phase B sources/artifacts/jobs, private SourceStorage, existing authorization, LivingMemoryService, memory revisions, builder activity and L13 personality invalidation. Processing cannot write canonical memories. Only explicit owner Preserve or Edit + Preserve promotes candidates; Skip creates no canonical memory or activity.

MediaIntelligenceService separates extraction from reasoning, with bounded chunks/candidates, stable keys, strict DTOs and generation fencing. Text uses deterministic spans; PDFs use pypdf page extraction (a limited synthetic-text fallback exists if unavailable). Audio/video require an injected timestamped transcription adapter. Image analysis offers constrained scene/object proposals and prohibits identifying people from appearance. Tests use deterministic providers and synthetic data.

Review locks Legacy, source, then candidate. Existing canonical writers share the Legacy lock with source promotion. Worker completion/failure lock **source before job**, preserving Phase B ordering. Embedding precedes the promotion write transaction; authorization/version/generation/evidence checks repeat under locks. Exact matches link an existing canonical memory. Receipt, provenance, memory/entities and legitimate activity commit atomically.

Deletion cancels pending candidates, clears all proposal/draft/evidence content, retains terminal receipts, marks approved source links unavailable and queues purge. Already approved canonical memories remain. Personality excludes source-linked memories without remaining approved support. Existing revisions retain later canonical edit/delete history; provenance records the text hash approved at promotion.

## Trusted disposable PostgreSQL provisioning

The earlier runtime blocker was resolved through approved tooling under explicit user authorization. The rejected portable-ZIP download was not retried.

- Windows Package Manager (winget v1.29.290), exact package PostgreSQL.PostgreSQL.17, package version 17.11-3, source winget.
- Official installer: https://get.enterprisedb.com/postgresql/postgresql-17.11-3-windows-x64.exe. Winget verified its hash; separate SHA-256 verification matched **2fd19749560be03020026f2d842b69af47f0ea2c7946bda17eed26a4a9235695**. Authenticode was **Valid**, signer EnterpriseDB Corporation.
- Official installer extraction with --extract-only 1, server/command-line components, no pgAdmin/StackBuilder. No system service or production configuration change.
- Actual version: **PostgreSQL 17.11 on x86_64-windows, compiled by msvc-19.44.35228, 64-bit**.
- Fresh data directory: workspace **backups/l16-phase-c-runtime/cluster**, initialized with UTF-8, locale C and SCRAM-SHA-256. No old/system data directory was reused.
- Database **l16_test_phase_c**, user l16_tester, newly generated disposable credentials. No secrets are recorded here.
- **127.0.0.1:55438 only**, verified through live listen_addresses. No production credentials or customer data.

[PostgreSQL's Windows page](https://www.postgresql.org/download/windows/) identifies EDB as its certified installer distributor; [EDB's installer documentation](https://www.enterprisedb.com/docs/supported-open-source/postgresql/installing/command_line_parameters/) documents extract-only mode.

The local acceptance driver guards the exact host/port/database. Existing tests retain their loopback/test-name guard. Only this dedicated database was reset when the uncommitted migration changed.

## Migration acceptance

Fresh PostgreSQL upgrade through the existing chain to **0017_media_sources**, then **0018_media_intelligence**, passed. Downgrade to 0017 and re-upgrade to 0018 passed. Already committed/deployed migrations, including 0017, were not edited.

All four tables were inspected live: source_evidence, source_memory_candidates, source_candidate_evidence and memory_source_links. Validation covered indexes, uniqueness, foreign keys, checks and PostgreSQL column types (JSON and timezone-aware timestamps included). All constraints were validated. Artifact/job references now include Legacy/source/generation; evidence/candidate/provenance and canonical references enforce Legacy isolation. Focused SQLite migration checks also passed.

Sanitized schema evidence: workspace backups/l16-phase-c-runtime/migration-result.json. Runtime and acceptance artifacts remain outside the backend Git repository.

## Live PostgreSQL scenarios

The original ten opt-in tests comprise **five Phase B and five Phase C cases**. All executed before coverage changes: **10 passed, 0 skipped, 0 failed, 4 warnings in 3.88s**. These tests were retained. Race helpers now accept only expected HTTP 409/410 conflicts, exposing unexpected errors.

After fixes, the expanded suite passed **22 tests, 0 skipped, 0 failed, 4 warnings in 9.21s**. All 22 passed again in the final focused suite after the final authorization/privacy/DTO and fixture-isolation changes (22 passed, 0 skipped, 0 failed; the enclosing focused run reports 2 dependency warnings).

| Scenario | Validated result |
| --- | --- |
| Phase B schema/composite FK | Invalid scoped relationships rejected. |
| Delete versus claim | Deleted source cannot be claimed for extraction or resurrected. |
| Phase B stale completion after delete | Old completion cannot mutate deleted source. |
| Retry versus delete | No extraction survives in deleted generation. |
| Duplicate processing-job admission | Concurrent requests remain idempotent under PostgreSQL uniqueness constraints. |
| Preserve versus Preserve | One terminal promotion and canonical memory; loser conflicts. |
| Preserve versus Skip | Exactly one terminal outcome; no half-preserved state. |
| Edit + Preserve versus Preserve | Version fence permits one final receipt with matching provenance. |
| Preserve versus source deletion | Either promotion conflicts, or it commits atomically before deletion and canonical memory remains with unavailable support. No personality evidence from deleted support. |
| Phase C stale completion after delete | Zero evidence/candidate/canonical resurrection. |
| Concurrent duplicate processing and retry | Barrier-controlled workers publish one evidence/candidate/link set; retry does not duplicate or reopen a skipped candidate. |
| Delete during provider work | Event-controlled in-flight worker returns stale without publishing. |
| Promotion rollback | Injected activity failure rolls back memory, entities, links, activity, candidate finalization and personality invalidation. |
| Concurrent identical request key | Same receipt/memory for both callers; one activity. Later conflicting request has no personality effect. |
| Cross-Legacy artifact/job/candidate-job/candidate-evidence/memory-provenance (5 cases) | Real foreign-Legacy resources rejected by composite FKs. |
| Stale failure callback | Old generation cannot fail a newer generation. |
| Review after committed deletion | Deleted candidate cannot promote. |
| Dashboard edit versus source Preserve | Event-controlled embedding delay cannot produce duplicate active canonical text. |

## Correctness fixes required

Three added PostgreSQL tests reproduced defects before fixes:

1. Evidence could reference another Legacy's artifact. Uncommitted 0018 and matching models now use composite artifact/job identities (legacy_id, source_id, generation, id). Targeted PostgreSQL FK rejection tests pass.
2. A stale failure callback could fail a newer generation. Failure/preflight processing reject mismatched generations and deleted/deleting sources; source-before-job order remains.
3. Dashboard edit checked duplicates before provider latency, allowing concurrent Preserve to create duplicate text. Canonical store/edit/delete and reviewed-source preservation now share the Legacy lock. Edit refreshes state and checks duplicates after locking; inactive edits return HTTP 409. Promotion uses the existing shared canonical creation command.

The first full run exposed a test-fixture isolation defect: in-process Alembic logging configuration disabled the application logger, causing the existing structured-logging test to fail (808 passed, 14 skipped, 1 failed). The two-case reproduction failed identically. Both L16 PostgreSQL fixtures now run Alembic in a subprocess; both fixtures followed by the unchanged logging test pass (3 passed, 0 skipped, 0 failed, 2 warnings). Application logging and the logging test were not changed.

Additional narrow fixes authorize owners before provider work; handle finalized/deleted candidates before proposal validation; allow Skip without a provider; erase skipped/approved proposal text on deletion while retaining receipts; reject negative/out-of-range/boolean evidence indexes; and flush human-evidence links before returning review responses. Tests cover these paths. Unapproved candidates remain outside personality evidence.

## Final verification

Commands ran from WaffleBerry-Backend/backend with private disposable L16_TEST_POSTGRES_URL. Normal tests retain SQLite isolation. No external model calls were made.

```text
python -m pytest -o addopts= -x -q tests/test_media_sources_postgresql_l16.py tests/test_media_intelligence_postgresql_l16.py tests/test_media_intelligence_l16.py tests/test_media_sources_l16.py tests/test_personality_migration_l13.py tests/test_realtime_migration_l15.py
52 passed, 0 skipped, 0 failed, 2 warnings in 39.39s

python -m pytest -o addopts= -q -ra
809 passed, 14 skipped, 0 failed, 2 warnings in 225.76s (0:03:45)
```

Focused coverage also includes owner authorization, collaborator restrictions, prompt-injection containment, candidate isolation, edited human evidence, tombstone privacy, strict output validation and migration contracts. Final in-process warnings are existing Starlette/httpx and AnyIO BlockingPortal deprecations. Alembic's existing path_separator deprecation appears in migration subprocess output.

The remaining 14 full-suite skips are older opt-in PostgreSQL acceptance: 12 L15 realtime cases and 2 L14 turn cases. Their separate test URLs were not configured for this L16 task. No L16 PostgreSQL tests are skipped: all 22 also passed within the final full regression.

JUnit evidence outside Git: workspace backups/l16-phase-c-runtime/postgresql-original.xml, postgresql-final.xml, focused-final.xml and backend-final.xml.

## Cleanup, final diff and limitations

Cleanup complete: pg_ctl stopped the dedicated server cleanly. The exact workspace cluster directory and disposable credential/PID files were removed after validating their resolved paths. The verified runtime/installer and sanitized test evidence remain outside Git for future local acceptance. No unrelated PostgreSQL directory or system installation was removed.

The complete Phase C diff was reviewed, including migration, models, services, worker, APIs, tests and this report. git diff --cached --check passes. Commit scope: migration/models/schemas/services/worker/review APIs, canonical locking, source tombstones/personality filtering, configuration/requirements, migration expectations, tests and this report. Known pre-existing realtime_provider.py and test_realtime_l15.py edits plus L16_PHASE_A_ARCHITECTURE.md are excluded. Frontend edits are untouched. No runtime, secrets or temporary infrastructure enters the commit.

Acceptance covers database correctness and deterministic regression. It does not establish live model extraction quality or production audio/video transcription: transcription requires an injected adapter, video is passed to that adapter, and no live OpenAI/vision/transcription smoke test ran. No permanent infrastructure was introduced.

Phase D frontend/upload-review UX remains unimplemented. Production was neither contacted nor modified. No deployment or milestone tag occurred.
