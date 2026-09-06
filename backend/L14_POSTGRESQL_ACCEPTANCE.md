# L14 Phase C PostgreSQL acceptance

Status: ACCEPTED. All 25 live PostgreSQL stress iterations passed and the
temporary database, role, tunnel and remote manifest have been removed.

## Environment and isolation

- PostgreSQL 18.6 on the existing Hetzner host `89.167.14.211`.
- Dedicated temporary database: `l14_test_phase_c_20260906_093106`.
- Dedicated temporary login role: `l14_test_role_20260906_093106`, without
  superuser, database creation, role creation, replication or RLS-bypass powers.
- The local uncommitted code executes on Windows through a localhost-only SSH
  tunnel. No application code was copied to the server or production directory.
- All test connections verify the database name. The test URLs accept only
  localhost and explicitly named `l14_test*` databases. Application provider
  dependencies use the existing test fakes; no actual AI generation is invoked.
- The local virtual environment was missing the already-declared
  `psycopg2-binary` dependency. Version 2.9.12 was installed locally; requirements
  and production environments were not changed.

Production baseline captured before database creation:

| Property | Value |
| --- | --- |
| `legarya` schema SHA-256, normalized schema-only dump | `97810db1b1b4e38d10135b0f0541885ff89048a750d5492d970efb024ad1489d` |
| `legarya` Alembic revision | `0014_legacy_personality` |
| Deployed backend HEAD | `dd246d658d308e5c558ff320d74e0056727048b5` |
| Production service PID | `705190` |
| Production service active since | `2026-09-06 00:42:28 UTC` |

The only production-database operations are read-only schema/revision checks.
All writes target the temporary database or its disposable role. No production
configuration, service restart, migration, deployment, commit or push is involved.

## Migration and exercised code

`alembic upgrade head` ran from an empty temporary database through the complete
L1–L14 chain. `current` and the single source `head` resolve to
`0015_conversation_turns`. Both new tables exist.

The acceptance tests use a random disposable schema with the current ORM model.
Its `conversation_turns` and `turn_effects` definitions were compared against
the live Alembic-created public tables: columns/types/nullability, primary keys,
checks, unique constraints, foreign keys and indexes match.

The previously skipped PostgreSQL test in `test_turn_lifecycle_l14.py` passed
against this instance. The new `test_turn_postgresql_acceptance_l14.py` forces
two separate ASGI workers past the absent-key lookup before either inserts. Each
worker has its own SQLAlchemy session and PostgreSQL connection.

Each stress iteration resets only the disposable schema's test rows/identities
and exercises:

1. Same actor/conversation/key/digest: one successful claim, one provider
   generation, one new assistant, one completed turn, one actual memory revision,
   one memory receipt, and one activity receipt/contribution.
2. Same key with a different digest: 409 without extra generation or overwrite.
3. Terminal transition races rotating completed/completed, completed/failed and
   completed/interrupted: one winner, with losing assistant inserts rolled back.
4. Two concurrent finalizers for a completed turn with no effects yet: one new
   memory revision and activity contribution. Both workers deliberately retain
   previously loaded ORM objects to exercise stale-state handling.
5. Independent conversations, actors and Legacies reuse the same key safely.
   Forged Legacy/actor combinations fail authorization/scope checks. Collaborator
   revision provenance is checked, and persona turns have no builder receipts.

This validates database effects, not just fake-provider counters. The seeded
assistant is excluded from the new-assistant count.

## Regression results

- All 442 preexisting backend tests: passed, zero skipped, 79.45 seconds.
  This includes the formerly skipped live PostgreSQL test.
- Full frontend: 80 passed; frontend remains unchanged.
- Python compilation: 136 files. JavaScript syntax: 48 files.
- Migration tests, New Chat, first/second send, persisted history, onboarding,
  daily question, visitor safety, collaborator provenance, L12 voice and L13
  personality automated regressions pass. No manual browser session was run.
- Two existing dependency deprecation warnings remain.

The full preexisting suite and new stress acceptance run are separate invocations
so the long stress loop is not needlessly repeated. Deferred crash recovery, SSE
replay, creation-key UI, realtime work and the collaborator DELETE policy finding
remain unchanged.

## Final stress and cleanup result

The new stress test passed in 739.51 seconds, with 25 iterations and two distinct
PostgreSQL backend connections. Each iteration exercised three concurrent races
(same-key admission, terminal transition, effect finalization): 75 stress races.

| Same-key race measurement | Per iteration | Total |
| --- | ---: | ---: |
| Successful processing claims | 1 | 25 |
| Provider generations | 1 | 25 |
| New persisted assistant results | 1 | 25 |
| Completed turns | 1 | 25 |
| Memory receipts / actual revisions | 1 | 25 |
| Activity receipts / contributions | 1 | 25 |

These totals describe the same-key race, not the additional independently
accepted scope-validation turns. The 25 terminal races each had one winner. The
25 separate concurrent effect-finalizer races each produced one additional
revision and one contribution, with one receipt of each kind. Different-digest
conflicts and cross-scope checks passed in every iteration.

Final test-database counters: zero deadlocks, zero conflicts, no remaining
sessions or blocked transactions. The test role had write privileges on zero
production tables. Unique-constraint losers, claim ownership, terminal CAS and
stale ORM state behaved correctly. No application bug or Phase C behavior fix
was required.

The backend result is **443 passed, zero skipped across the two complementary
invocations**: 442 preexisting tests plus this one stress acceptance test. The
formerly skipped PostgreSQL test passed both in the initial targeted run and in
the preexisting-suite regression run.

Cleanup verified:

- Test schema removed by the test's `finally` cleanup.
- Dedicated SSH tunnel PID 22696 stopped after verifying its command line.
- Temporary database and role dropped; absence verified in PostgreSQL catalogs.
- Remote temporary manifest removed; no application code was copied remotely.
- Production schema SHA-256, Alembic revision, deployed commit, service PID and
  service start timestamp exactly match the baseline table above.
- Local temporary control scripts, credential files and test pointer removed.

Repository changes for this acceptance run are the new acceptance test, this
report, and the updated Phase C report. All application implementation files and
frontend files are unchanged from the Phase C implementation reviewed before
this run. No commit, push, deployment, production migration, service restart or
L14 tag was performed. No acceptance blocker remains.

Automatic crash recovery, resumable SSE, creation-key frontend work, realtime
transport and collaborator DELETE policy changes remain deferred as instructed.
