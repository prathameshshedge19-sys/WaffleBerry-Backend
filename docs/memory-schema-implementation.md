# Memory Schema Implementation

**Milestone:** Phase 6.5.2 — Memory Database Schema  
**Architecture source:** `docs/memory-engine-design.md`  
**Scope:** Persistence foundation only; no extraction, API, UI, or retrieval
integration

## Summary

Phase 6.5.2 introduces a versioned SQLAlchemy persistence foundation for
legacy-isolated Guided Stories and reviewable memories. It adds no public
routes and does not connect memories to Story Guide or Companion Chat.

The implementation follows existing conventions:

- SQLAlchemy ORM and integer entity IDs;
- snake_case tables, columns, schemas, and repository methods;
- timezone-aware creation/update timestamps;
- constrained non-native string enums;
- FastAPI/Pydantic 2 validation contracts;
- transaction-owning CRUD methods with rollback on failure;
- authenticated owner-scoped lookup patterns modeled after
  `ConversationCRUD.get_user_conversation()`.

## Entities created

### `Legacy`

Table: `legacies`

Represents one person whose contributed life information is being preserved.
A user can own multiple legacies.

Key fields:

- `legacy_id`
- `owner_user_id`
- `display_name`
- `relationship`
- `status`
- `created_at`
- `updated_at`

`owner_user_id` is required and cascades on user deletion. No `deleted_at`
column was added because soft deletion is not currently a backend convention.

### `StorySession`

Table: `story_sessions`

Represents one Guided Story conversation under exactly one legacy.

Key fields:

- `story_session_id`
- `legacy_id`
- `chapter_key`
- optional `title`
- `status`
- `created_by_user_id`
- `created_at`
- `updated_at`
- optional `completed_at`

The repository creates a session only after resolving the legacy through its
owner.

### `StoryMessage`

Table: `story_messages`

Stores application-visible messages in a Story Session.

Key fields:

- `story_message_id`
- `story_session_id`
- `role`
- `content`
- `sequence`
- `created_at`

`(story_session_id, sequence)` is unique, and `sequence` must be positive.
`StorySessionCRUD.append_story_message()` locks the session row, obtains the
next sequence, and commits one deterministic message. Hidden chain-of-thought,
internal reasoning, and provider SDK metadata are not fields.

### `Memory`

Table: `memories`

Uses the single-table design for atomic and narrative memories.

Key fields:

- `memory_id`
- required `legacy_id`
- `memory_type`
- controlled `category`
- `title`
- `summary`
- optional JSON `details`
- optional `emotional_significance`
- optional `importance`
- optional `extraction_confidence`
- `review_status`
- optional `uncertainty_note`
- optional `contradiction_group_id`
- optional `superseded_by_memory_id`
- review actor/time fields
- creation/update timestamps

Database checks enforce nonblank title/summary, importance from 1–5,
confidence from 0–1, and no self-supersession.

`details` contains Pydantic-validated `temporal_references` and `places`, while
allowing future category-specific keys. Important ownership, lifecycle,
category, type, and conflict fields remain first-class columns.

### `MemoryProvenance`

Table: `memory_provenance`

Provides first-class, many-source traceability.

Key fields:

- `provenance_id`
- `memory_id`
- `source_type`
- nullable `conversation_id` and `message_id`
- nullable `story_session_id` and `story_message_id`
- optional JSON `source_locator`
- minimal optional `excerpt`
- optional `speaker`
- optional `chapter`
- `extracted_at`
- optional `extractor_version`

Existing source entities use foreign keys. Future voice, photo, video, and
document sources use a validated source locator until their source tables
exist. Entire conversations are not copied.

Application validation requires:

- both conversation and message IDs for conversation provenance;
- both Story Session and Story Message IDs for story provenance;
- no message/session IDs for manual provenance;
- a locator for future media/document source types;
- referenced messages to belong to their supplied container;
- referenced containers to belong to the target legacy.

### `MemoryRevision`

Table: `memory_revisions`

Stores immutable pre-edit content snapshots so user edits do not erase the
original extracted form.

Key fields:

- `memory_revision_id`
- `memory_id`
- positive, per-memory `revision_number`
- optional `edited_by_user_id`
- JSON `previous_content`
- optional `edit_reason`
- `created_at`

`edited` is therefore not a review status.

### `MemoryContradictionGroup`

Table: `memory_contradiction_groups`

Groups conflicting accounts without merging or overwriting them.

Key fields:

- `contradiction_group_id`
- `legacy_id`
- `topic`
- `resolution_status`
- optional resolution actor, note, and timestamp
- `created_at`

Each account remains an independent `Memory` with its own provenance.

### `MemoryParticipant`

Table: `memory_participants`

Stores source-grounded people and relationship labels relationally for future
relationship-aware retrieval.

### `Tag` and `MemoryTag`

Tables: `tags`, `memory_tags`

