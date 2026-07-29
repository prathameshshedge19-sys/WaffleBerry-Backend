# Memory Storage Pipeline

**Milestone:** Phase 6.5.5  
**Service:** `backend/app/services/memory/storage_pipeline.py`  
**Public class:** `MemoryStoragePipeline`

## Architecture

The pipeline is an internal application service. It adds no route and does not
change Companion Chat or Story Guide behavior.

```text
Owner-scoped StorySession or legacy-associated Conversation
  -> existing MemoryExtractionService
  -> existing MemoryValidationService
  -> validation-status persistence policy
  -> one atomic transaction per eligible candidate
  -> MemoryStorageReport
```

`process_story_session()` and `process_conversation()` first load the Legacy
through an owner-scoped query. They then load a source constrained to that same
Legacy (and, for conversations, the same user). Source messages are loaded in
their existing deterministic order. Application code registers those trusted
records with `RegisteredProvenanceVerifier`; ownership and source identifiers
are never accepted from model output.

For Story Sessions, trusted internal processing metadata may provide a
positive `message_boundary`. The pipeline then extracts only messages through
that persisted sequence, allowing a durable extraction run to represent a
stable source version. Client requests never supply this value directly.

## Input and output

Both public methods accept a SQLAlchemy `Session`, authenticated `user_id`,
verified target `legacy_id`, and one persisted source ID.

They return `MemoryStorageReport`, defined in
`backend/app/services/memory/storage_contracts.py`. The report contains:

- source and Legacy identifiers;
- extracted, eligible, persisted, and skipped counts;
- validation status counts;
- safe per-candidate outcomes;
- related Memory and contradiction-group identifiers;
- created Memory identifiers;
- safe error codes;
- duration.

It excludes source conversations, memory excerpts, model payloads, hidden
prompts, SDK exceptions, and credentials. Extraction confidence and validation
confidence remain separate fields.

## Persistence policy

| Validation status | Pipeline behavior |
|---|---|
| `accepted` | Persist as `review_status = candidate` |
| `duplicate` | Skip and report related IDs |
| `possible_duplicate` | Skip conservatively and report related IDs |
| `possible_enrichment` | Persist independently as `candidate`, retain review links to existing memories, and do not edit them |
| `contradiction` | Persist as `candidate`, preserve both accounts, and create or reuse a same-Legacy contradiction group |
| `invalid` | Skip |
| `insufficient_information` | Skip |

No result is approved, rejected, merged, superseded, or used for Companion
retrieval automatically.

## Transaction strategy

Candidate-level atomicity is used so one malformed candidate does not discard
other valid results from the same source.

For each eligible candidate:

1. a SQLAlchemy savepoint begins;
2. the Memory is flushed;
3. all provenance, participants, tags, and required association records are
   flushed;
4. contradiction or enrichment metadata is flushed where applicable;
5. the savepoint succeeds and that candidate is committed.

Any failure rolls back that candidate, including its Memory row. Previously
committed candidates remain available, and a safe failure appears in the
report. Required provenance is checked before completion, so a Memory cannot
be committed without traceability.

## Idempotency and concurrency

The deterministic Phase 6.5.4 exact comparison remains the first duplicate
gate. The pipeline additionally computes a SHA-256 normalized fingerprint from
stable claim fields:

- Legacy ID;
- memory type and category;
- normalized summary;
- normalized participant identity/roles;
- normalized temporal references.

Confidence and extraction timestamps are intentionally excluded. The
fingerprint is not a primary key.

Migration `0003_memory_pipeline_support.py` adds a nullable fingerprint and a
unique constraint scoped by Legacy. Nullable preserves all existing rows. The
unique constraint closes the common race where two workers validate the same
new claim concurrently; an integrity conflict is safely classified as an
exact duplicate when the winning row is visible.

This milestone does not create extraction-run records or distributed locks.
Concurrent transactions can still produce separate non-exact
possible-enrichment candidates or independently discover a new contradiction
group before either transaction commits. Phase 6.5.7/6.5.8 should add a
source-processing run key or scoped locking if automatic parallel triggering
is introduced.

## Provenance guarantee

Every persisted candidate has at least one provenance record. The extractor
constructs provenance from persisted application messages, and the validator
checks it against records registered by the pipeline.

For text sources:

- only user-authored messages are valid evidence;
- the source message must belong to the requested container;
- the container must belong to the target Legacy;
- the cited excerpt must occur verbatim;
- assistant messages are rejected;
- complete conversations are not copied into provenance.

The CRUD layer repeats same-Legacy database reference checks inside the
candidate transaction.

## Relationships and contradictions

`memory_links` stores a durable, reviewable `possible_enrichment` relationship.
It never changes either Memory. Its unique constraint prevents duplicate
association rows.

For contradictions, the pipeline locks the related memories, reuses their
single existing group when present, or creates a new group. Both old and new
claims keep their provenance and candidate/review status. If related memories
belong to different Legacies or already span incompatible groups, persistence
is refused for that candidate.

## Ownership and errors

The pipeline enforces:

- authenticated ownership of the Legacy;
- Story Session membership in that Legacy;
- Story Message membership in that Story Session;
- conversation ownership by the authenticated user;
- non-null conversation association with the same Legacy;
- Message membership in that conversation;
- same-Legacy related memories and contradiction groups.

Application exceptions live in
`backend/app/services/memory/storage_exceptions.py`. They provide safe boundary
categories for ownership, source, extraction, validation, provenance,
persistence, and cross-Legacy failures. Provider and database exception
payloads are never returned.

## Logging

One structured completion event records identifiers, counts, status totals,
duration, and error count. It does not log full conversations, memory text,
provider payloads, prompts, tokens, or API keys. No monitoring dependency was
added.

## Tests and execution status

`backend/tests/test_memory_storage_pipeline.py` uses a fake extraction service
and an in-memory database. It makes no provider or network calls. It covers
accepted candidates, atomic provenance, duplicates and reprocessing,
invalid/insufficient results, contradictions and group reuse, enrichments,
ownership and cross-Legacy boundaries, assistant evidence, candidate rollback,
multiple/zero candidates, unassociated conversations, distinct confidence
fields, and the no-auto-approval invariant.

The repository requires Python 3.10 or newer
(`BACKEND_GUIDE.md`). On this workstation the Python launcher reports that no
Python installation is available, so the tests could not be executed here.
There is no Docker/Compose or CI configuration that provides an alternative
runtime.

From `WaffleBerry_backend/backend`, a developer can run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py"
```

## Remaining work for Phase 6.5.6

- authenticated review/list endpoints and frontend review workflow;
- explicit approve/reject/edit actions;
- revision creation for user edits;
- reviewer-visible enrichment and contradiction relationships;
- careful migration/backfill policy for fingerprints on existing memories.

Automatic background extraction, queues, memory retrieval, embeddings,
semantic search, media processing, merging, supersession, and approval remain
out of scope.
