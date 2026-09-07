# L16 Phase B — Media Library Foundation report

Status: implemented locally on 2026-09-07. Production remains unchanged.

## 1. Scope implemented

Phase B establishes the private source-library control plane only:

- first-class Legacy-scoped source records;
- immutable original-artifact registry;
- durable extraction/purge job records and lease claims;
- local test/debug storage and an S3-compatible production adapter boundary;
- upload reservation and bounded binary receive lifecycle;
- filename, MIME, signature, UTF-8 and size validation;
- owner/collaborator authorization and visitor denial;
- source-scoped private reads, deletion fencing, purge and retry behavior;
- authenticated FastAPI routes for reservation, receive, list, metadata, content, delete and retry;
- backend tests for the source lifecycle and canonical-memory safety.

Phase C work is intentionally absent: OCR, document parsing, transcription segments, vision analysis, candidate evidence, candidate memories, review actions, source-to-memory links, personality integration, model calls and UI.

## 2. Phase A decisions followed

The complete Phase A architecture was read before implementation. The three-table foundation is the narrow Phase B subset of its seven-table proposal. `source_evidence`, `source_memory_candidates`, `source_candidate_evidence` and `memory_source_links` are deferred because they are intelligence/review persistence and are not structurally required to create a safe media library. The report's private storage, owner-only review, source tombstones, no-canonical-write invariant, generation fencing and PostgreSQL requirements were retained.

## 3. Schema and migration

Migration `0017_media_sources` follows `0016_realtime_sessions`. It creates:

| Table | Purpose |
| --- | --- |
| `media_sources` | Legacy, uploader, sanitized filename, declared/verified MIME and size, checksum, lifecycle/safety status, generation, metadata, timestamps and deletion tombstone. |
| `media_artifacts` | Immutable original object reference, storage backend/key/version, encryption key reference, verified bytes/hash/type and purge state. |
| `media_processing_jobs` | Extract/purge kind, pipeline version, status, stage/checkpoint, attempts, next attempt, lease fencing and fixed failure code. |

Composite `(legacy_id, source_id)` foreign keys prevent child rows crossing Legacies. Source and artifact keys are generated UUIDs; object keys are generated server-side as `legarya/legacies/{legacy_id}/sources/{source_id}/{artifact_id}`. No candidate/evidence/memory link tables were created in Phase B. No existing migration was changed. SQLite upgrade, downgrade to 0016, and re-upgrade passed. PostgreSQL offline DDL compilation passed for all three tables and scoped foreign keys.

## 4. Storage abstraction

`app/services/media_storage.py` defines `SourceStorage` with `put`, `open`, `exists` and version-aware `delete`. `LocalSourceStorage` is a private path adapter for tests/debug and rejects absolute/traversal keys. `S3SourceStorage` lazily uses boto3, requires endpoint/bucket/credentials and SSE-C customer key plus key ID, and never stores secrets in SQL or returns them to clients. Production settings fail closed if media is enabled without S3 and the full encryption configuration. The actual bucket was not created, configured or touched.

Originals are write-once; local writes use exclusive creation and S3 writes use a fresh generated key. The route returns authenticated server-mediated content with private/no-store caching and `nosniff`; there are no public URLs or bucket listings. `boto3>=1.35,<2.0` was added to backend requirements for the production adapter.

## 5. Upload lifecycle

`POST /api/v1/legacies/{legacy_id}/sources` authenticates and authorizes before creating any source row. It validates source kind, declared MIME, size, sanitized filename and UUID idempotency key, then creates an uploading source, reserved original artifact and queued extract job atomically. `PUT /.../sources/{source_id}/content` authenticates again, bounds the request stream, requires the original uploader or owner, checks expiry/state/exact reserved size, verifies signatures and UTF-8 text, writes the private object, records SHA-256/verified bytes/MIME and moves the source to `queued` with a clean safety state. Repeating the same completed body is idempotent; a different body or request metadata conflicts.

Supported foundation types are JPEG/PNG/WebP images, MP3/M4A/WAV/OGG/FLAC/WebM audio, MP4/WebM video, PDF and UTF-8 plain text documents. Archives, executables, HTML/SVG, Office/macro documents, encrypted PDFs and unsupported codecs are rejected. Phase B records bounded metadata JSON but does not probe duration/pages/dimensions; deterministic probing belongs to Phase C.

## 6. Processing-state implementation