Tags are normalized and unique within a legacy. The association table provides
the many-to-many memory/tag relationship without storing query-critical tags
as opaque JSON.

## Existing entity change

`Conversation` gains nullable `legacy_id`.

This is intentionally nullable:

- existing rows remain valid;
- no legacy is fabricated or inferred;
- existing conversations continue to belong to their current user;
- only explicitly assigned future conversations can be used as
  legacy-grounded memory provenance.

The foreign key uses `ON DELETE SET NULL`, preserving a conversation when its
legacy association is removed. The later privacy workflow may explicitly
delete legacy conversations when the product requests full legacy deletion.

No message columns or existing chat persistence methods were changed.

## Enums and controlled values

Database-backed non-native enums:

| Enum | Values |
|---|---|
| `LegacyStatus` | `active`, `archived` |
| `StorySessionStatus` | `in_progress`, `paused`, `completed` |
| `StoryMessageRole` | `user`, `assistant` |
| `MemoryType` | `atomic`, `narrative` |
| `MemoryReviewStatus` | `candidate`, `approved`, `rejected`, `superseded` |

Non-native enums match the existing `MessageRole` approach and remain portable
between SQLite tests and PostgreSQL deployments.

Source types and categories are bounded strings validated by the application.
This preserves the controlled taxonomy while allowing later additions without
database enum rewrites.

Initial categories:

- `personal_detail`
- `relationship`
- `place`
- `life_event`
- `preference`
- `tradition`
- `habit`
- `value`
- `achievement`
- `challenge`
- `lesson`
- `expression`
- `story`

Supported source types:

- `conversation`
- `story_session`
- `voice`
- `photo`
- `video`
- `document`
- `manual`

## Relations

```text
User
 └── Legacy
      ├── StorySession
      │    └── StoryMessage
      ├── Memory
      │    ├── MemoryProvenance
      │    ├── MemoryRevision
      │    ├── MemoryParticipant
      │    └── MemoryTag ── Tag
      ├── MemoryContradictionGroup
      │    └── Memory
      └── Conversation (optional association)
           └── Message
```

`Memory.superseded_by_memory_id` is a nullable self-reference. The repository
requires both memories to belong to the same legacy and rejects
self-supersession.

## Indexes

Indexes target ownership and expected retrieval paths:

- `legacies(owner_user_id)`
- `legacies(owner_user_id, status)`
- `conversations(legacy_id)`
- `story_sessions(legacy_id)`
- `story_sessions(legacy_id, chapter_key, status)`
- `story_messages(story_session_id)`
- `story_messages(story_session_id, sequence)`
- `memories(legacy_id)`
- `memories(review_status)`
- `memories(category)`
- `memories(legacy_id, review_status)`
- `memories(legacy_id, category, memory_type)`
- contradiction and supersession references
- provenance memory and source references
- revision and participant memory references
- legacy-scoped tags

No indexes or fields for embeddings, similarity, or semantic deduplication were
added.

## Migration behavior

The repository previously had no migration tooling. Alembic was introduced as
the SQLAlchemy-compatible migration authority.

### Revisions

1. `0001_existing_schema_baseline`
   - represents the schema that predates Phase 6.5.2;
   - creates that schema only for fresh databases.
2. `0002_memory_engine`
   - creates all Phase 6.5.2 entities;
   - adds nullable `Conversation.legacy_id`;
   - is reversible and contains no seed data.

### Fresh database

From `WaffleBerry_backend/backend`:

```text
alembic upgrade head
```

This executes both revisions.

### Existing pre-Alembic database

Back up the database, then from `WaffleBerry_backend/backend`:

```text
alembic stamp 0001_existing_schema
alembic upgrade head
```

The stamp records the already-existing baseline without trying to recreate its
tables. The second command applies only the Memory Engine schema.

Do not run the baseline `upgrade` against a populated pre-Alembic database.

Startup-time `Base.metadata.create_all()` was removed from `app/main.py`.
Deployments must apply migrations before starting the updated application.
This prevents model imports from silently creating an unversioned partial
schema.

### Existing data

- no users are changed;
- no legacy rows are generated;
- no conversation is assigned to a legacy;
- all existing `Conversation.legacy_id` values begin as `NULL`;
- existing messages and chat titles are unchanged;
- the migration performs no destructive data rewrite.

## Delete behavior

Intentional foreign-key actions:

- deleting a user cascades owned legacies;
- deleting a legacy cascades Story Sessions, Story Messages, memories,
  revisions, participants, tags, contradiction groups, and their provenance;
- deleting a Story Session cascades its Story Messages;
- deleting a memory cascades its provenance, revisions, participants, and tag
  links;
- deleting an individual conversation, message, Story Session, or Story
  Message sets the corresponding nullable provenance reference to `NULL`;
