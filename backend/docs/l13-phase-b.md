# L13 Phase B: isolated derived personality

No response prompts, routes, frontend behavior, auth, voice, or canonical memory
extraction have been changed. This subsystem has no public API. It is not an
authoritative personal memory store. No new model/provider configuration is used.

## Activation and worker

Apply migration `0014_legacy_personality` before running this application revision.
Do not start this revision against an unmigrated database: canonical ORM writes
require the new table to enforce atomic invalidation. Phase B does not apply the
migration to a development or production database automatically.

From `backend/`, using the existing environment and database configuration:

```powershell
python -m app.services.personality_worker --enqueue-existing --once
python -m app.services.personality_worker
```

Backfill queues at most 100 previously unprojected Legacies per invocation. Run
additional bounded invocations as needed. Continuous mode processes durable
pending/failed jobs. No web process starts the worker, no visitor request queues a
job, and no provider call exists in the builder. Deployment/service wiring is a
later phase and is not performed here.

## Transactions, concurrency, and failure

SQLAlchemy ORM flush hooks inspect actual canonical/provenance/entity changes.
The generation upsert uses the SAME connection and transaction as those changes.
Embedding-only writes and no-op text assignments do not invalidate. Several
effective flushes in one transaction may advance the generation more than once;
the number is a monotonic freshness token, not a memory/event counter. Rollback
also rolls back generation changes. Deleted/superseded sources are not loaded.

Invalidation clears cached JSON and marks the row pending immediately. A future
consumer must use `current_profile`, never raw `profile_json`. The helper performs
a fresh SELECT, validates schema/policy versions and matching generations, and
does not queue work. No Phase B chat path calls this helper.

A compare-and-swap lease serializes valid builds. The worker closes its read
transaction before derivation and publishes only with the same source generation,
unexpired lease, and token. Mutations retain a live lease but invalidate the build;
stale completion releases it without publishing. A crash recovers after lease
expiry. A very slow expired worker may still compute, but can never publish over
a replacement worker. Retries back off up to 256 seconds. Only the fixed error
code `build_failed` is retained; source text and exception payloads are not logged.
Database/worker failure is separate from chat; derivation failure cannot break
L12 responses. Database failure during atomic invalidation aborts the memory
transaction, rather than committing facts with a falsely fresh projection.

ORM hooks cover the current application mutation mechanism. Raw/bulk SQL memory
maintenance must call `invalidate_in_transaction` itself in the same transaction.
Do not use raw SQL to change canonical memories without invalidation. No generic
database trigger or auth/visitor changes are introduced.

## Deliberately conservative derivation

The rule-based engine recognizes explicit English canonical descriptions in
allowlisted dimensions, direct value statements, contextual teasing accounts,
and a narrow education-related behavior. It does not infer traits from ethnicity,
religion, age, occupation, gender, or relationship labels. Unrecognized/ambiguous
material produces no observation. This is intentionally not comprehensive
natural-language understanding; a later evidence-validated extractor can expand
coverage without making missing dimensions default traits.

Explicit evidence is supported, not numerically scored. Corroborated requires
independent known stories/conversations; unknown provenance is not independent.
Behavioral evidence cannot become a universal trait. Opposing explicit traits in
the same context are retained and made ineligible; different contexts coexist.
Allowlisted values are not invented beliefs or instructions to a response model.

Original quotations must occur verbatim inside quotation marks in BOTH canonical
memory and original source excerpt, with canonical subject attribution. No
back-translation is performed. Edited/enriched rows are excluded from signatures
because L12 can retain stale original excerpts. This intentionally omits many
multilingual sayings when canonical English did not retain their original form.
Quotation provenance is reported evidence, not forensic speaker authentication.
Known instruction-like quotes are retained only as explicitly ineligible data.
All future consumers must treat ALL descriptions/quotations as untrusted data,
not rely on the instruction-pattern filter as a security boundary.

Profiles contain at most 48 observations, 16 expressions, eight evidence references
per observation, and a complete active source fingerprint manifest (up to 2048
memories). Output over 128 KiB fails closed for this cache only. Content hashes
include context/provenance, and source spans are validated against original input.
Nothing writes new factual memories. JSON contains private preserved information
and must inherit Legacy-scoped access controls in any later API.

## Tests and migration boundaries

Tests use disposable SQLite databases and existing fake-provider API fixtures.
Migration tests exercise fresh head and 0013-to-0014 with Alembic current/heads;
the new migration also compiles PostgreSQL offline DDL. Offline DDL is not a live
PostgreSQL migration/concurrency acceptance test. No production migration,
backfill, real-provider call, browser/device acceptance, commit, or deployment is
part of Phase B.