Source states are `uploading`, `queued`, `processing`, `ready`, `partially_ready`, `failed`, `deleting` and `deleted`; safety is independently `pending`, `clean` or `rejected`. Jobs are `queued`, `running`, `retry_wait`, `succeeded`, `partial`, `failed` or `cancelled`, with a lease token/expiry pair and unique `(source,generation,kind,pipeline_version)` key.

`MediaWorker.claim()` uses a database conditional claim and generation/Legacy checks. Extract jobs transition to `processing`, then deliberately finish as `failed` with `extraction_deferred_phase_c`; no parser or model is called. Purge jobs delete all registered artifact versions and then mark artifacts purged, the job succeeded and the source deleted. Lease expiry, cancellation and generation changes fence stale workers. Retry reuses the same generation/job identity and cannot duplicate durable jobs.

## 7. Authorization and isolation

Owner access is full within the selected active Legacy. Active collaborators may reserve and receive sources and may list/read only their own submissions; they cannot delete, retry or manage another source. Visitors/viewers fail the existing builder authorization and receive no source-library visibility or mutation path. Owner-only canonical-memory DELETE remains untouched.

Every service query includes both Legacy ID and resource ID. Content access resolves the scoped source before opening an object. Composite child foreign keys, generated keys, server-controlled prefixes and 404 responses for foreign resources protect against enumeration and cross-Legacy attachment. Revocation is re-read before each operation; a revoked collaborator cannot continue using an upload reservation.

## 8. Deletion semantics

`DELETE /.../sources/{source_id}` is owner-only and changes the source to `deleting`, increments its generation, cancels queued/running extract jobs and creates one purge job. It never deletes or changes canonical `Memory` rows. `MediaWorker` performs physical artifact removal and marks the source `deleted`; object-not-found is treated as already purged. A failed storage delete moves the purge job to `retry_wait` while the source remains inaccessible. A stale extract worker cannot publish after deletion, and a retry after deletion cannot resurrect the source.

Phase B has no source-to-memory links yet, so there is no provenance tombstone to update. Phase C must add that link model before approved source-backed memories exist; it must preserve those memories on source deletion and invalidate source-dependent personality support.

## 9. API endpoints

Implemented routes are:

- `POST /api/v1/legacies/{legacy_id}/sources` — reserve an upload;
- `PUT /api/v1/legacies/{legacy_id}/sources/{source_id}/content` — receive bytes;
- `GET /api/v1/legacies/{legacy_id}/sources` — scoped library list;
- `GET /api/v1/legacies/{legacy_id}/sources/{source_id}` — scoped metadata/job status;
- `GET /api/v1/legacies/{legacy_id}/sources/{source_id}/content` — authorized private original stream;
- `DELETE /api/v1/legacies/{legacy_id}/sources/{source_id}` — logical delete and purge admission;
- `POST /api/v1/legacies/{legacy_id}/sources/{source_id}/retry` — owner retry for failed/partial extraction.

All routes are behind `MEDIA_ENABLED`, default false. Responses omit storage keys, credentials, raw errors and source content. No candidate/review API is exposed.

## 10. Security and privacy controls

The implementation uses centralized per-kind byte limits, strict kind/MIME allowlists, signatures, UTF-8 validation, generated keys, path traversal checks, exclusive local writes, private streaming, `nosniff`, `no-store`, no public object URLs and fixed error categories. It does not parse, execute, OCR, transcribe or send uploaded content to a model. Uploaded bytes are treated as data; prompt-injection text cannot reach a command or model in Phase B.

S3 credentials and SSE-C material are environment-only. The database retains only an encryption key ID. The local adapter is explicitly a debug/test boundary; production configuration refuses it when media is enabled. Logs contain no source bytes, transcript, document text, keys or signed URLs. Malware scanning, parser sandboxing, provider retention review and backup erasure are Phase C/release gates because no parser runs in this phase.

## 11. Tests added and exact results

`tests/test_media_sources_l16.py` covers owner upload, collaborator own-source boundary, foreign Legacy isolation, supported signatures, spoofed/oversized input, filename neutralization, idempotent receive, deletion/purge, stale processing deferral and zero canonical-memory writes. Existing migration tests were updated only so `upgrade head` expects the new intentional `0017` head and excludes the three new empty additive tables from the L15 historical snapshot.

Verification performed:

- focused Phase B + migration tests: passed;
- full backend suite: **774 passed, 19 skipped, 2 pre-existing dependency warnings**; the five new PostgreSQL tests are opt-in and skipped in the normal SQLite run;
- SQLite Alembic fresh upgrade, downgrade to 0016 and re-upgrade: passed;
- PostgreSQL offline migration DDL compilation: passed;
- Python compile/import and SQLAlchemy `Base.metadata.create_all`: passed;
- `git diff --check`: passed for tracked changes.

## 12. Live PostgreSQL acceptance

The opt-in `tests/test_media_sources_postgresql_l16.py` suite was run against a disposable local PostgreSQL **17.5** cluster on Windows (`x86_64-windows`, loopback `127.0.0.1:55432`) and database `l16_test_phase_b`. The cluster and database were initialized under the temporary directory and were separate from the configured `legarya` production database. The test URL guard accepts only loopback hosts and database names beginning with `l16_test`.

Alembic migrated the disposable database from the empty baseline through `0017_media_sources`; the test asserted `public.alembic_version = 0017_media_sources`, the three Phase B tables, composite foreign keys, uniqueness constraint and claim/source-generation indexes. The disposable database was truncated between cases and the local cluster was stopped and removed after validation.

The focused live result was **5 passed**:

- five synchronized concurrent source-delete versus worker-claim iterations; a claimed extract was stale/cancelled and a purge claim completed purge, while no source returned to `queued`, `processing` or `ready`;
- stale extract completion after a committed delete was fenced by the cleared lease/generation and left the source `deleting` at generation 2;
- retry versus delete raced on separate PostgreSQL connections and the final source remained `deleting` at generation 2;
- duplicate admission with the same uploader/request key across two concurrent transactions returned one source ID and one extract job;
- composite `(legacy_id, source_id)` foreign-key rejection plus live constraint/index catalog checks.

The race validation required implementation changes in `media_sources.py` and `media_worker.py`: mutating source operations now take a PostgreSQL row lock, retry also locks its extract job, and worker claim locks the source before its job. Both delete and claim therefore use the same source-then-job order, fence generation changes and avoid resurrecting a deleted source. SQLite-focused tests and the full backend regression remained green after these changes.

## 13. Files changed

L16 Phase B files:

- `backend/app/config.py`, `backend/.env.example`, `backend/requirements.txt`;
- `backend/app/models/media_source.py`, `backend/app/models/__init__.py`;
- `backend/app/services/media_storage.py`, `backend/app/services/media_sources.py`, `backend/app/services/media_worker.py`;
- `backend/app/schemas/media_source.py`, `backend/app/api/routes/media_sources.py`, `backend/app/main.py`;
- `backend/alembic/versions/0017_media_sources.py`;
- `backend/tests/test_media_sources_l16.py`, `backend/tests/test_media_sources_postgresql_l16.py` and expected-head updates in `tests/test_personality_migration_l13.py`, `tests/test_realtime_migration_l15.py`;
- this report.

The pre-existing L15 edits reported by Phase A remain untouched: `backend/app/services/realtime_provider.py`, `backend/tests/test_realtime_l15.py`, `js/realtime-worklet.js` and `tests/realtime-playback-l15.test.mjs`. No frontend product file was changed by Phase B.

## 14. Deferred Phase C work and known risks

Deferred: deterministic PDF/text/OCR/media probing, FFmpeg/audio extraction, timestamped transcription, photo/video interpretation, evidence/candidate tables, candidate review, canonical promotion, source provenance links, personality invalidation integration, source-aware memory API fields, signed range URLs, malware/parser sandbox, quotas, multipart uploads, progress polling UI and live provider quality tests.

Primary risks before Phase C/release are the conversation-bound existing memory service, cross-writer canonical locking, private-source redaction, S3 SSE-C key lifecycle, object-version erasure, upload/delete races, parser isolation and real PostgreSQL concurrency. The current defaults keep `MEDIA_ENABLED=false`; no production storage, migration, worker or route was enabled.

## 15. Recommended Phase C starting point

First add the deferred evidence/candidate schema and strict noncanonical DTOs, then implement deterministic page/time mapping and parser isolation behind fake storage/providers. Prove source deletion and stale-job invariants on PostgreSQL. Only after those pass should the existing `LivingMemoryService` gain a caller-owned reviewed-source promotion command and L13 receive source-aware approved-support links. Candidate review UI and real model providers come after those backend invariants.

## 16. Production boundary

No production database was connected to, no production migration was applied, no object-storage bucket was created or mutated, no deployment or worker restart occurred, and no frontend deployment occurred. Phase C remains unimplemented: there are no candidate extraction, evidence, review or promotion tables, APIs or workers.