- deleting a contradiction group sets memory group references to `NULL`;
- deleting a replacement memory sets `superseded_by_memory_id` to `NULL`;
- deleting a legacy sets associated `Conversation.legacy_id` to `NULL`.

The `SET NULL` provenance behavior keeps a minimal review excerpt available
when a source is independently removed. Phase 6.5.3 must implement the
application-level privacy workflow that deletes or reclassifies derived
memories and excerpts when full source erasure is requested.

## Repository and validation foundation

New module: `app/crud/memory.py`

### `LegacyCRUD`

- `create_legacy()`
- `get_user_legacy()`
- `get_user_legacies()`

### `StorySessionCRUD`

- `create_story_session()`
- `get_legacy_story_session()`
- `append_story_message()`
- `get_story_messages()`

### `MemoryCRUD`

- `get_legacy_memory()`
- `create_contradiction_group()`
- `create_memory_candidate()`
- `list_legacy_memories()`
- `update_review_status()`
- `supersede_memory()`
- `add_revision()`
- `attach_provenance()`

Creation, listing, review, contradiction, revision, supersession, and
provenance entry points require either an owner-scoped legacy resolution or a
previously verified legacy boundary. `MemoryPersistenceError` represents
ownership/provenance invariant failures without exposing another legacy's
resources.

New module: `app/schemas/memory.py`

It provides strict persistence contracts for:

- legacy and Story Session creation;
- visible Story Messages;
- temporal and place references;
- candidate memories;
- participants and tags;
- provenance source shapes;
- review status.

Confidence and importance ranges are enforced by both Pydantic and database
constraints.

## Ownership boundaries

The persistence layer enforces:

1. a legacy has exactly one owner;
2. owner-scoped legacy lookup uses both `legacy_id` and `owner_user_id`;
3. Story Session creation requires an owned legacy;
4. Story Session lookup and message append require both session and legacy IDs;
5. memory creation/listing/review requires an owned legacy;
6. conversation provenance requires a conversation assigned to that legacy and
   a message belonging to that conversation;
7. story provenance requires a Story Session assigned to that legacy and a
   Story Message belonging to that session;
8. contradiction and supersession links cannot cross legacy boundaries through
   repository operations.

No public APIs were added, so there is no new externally reachable
authorization surface in this milestone.

## Backward compatibility

- Existing route URLs and schemas are unchanged.
- Existing authentication is unchanged.
- Existing conversation and message CRUD behavior is unchanged.
- Existing chat and Story Guide streaming code is unchanged.
- Guided Stories remain stateless at runtime until Phase 6.5.3 wires the new
  persistence layer.
- The frontend remains unchanged and can continue using temporary legacy
  objects.
- Existing conversations remain valid with `legacy_id = NULL`.

Future frontend integration must replace temporary legacy identifiers with
server-issued `legacy_id` values before persisted Story Sessions or Memory
Review can be exposed.

## Tests

`backend/tests/test_memory_persistence.py` covers:

1. multiple legacies owned by one user and owner-scoped lookup;
2. Story Session ownership and deterministic message sequence;
3. atomic and narrative memory persistence;
4. multiple provenance sources;
5. approximate and uncertain temporal details;
6. candidate, approved, rejected, and superseded states;
7. immutable revision snapshots;
8. contradictory memories coexisting;
9. explicit same-legacy supersession;
10. cross-legacy Story provenance rejection;
11. existing conversations without a legacy association.

Tests use in-memory SQLite with foreign-key enforcement and make no external AI
calls.

## Deferred work for Phase 6.5.3

- public owner-authenticated Legacy and Story Session APIs;
- wiring Guided Stories to persisted sessions/messages;
- deciding how frontend temporary legacies are imported or replaced;
- explicit assignment of future normal conversations to a legacy;
- transactional memory editing around `MemoryRevision`;
- full review and source-inspection service behavior;
- privacy deletion orchestration;
- extraction-run/outbox entities when extraction is introduced;
- extraction prompt and provider-neutral structured extraction;
- retry/idempotency behavior for extraction;
- any UI integration.

The following remain intentionally absent:

- OpenAI extraction calls;
- extraction prompts;
- background jobs;
- semantic deduplication;
- embeddings and vector storage;
- automatic merges or contradiction detection;
- Companion Chat memory retrieval;
- frontend changes.

## Deviations and clarifications

- Alembic was added because the repository had no migration framework. This is
  a prerequisite for safe, reversible production schema changes.
- `MemoryExtractionRun` was deferred because no extraction executes in this
  milestone. It should be added with the extraction/outbox boundary rather than
  as an unused table.
- `MemoryLink` was deferred because narrative-to-atomic and enrichment linking
  are not yet implemented.
- Media-specific source foreign keys were deferred because those source
  entities do not exist. Their future IDs can be represented in validated
  `source_locator` JSON until dedicated tables are introduced.
- No public APIs were added because the milestone requests persistence
  foundations and explicitly excludes unnecessary controllers.
