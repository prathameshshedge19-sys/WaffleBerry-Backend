# L14 Phase C: durable turn lifecycle

Local, uncommitted Phase B+C work. No provider, frontend, deployment, production
database, or L14 tag changes. Live PostgreSQL concurrency acceptance is complete;
see [the PostgreSQL acceptance report](L14_POSTGRESQL_ACCEPTANCE.md) for all race
counts, production-isolation evidence and verified temporary-environment cleanup.

## Admission, scope and requests

`MessageCreate.client_turn_id` is optional, 1–128 ASCII letters/digits or
`._:-`. No key preserves the current one-request/one-generation behavior. Future
clients must generate and retain a key to obtain retry protection. Neither
`input_mode` nor the key grants a capability. Actor, conversation, mode, Legacy
access and builder/persona role still come from the existing server authorization.

The unique key is `(actor_user_id, conversation_id, client_turn_id)`. The SHA-256
digest uses deterministic UTF-8 JSON containing conversation ID, actor ID, mode,
trimmed content, input mode and timezone. Internal whitespace is not rewritten.
Legacy isolation is enforced in the authorized lookup, including Legacy ID and
mode predicates, rather than hashing the mutable historical Legacy association.
This permits a pre-L2 conversation with a NULL Legacy link to initialize once
without invalidating its original digest. Conversation IDs are globally unique.
No timestamps or raw provider errors enter the digest or error metadata.

The `conversation_turns` row contains ID, conversation/Legacy/actor, mode, optional
client key, digest, input mode, state, random server claim token, optional user and
assistant message links, a safe error code, and accepted/started/finished/created/
updated timestamps. It does not duplicate message content. Legacy may be NULL
only while an old unlinked conversation has not durably initialized its Legacy.
Historical messages have no inferred turn rows. Message links are unique FKs;
deleting the linked conversation/messages removes associated lifecycle records.

The database permits `text`, `voice`, and future `realtime_voice`. The public
request schema still accepts only `text` and `voice`.

## State and transaction boundaries

1. After authorization, insert and commit `pending`, without staging a message.
2. Compare-and-set `pending` with no claim to `streaming`, with a random server
   token and start time; commit before any analysis, embedding, web or generation.
3. Link the user message in the existing route transaction. SSE commits that
   message before preparation. JSON keeps it uncommitted until generation succeeds.
4. Persist assistant, source rows and the token-checked transition from
   `streaming` to `completed` together. The assistant link and finish time become
   durable in the same transaction as the assistant content.
5. Builder effects run synchronously afterward in the transactions described below.

Generation/preparation/persistence failures roll back the route transaction and
record `failed` with a fixed category. JSON retains no failed user message; SSE
retains its already committed user message. Terminal states cannot transition
again through lifecycle helpers. A partial stream closed/cancelled while its
iterator is running records `interrupted`, with no assistant placeholder or tail.
The internal terminal helper supports a validated conservative prefix in future;
no public endpoint accepts a prefix, terminal state or claim token.

For old unlinked conversations, admission precedes Legacy bootstrap. The Legacy
and message association commit at the old route boundary, so failed JSON also
rolls back the bootstrap. No guessed historical turns are backfilled.

This is a one-shot claim, with no expiry or automatic stealing. An actual process
death can leave `pending` or `streaming`; a database outage can prevent recording
the safe failure code. Such rows require operator reconciliation. Closing a
stream before its iterator ever starts can likewise leave the active claim.
There is no recovery daemon or public retry-state mutation endpoint.

## Duplicate/reconnect policy

| Existing key | Result |
| --- | --- |
| Different immutable digest | HTTP 409 `turn_key_conflict` |
| Pending or streaming | HTTP 409 `turn_in_progress`; no provider calls |
| Completed, JSON request | Original persisted message pair, same keys and 201 |
| Completed, SSE request | HTTP 409 `turn_already_completed`; no delta replay |
| Failed or interrupted | HTTP 409 `turn_terminal`; no automatic new attempt |

A deliberate new attempt requires a new key. Newer clients may retrieve a
completed turn with the same JSON submission or use existing message history.
Historical SSE delta replay and resumable streaming are deferred. No new fields
were added to existing JSON success responses or SSE payloads.

Conversation-creation idempotency is deferred. Ordinary creation can resolve an
active/pending Legacy or bootstrap one and has no stable client creation identity.
Adding a separate receipt/creation key needs a narrow creation/bootstrap contract
review and later client wiring. The existing daily-question creation uniqueness
is preserved. `chat-session.js`, pendingConversation and first-send UI logic are
untouched.

## Post-success receipts and exact atomicity limits

`turn_effects` has primary key `(turn_id, kind)`, a result summary, and completion
time. Kinds are `memory` and `activity`. A receipt's presence means that effect
transaction finished, including legitimate no-op/skipped memory outcomes.

The finalizer validates the completed builder scope and obtains a database write
lock on that turn before checking receipts. Canonical memory/revisions/personality
invalidation and the memory receipt commit together. Activity contribution count,
daily-question answered state and the activity receipt commit together. Activity
also locks the Legacy on PostgreSQL to serialize different turn contributions to
the same day. Streak and progression calculations remain read-only.

`LivingMemoryService.store(commit=False)` and
`record_builder_activity(commit=False)` are narrow opt-ins allowing the finalizer
to own those two commits. Defaults preserve all other callers. Memory provider
failure/IntegrityError preserves the existing skip behavior and commits a no-op
receipt. Receipt summaries contain counts, IDs, operation category, local date
and first-contribution flag; they contain no private message text.

Assistant completion, memory application and activity remain **three separate
transactions**, not an end-to-end exactly-once transaction. A completed assistant
can have missing receipts after a crash. HTTP replay never reruns effects. An
internal caller with a validated preparation can repeat finalization safely;
the tests exercise retained preparation across logical crash boundaries. There
is no durable copy of memory analysis and no automatic reconstruction after a
real process crash. Missing receipts must be reviewed before controlled recovery.

Persona routes never enter this effect finalizer and create no effect receipts.
Collaborator provenance and behavior are preserved. The known discrepancy remains:
analyzed collaborator DELETE can mutate memory while dashboard DELETE is owner-only.
Resolve that policy separately before L15 write-tool exposure; Phase C does not
widen or fix it.

## Migration and validation

`0015_conversation_turns` follows `0014_legacy_personality`; only the two new
tables and their constraints/indexes are added. Old migrations are unchanged.
Fresh SQLite, populated 0014 upgrade, downgrade of the new tables, historical
data preservation and PostgreSQL DDL compilation are covered by migration tests.

The configured local `backend/waffle_berry.db` was backed up to:

`C:\Users\Saee\Desktop\Waffleberry-new\backups\legarya-local-before-0015-20260906T092043757354Z.db`

It is now at 0015. All 18 existing table definitions and all four existing data
rows match that 0014 backup. New tables are empty; integrity and FK checks pass.
The unpublished migration was reapplied while its new tables were verified empty
to incorporate NULL-Legacy historical compatibility. No existing table was dropped.

New tests cover key/digest conflicts, actor/Legacy/conversation isolation, public
realtime input rejection, no-key voice sends, provider counts, memory revision and
activity counts, personality invalidation, cancellation, immutable terminals,
historical bootstrap rollback and crash boundaries before generation, before
assistant persistence, before effects, during memory commit and during activity
commit. The concurrency test forces two independent ASGI workers past the absent
key lookup before either insert, using separate database sessions and file-backed
SQLite. Exactly one provider generation and effect application are required.

For the PostgreSQL variant, set `L14_TEST_POSTGRES_URL` to a disposable localhost
database whose name starts with `l14_test`, then run:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -o addopts= -p no:cacheprovider tests/test_turn_lifecycle_l14.py -k simultaneous
```

The test uses a random schema and removes only that schema. No production URL is
accepted. SQLite results and PostgreSQL DDL compilation do **not** establish live
PostgreSQL locking acceptance. The subsequent acceptance run used an isolated
temporary Hetzner database through a localhost SSH tunnel and passed all 25
stress iterations. That environment was removed afterward; see the acceptance
report for the verified live results.

## Files in the local Phase B+C diff

All paths are relative to `backend/`:

- `app/api/routes/conversations.py`
- `app/api/routes/legacy_conversations.py`
- `app/models/__init__.py`
- `app/models/turn.py`
- `app/schemas/chat.py`
- `app/services/builder_turns.py`
- `app/services/conversation_turns.py`
- `app/services/persona_turns.py`
- `app/services/turn_lifecycle.py`
- `app/services/turn_effects.py`
- `app/services/memory.py`
- `app/services/progression.py`
- `alembic/versions/0015_conversation_turns.py`
- `tests/test_conversation_turns_l14.py`
- `tests/test_turn_lifecycle_l14.py`
- `tests/test_turn_migration_l14.py`
- `tests/test_personality_cadence_l13.py` (Phase B spy relocation only)
- `tests/test_personality_migration_l13.py` (expected current head updated)
- `L14_PHASE_C.md`
- `L14_POSTGRESQL_ACCEPTANCE.md`
- `tests/test_turn_postgresql_acceptance_l14.py`

Phase B transaction-order tests still assert the original provider/message/effect
order, excluding the two explicitly marked admission/claim commits. No old
behavior assertion was removed. The L13 migration test now expects source head
0015 while retaining its original projection and preservation assertions.

## Final validation (2026-09-06)

- Backend: 443 passed, zero skipped across the 442-test preexisting suite and
  the new 25-iteration live PostgreSQL acceptance test. Two existing dependency
  deprecation warnings remain. All 406 pre-Phase-C tests remain covered.
- Full frontend: 80 passed. New Chat, first-send ownership, pending bootstrap,
  daily-question, voice, role and personality tests pass. These are automated
  gates; no manual browser session was run.
- Python in-memory compilation: 136 files. JavaScript syntax: 48 files.
- Frozen route AST checks: 11 builder and 13 persona helpers/CRUD functions
  match the L13 commit. Frontend working tree is clean; all provider, auth,
  personality, New Chat and onboarding implementation files remain unchanged.
- Source and configured local Alembic head: 0015_conversation_turns. Local SQLite
  backup, schema/data preservation, integrity and foreign-key checks pass.
- Backend HEAD/main/origin/main/L13 checkpoint remain
  dd246d658d308e5c558ff320d74e0056727048b5. Frontend references remain
  f075f6a569db20bbfe5173d175fe0fa6f149029b.
- Backend has the intentional local Phase B+C diff; frontend is clean. No commit,
  push, deployment, production migration or L14 tag was created.
- Static review: authorized scope precedes key lookup; privilege fields are never
  unpacked into capabilities; lifecycle errors are fixed categories; clients
  cannot choose claims or terminal states; completed visitor turns have zero
  builder receipts; historical bootstrap rollback is preserved.

Live PostgreSQL concurrency acceptance is complete, with no Phase C behavior
fix required. The temporary test environment was removed and production remained
unchanged. Real crash recovery and SSE delta replay remain explicitly deferred
infrastructure work, and the known collaborator DELETE policy finding remains a
pre-L15 tool-exposure issue.
