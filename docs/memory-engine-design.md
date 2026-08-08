# WaffleBerry Memory Engine Design

**Milestone:** Phase 6.5.1 — Memory Architecture and Data Contract  
**Status:** Design only; no runtime implementation or database migration  
**Scope:** Structured, reviewable memories derived initially from text and later
from voice, photos, videos, documents, and manual entry

## 1. Current backend architecture findings

### 1.1 Application structure

The backend is a FastAPI application under `backend/app`:

- `main.py` creates the application, initializes SQLAlchemy metadata, and
  mounts the versioned API routers.
- `api/v1/user.py` owns authentication-adjacent, conversation, message, and
  temporary Story Guide endpoints.
- `schemas/user.py` contains Pydantic request and response contracts.
- `models/user.py` contains the user, conversation, message, voice, consent,
  and settings SQLAlchemy models.
- `crud/user.py` contains database operations and transaction boundaries.
- `services/chat_service.py` orchestrates context preparation and AI calls
  without persistence.
- `services/ai/` contains the provider-independent message contract, context
  builder, prompts, reliability behavior, and OpenAI adapter.
- `dependencies/auth.py` resolves the authenticated `User` from a Bearer token.
- `dependencies/ai.py` constructs and caches the configured `ChatService`.
- `db.py` provides the SQLAlchemy base, engine, session factory, and request
  session dependency.

The project uses SQLAlchemy 2.x-style dependencies with predominantly
declarative 1.x query syntax, Pydantic 2, FastAPI dependency injection, and
integer primary keys named for their entities (`user_id`, `conversation_id`,
and `message_id`). API contracts use snake_case.

### 1.2 Current persistence boundaries

`User` owns `Conversation` through `Conversation.user_id`. `Conversation` owns
`Message` through `Message.conversation_id`. Both relationships use database
cascades, and the ORM relationships use `delete-orphan`.

Normal chat behavior is persisted:

1. An authenticated user requests a conversation endpoint.
2. `ConversationCRUD.get_user_conversation()` scopes the lookup by both
   `conversation_id` and `current_user.user_id`.
3. `ChatService` loads recent messages in reverse chronological order, reverses
   them into chronological order, and builds provider-neutral `AIMessage`
   objects.
4. The existing AI service generates or streams the response.
5. `MessageCRUD` persists the resulting messages and updates the conversation.

The non-streaming path stores the user and assistant messages atomically in
`MessageCRUD.create_message_pair()`. The streaming path currently commits the
user message before streaming and stores the assistant message only after a
complete stream. This distinction matters when extraction is introduced:
memory extraction must run only from durable source messages and must not alter
these existing transaction semantics.

### 1.3 Current Guided Stories boundary

`POST /api/v1/stories/stream` is authenticated, but Guided Stories are currently
temporary and frontend-owned:

- `StoryGuideRequest` supplies `current_chapter`, `relationship`,
  `display_name`, and up to 50 history messages.
- The endpoint does not accept or resolve a legacy ID.
- There is no `Legacy`, `StorySession`, or `StoryMessage` database entity.
- Story history and AI output are not persisted.
- The endpoint discards `current_user` after authentication and cannot verify
  that the submitted relationship or display name belongs to that user.

Consequently, Phase 6.5 cannot create traceable Guided Story memories until a
server-owned legacy and story-session persistence boundary exists. Client-side
legacy IDs must not be treated as authoritative database identities.

### 1.4 Existing ownership strengths and gaps

The conversation/message endpoints demonstrate the correct ownership pattern:
authenticate with `get_current_user()`, then query the resource using both its
ID and the authenticated user's ID.

The current backend has no legacy ownership model. Some older voice-profile and
project endpoints also do not consistently use `get_current_user()`, so they
must not be used as authorization examples for the Memory Engine. Every memory
operation must use an authenticated, owner-scoped legacy lookup.

### 1.5 Architectural consequences

The Memory Engine should:

- add a service layer parallel to `ChatService`, not put extraction or merging
  logic in routes or CRUD;
- reuse the existing provider-independent AI infrastructure when extraction is
  implemented, with a dedicated extraction contract and prompt;
- keep extraction asynchronous relative to the source request where possible;
- persist only structured candidates after source content is durable;
- keep candidates separate from the provider response and SDK objects;
- make the legacy, not the user or conversation, the primary isolation key;
- model provenance as relational records rather than embedding entire
  conversations in memory JSON.

## 2. Proposed Memory Engine components

The following modules are recommended for later implementation. They are not
created in this milestone.

| Component | Proposed location | Responsibility |
|---|---|---|
| Memory API router | `backend/app/api/v1/memory.py` | Owner-scoped candidate review, listing, source inspection, and deletion endpoints |
| Memory schemas | `backend/app/schemas/memory.py` | Pydantic contracts for candidates, provenance, review actions, and responses |
| Memory models | `backend/app/models/memory.py` | Legacy, story-source prerequisites, memory, provenance, revision, participant, tag, and conflict entities |
| Memory repository/CRUD | `backend/app/crud/memory.py` | Persistence queries only; no extraction decisions |
| Memory service | `backend/app/services/memory_service.py` | Authorization-safe orchestration, lifecycle rules, deletion, deduplication decisions, and transactions |
| Extractor interface | `backend/app/services/memory/extractor.py` | Provider-neutral extraction contract returning validated candidates |
| AI extractor | `backend/app/services/memory/ai_extractor.py` | Uses the existing `AIService` with a dedicated extraction prompt and structured response validation |
| Candidate validator | `backend/app/services/memory/validator.py` | Validates categories, confidence ranges, provenance completeness, and legacy/source consistency |
| Reconciliation service | `backend/app/services/memory/reconciliation.py` | Future duplicate, enrichment, contradiction, and correction classification |
| Extraction worker | future worker module | Runs extraction after source persistence without delaying chat or story streaming |

The extractor must return application-owned Pydantic objects. OpenAI request or
response objects must remain behind the existing provider abstraction.

## 3. Memory data contract

### 3.1 Contract goals

A memory candidate is an interpretation of contributed source material. It is
not automatically treated as truth, and it is not a representation of
consciousness. Every candidate:

- belongs to exactly one legacy;
- has at least one provenance record;
- records whether it is atomic or narrative;
- has a controlled category;
- starts in human-reviewable state;
- preserves uncertainty and extraction confidence separately;
- can be revised without erasing prior human-visible content;
- can be grouped with contradictions without silently overwriting either claim.

### 3.2 `Memory` entity

Recommended table: `memories`

| Field | Storage | Required | Purpose |
|---|---|---:|---|
| `memory_id` | integer PK | yes | Repository-standard entity identifier |
| `legacy_id` | FK to `legacies.legacy_id`, indexed | yes | Hard isolation boundary |
| `memory_type` | non-native enum | yes | `atomic` or `narrative` |
| `category` | bounded string, indexed | yes | Controlled but extensible category slug |
| `title` | bounded string | yes | Human-scannable label; not the canonical claim |
| `summary` | text | yes | Concise fact or narrative summary |
| `details` | JSON | no | Type-specific structured attributes that do not merit universal columns |
| `emotional_significance` | text | no | Source-grounded meaning or emotional context |
| `importance` | small integer | no | Optional owner/editor priority, recommended range 1–5 |
| `extraction_confidence` | fixed decimal | no | Extractor confidence from 0.00–1.00; never factual certainty |
| `review_status` | non-native enum, indexed | yes | `candidate`, `approved`, `rejected`, or `superseded` |
| `uncertainty_note` | text | no | Human-readable qualification present in the source |
| `contradiction_group_id` | nullable FK, indexed | no | Groups mutually inconsistent accounts |
| `superseded_by_memory_id` | nullable self-FK | no | Explicit replacement after review, never automatic |
| `created_at` | timezone-aware datetime | yes | Candidate creation time |
| `updated_at` | timezone-aware datetime | yes | Current projection update time |
| `reviewed_at` | timezone-aware datetime | no | Most recent approve/reject/supersede action |
| `reviewed_by_user_id` | nullable FK to users | no | Human actor responsible for review |

Recommended constraints:

- `legacy_id` is immutable after creation.
- `summary` and `title` must not be blank.
- `importance`, when present, is between 1 and 5.
- `extraction_confidence`, when present, is between 0 and 1.
- `superseded_by_memory_id` cannot reference itself.
- a superseded memory must have `superseded_by_memory_id`.
- every memory must have one or more provenance records before its transaction
  commits.
- every linked provenance source must resolve to the same `legacy_id`.

`details` should contain only optional category-specific fields, for example a
favorite flower species or an achievement organization. Universal fields,
ownership, review state, provenance, people, and conflict links must not be
hidden inside it.

### 3.3 Relations

#### `MemoryParticipant`

Recommended table: `memory_participants`

- `memory_participant_id`
- `memory_id`
- `name`
- `relationship` (nullable normalized label such as `son` or `spouse`)
- `role` (nullable contextual role such as `subject`, `witness`, or
  `mentioned_person`)

People are relational rather than a JSON list because relationship-aware
retrieval is a core product behavior. A future canonical `Person` entity can be
introduced and linked without changing the memory contract; initially,
participant names remain source-grounded labels.

#### `MemoryTag` and `Tag`

Tags should use a many-to-many relation:

- `tags(tag_id, legacy_id, name, normalized_name)`
- `memory_tags(memory_id, tag_id)`

Tags are legacy-scoped so a user's organizational vocabulary does not leak
between legacies.

#### Temporal and place structures

Dates and places can begin as bounded JSON structures on `Memory.details`
because a memory may contain multiple fuzzy temporal or geographic references,
and canonical place resolution is not yet required.

Recommended temporal item:

```json
{
  "text": "around the summer of 1985",
  "start_date": "1985-06-01",
  "end_date": "1985-08-31",
  "precision": "season",
  "is_approximate": true,
  "certainty": "uncertain"
}
```

Recommended place item:

```json
{
  "name": "Jaipur",
  "region": "Rajasthan",
  "country": "India",
  "certainty": "possible"
}
```

These structures should be validated by Pydantic, not accepted as arbitrary
JSON. If temporal or place querying becomes central, they can later be promoted
to relational entities without changing `Memory` identity or provenance.

### 3.4 Computed values

The following should be computed rather than stored initially:

- `source_count` from provenance rows;
- `has_conflict` from `contradiction_group_id`;
- `is_reviewed` from `review_status`;
- a display date derived from the temporal structure;
- provenance availability from current source references;
- completion or chapter progress, which is not a memory property.

## 4. Proposed categories and enums

### 4.1 Memory type

Stable application and database enum:

- `atomic`
- `narrative`

Use SQLAlchemy `Enum(..., native_enum=False)` to match the existing
`MessageRole` convention while avoiding database-specific enum migrations.

### 4.2 Review status

Stable application and database enum:

- `candidate` — extracted or manually submitted and awaiting review
- `approved` — accepted by an authorized human
- `rejected` — retained for audit/provenance but excluded from companion use
- `superseded` — explicitly replaced by another reviewed memory

`edited` should not be a status. Editing is an action recorded in revision
history; the resulting current memory can still be a candidate or approved.

### 4.3 Source type

Controlled application values:

- `conversation`
- `story_session`
- `voice`
- `photo`
- `video`
- `document`
- `manual`

Store `source_type` as a bounded string validated by an application enum rather
than a database enum. This lets new source adapters be introduced without
rewriting existing rows or requiring a database enum migration.

### 4.4 Category system

Recommended initial categories:

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

This list deliberately consolidates overlapping suggestions:

- a person is represented through participants, not a memory category;
- beliefs initially fit under `value`;
- nicknames fit under `expression`;
- emotional memories use `narrative` plus `emotional_significance`;
- broad narrative content uses `story`.

Categories should be string slugs validated against a versioned application
registry. Unknown values from an extractor must be rejected or mapped to
`story`; they must not be inserted unchecked. Later categories can be appended
without changing stored values. Renaming a category requires an explicit alias
and migration policy so old API values remain readable.

### 4.5 Other controlled values

Recommended application enums:

- temporal precision: `day`, `month`, `season`, `year`, `decade`, `range`,
  `unknown`;
- certainty: `stated`, `approximate`, `uncertain`, `disputed`;
- participant role: `subject`, `witness`, `mentioned_person`;
- conflict resolution: `unresolved`, `one_account_preferred`,
  `both_accounts_retained`, `resolved_by_correction`.

## 5. Atomic versus narrative memory decision

Use one `memories` table with `memory_type`.

This is the simplest scalable choice because both forms share ownership,
review, provenance, category, uncertainty, lifecycle, tags, and deletion
behavior. Separate tables would duplicate these rules and complicate mixed
retrieval. A narrative can be longer and have emotional context, while an
atomic memory should express one independently reviewable claim.

Examples:

- Atomic: “Mom studied at Fergusson College.”
- Narrative: “Mom remembered her first day at college because her father
  travelled with her by bus and waited outside until class ended.”

A future `memory_links` table can connect related records without requiring a
parent-child hierarchy:

- `from_narrative` — an atomic fact was extracted from a narrative;
- `supports` — one memory adds evidence to another;
- `enriches` — one memory adds compatible detail;
- `related_to` — non-hierarchical association.

Narrative memories must remain meaningful records in their own right; they
should not become disposable containers after atomic facts are extracted.

## 6. Provenance model

### 6.1 `MemoryProvenance` entity

Recommended table: `memory_provenance`

| Field | Storage | Purpose |
|---|---|---|
| `provenance_id` | integer PK | Provenance identity |
| `memory_id` | FK, indexed | Referenced memory |
| `source_type` | bounded string, indexed | Adapter/source family |
| `conversation_id` | nullable FK | Normal conversation source |
| `story_session_id` | nullable FK | Guided Story source |
| `message_id` | nullable FK | Persisted normal chat message |
| `story_message_id` | nullable FK | Persisted Guided Story message |
| `source_locator` | validated JSON | Future file timestamp, page, photo region, or manual-entry locator |
| `excerpt` | text | Minimal source excerpt supporting this memory |
| `speaker` | bounded string | `user`, `berry`, or a future source speaker label |
| `chapter` | bounded string, nullable | Chapter at extraction time |
| `extracted_at` | timezone-aware datetime | Extraction time |
| `extractor_version` | bounded string, nullable | Prompt/schema/extractor version |

The row must reference a source appropriate to its type:

- `conversation` requires `conversation_id` and normally `message_id`;
- `story_session` requires `story_session_id` and normally
  `story_message_id`;
- media/document sources require their future source entity plus a locator;
- `manual` records the authenticated contributor and a manual-entry source.

Only a minimal supporting excerpt is copied. Entire messages, histories,
documents, or media transcripts must not be duplicated into each provenance
row. The source entities remain canonical.

Multiple provenance rows may support one memory, and one source message may
support multiple memories. Provenance is therefore a first-class many-to-one
entity, not a JSON field on `Memory`.

### 6.2 Required source prerequisites

Before Story Guide extraction, add:

- `Legacy`, owned by `User`;
- `StorySession`, owned transitively by `Legacy`, with chapter and status;
- `StoryMessage`, owned by `StorySession`, with role, content, and timestamp.

Before normal-chat extraction, add `Conversation.legacy_id`. Existing
conversations need an explicit migration/backfill decision; they must not be
silently assigned to whichever legacy a user opens next.

### 6.3 Extraction trace

An extraction batch should also have a lightweight `MemoryExtractionRun`:

- `extraction_run_id`
- `legacy_id`
- `source_type`
- source/session identity
- `extractor_version`
- `started_at`, `completed_at`
- status and safe failure category

Each provenance row may reference the run. This supports idempotency,
operational diagnosis, and re-extraction without storing provider payloads or
secrets.

## 7. Review lifecycle

### 7.1 Lifecycle

```text
candidate ──approve──> approved
    │                    │
    ├──reject──────> rejected
    │
    └──edit────────> candidate (new revision recorded)

approved ──edit────> approved or candidate, based on product policy
approved ──replace─> superseded ──superseded_by──> approved memory
```

Recommended initial policy:

- AI extraction always creates `candidate`.
- Manual entries may also begin as `candidate`; auto-approval should be a later
  explicit product decision.
- Approval and rejection require an authenticated user authorized for the
  legacy.
- Editing creates a `MemoryRevision` and preserves the current review state
  unless the edit materially changes the claim. Material edits return the
  memory to `candidate`.
- Rejected records are excluded from companion context and normal browsing
  unless the reviewer requests them.
- Only approved memories may eventually enter Companion Chat context.
- Superseding is an explicit human-reviewed action.

### 7.2 `MemoryRevision` entity

Recommended table: `memory_revisions`

- `memory_revision_id`
- `memory_id`
- `revision_number`
- `edited_by_user_id`
- `previous_content` as a validated JSON snapshot of editable fields
- `edit_reason` (nullable)
- `created_at`

The snapshot should include content fields, not ownership or provenance.
Provenance is independently append-only. This makes “edited” visible without
overloading review status or losing the original AI interpretation.

The UI can then show what was extracted, what changed, who changed it, and why.

## 8. Uncertainty handling

Three distinct concepts must not be collapsed:

1. **Source uncertainty** — the speaker says “I think,” “around,” or “maybe.”
2. **Account disagreement** — another person or source gives a conflicting
   account.
3. **Extraction confidence** — the extractor’s confidence that it interpreted
   the source correctly.

Source uncertainty is represented by:

- `uncertainty_note`;
- temporal/place certainty values;
- source excerpts that retain the qualifying language.

Account disagreement is represented by provenance plus a contradiction group.

Extraction confidence is stored as `extraction_confidence` for review
prioritization only. A high score does not make a claim true, and a low score
does not prove it false. Confidence should never be shown as a factual
probability without explanatory UI.

Exact dates should use ISO 8601 values. Approximate dates should use a range,
precision, original source text, and `is_approximate`. Unknown date components
must remain unknown rather than being fabricated for database convenience.

## 9. Deduplication strategy

No similarity search or embeddings are implemented in this milestone.

Future reconciliation should classify a new candidate against memories within
the **same legacy only**:

1. **Exact duplicate** — normalized type/category/claim and equivalent source
   meaning. Do not create another visible memory; attach new provenance to the
   existing memory through an idempotent operation.
2. **Semantic duplicate** — likely the same claim with different wording.
   Keep the new candidate pending and present a merge suggestion to a reviewer.
3. **Enrichment** — compatible additional detail, such as “August 1968” after
   “1968.” Propose a revised memory and attach both provenance sets. Preserve
   the previous content in `MemoryRevision`.
4. **Related but distinct** — keep separate and optionally add a memory link.
5. **Contradiction** — keep both and place them in a contradiction group.

Initial deterministic candidate keys may use normalized legacy ID, memory type,
category, and summary text to prevent retry duplicates. An extraction-run plus
source-reference uniqueness constraint should provide idempotency. Semantic
merging must remain reviewable.

The system must never deduplicate across legacies, even when names and wording
match.

## 10. Contradiction strategy

Recommended table: `memory_contradiction_groups`

- `contradiction_group_id`
- `legacy_id`
- `topic`
- `resolution_status`
- `resolution_note` (nullable)
- `resolved_by_user_id` (nullable)
- `created_at`, `resolved_at`

Every member remains a complete memory with its own provenance and review
status. A contradiction does not automatically reject or supersede either
claim.

Example:

- “Mom was born in 1968.”
- “Mom was born in 1967.”

Both are retained and linked to one group. The UI can show each account and its
source. If an authorized user later corrects the date, the correction becomes
a new or revised approved memory; prior accounts may be explicitly superseded
but remain auditable.

For compatible enrichment:

- “Mom was born in 1968.”
- “Mom was born in August 1968.”

The service may propose an enrichment rather than a contradiction. It still
requires review, a revision record, and combined provenance before changing an
approved memory.

## 11. Authorization boundaries

### 11.1 Required ownership chain

Every request must establish this chain:

```text
authenticated User
  └── owned Legacy
        ├── owned Conversation
        │     └── owned Message
        ├── owned StorySession
        │     └── owned StoryMessage
        └── owned Memory
              └── owned Provenance
```

An implementation should first resolve:

```python
legacy = LegacyCRUD.get_user_legacy(
    db,
    legacy_id=legacy_id,
    user_id=current_user.user_id,
)
```

Every subsequent query must include `legacy_id` or join through that already
verified legacy. Looking up a memory by `memory_id` alone and checking ownership
afterward is not sufficient.

### 11.2 Source validation rules

Before creating a candidate:

- the authenticated user must own the target legacy;
- a conversation source must have `Conversation.legacy_id == legacy_id`;
- a story source must have `StorySession.legacy_id == legacy_id`;
- a referenced message must belong to the referenced conversation/session;
- every provenance row must resolve to the same legacy;
- the extraction run must use server-loaded source content, not arbitrary text
  claimed by the client to belong to a source ID.

Use `404 Not Found` for inaccessible owner-scoped resources, matching current
conversation behavior and avoiding resource enumeration.

### 11.3 Future shared access

If WaffleBerry later supports collaborators or authorized legacy managers,
replace the simple owner query with a centralized `LegacyAccessService`. Memory
routes and services should depend on an access decision, not embed assumptions
that only `legacy.user_id` can authorize forever.

## 12. Deletion and privacy considerations

### 12.1 Legacy deletion

Deleting a legacy must delete or cryptographically render inaccessible:

- story sessions and story messages;
- legacy-linked conversations and messages;
- extraction runs;
- memories, revisions, participants, tags, conflict groups, and provenance;
- future source files, transcripts, derived indexes, and embeddings;
- caches and queued extraction jobs.

Database cascades can handle relational rows, but file/object storage and
vector indexes require an application-level deletion workflow with retryable
cleanup and an audit-safe completion record.

### 12.2 Source deletion

A source cannot be removed while silently leaving copied excerpts behind.
Recommended behavior:

- unreviewed candidates supported only by that source are deleted;
- approved memories supported only by that source are shown to the user as
  affected and deleted by default with their provenance;
- the user may explicitly retain a redacted, manually re-entered memory, which
  receives new `manual` provenance and no copied excerpt from the deleted
  source;
- memories with other valid sources may remain after the deleted provenance
  row and its excerpt are removed.

This policy should be implemented transactionally for database records and with
a durable cleanup job for external files.

### 12.3 Memory deletion

Deleting an individual memory removes:

- its current row;
- revisions;
- provenance excerpts and links;
- participant/tag links;
- derived indexes and embeddings.

It must not delete the underlying conversation or source by default. Conflict
groups with no remaining members should be cleaned up.

### 12.4 Data minimization

- Store only excerpts necessary to explain extraction.
- Never store provider request payloads or API credentials in extraction logs.
- Avoid putting sensitive source text in operational logs.
- Apply retention limits to failed extraction artifacts.
- Future exports must include review state, uncertainty, and provenance so an
  approved memory is not presented without context.
- Product language must describe these records as contributed information, not
  preserved consciousness.

## 13. Suggested Phase 6.5 implementation sequence

1. **Persist the ownership boundary.** Add `Legacy` and owner-scoped CRUD/API
   contracts. Reconcile this with existing frontend-only legacy identifiers.
2. **Associate conversations with legacies.** Add nullable
   `Conversation.legacy_id`, define explicit handling for existing rows, then
   require the association for memory-enabled conversations.
3. **Persist Guided Story sources.** Add `StorySession` and `StoryMessage`
   without changing the Story Guide's provider abstraction. Define pause and
   completion as explicit user/application state.
4. **Add core memory tables.** Add `Memory`, `MemoryProvenance`,
   `MemoryRevision`, participants, tags, extraction runs, and contradiction
   groups with database constraints and cascades.
5. **Add Pydantic contracts.** Implement strict enums, bounded text, validated
   temporal/place JSON, candidate responses, and review commands.
6. **Add owner-scoped repositories and service.** Keep transaction decisions in
   `MemoryService`; keep SQL in repository/CRUD modules.
7. **Implement manual candidates first.** Prove ownership, review, revision,
   provenance, and deletion behavior without AI.
8. **Add provider-neutral extraction.** Reuse `AIService`; introduce a dedicated
   extraction prompt and strict structured-output validation. Do not mix it
   with Companion or Story Guide prompts.
9. **Run extraction after durable source persistence.** Prefer a worker/outbox
   model. Retries must be idempotent and must not delay or invalidate streaming
   responses.
10. **Add deterministic duplicate and contradiction proposals.** Keep semantic
    search and embeddings out until the review workflow is proven.
11. **Add review APIs and tests.** Test cross-legacy isolation, provenance
    consistency, lifecycle transitions, retries, source deletion, and
    contradiction preservation.
12. **Only then expose approved memories to future retrieval.** Companion Chat
    integration belongs to a later milestone and must use explicit,
    owner-scoped retrieval.

## 14. Risks and open questions

### Blocking questions

1. **What is the canonical backend legacy entity?** The current backend has
   none. Its required fields and relationship to existing `VoiceProfile` must
   be decided before any memory migration.
2. **How are frontend-only legacy IDs reconciled?** They cannot safely become
   database foreign keys without a creation/import flow.
3. **Do normal conversations always belong to one legacy?** Memory isolation
   requires this. Existing conversations need a backfill, archive, or
   “unassigned” policy.
4. **Who may review memories?** Initially the legacy owner is recommended.
   Future legacy managers require a role/permission model.
5. **Should material edits return approved memories to candidate?** This design
   recommends yes, but the product must define what counts as material.
6. **What is the source retention policy?** Memory and provenance deletion
   cannot be finalized without rules for conversations, story transcripts, and
   future uploaded media.

### Non-blocking risks

- AI extraction can omit qualifiers or split/merge claims incorrectly; strict
  provenance and human review mitigate but do not eliminate this risk.
- A numeric extraction confidence may be mistaken for truth. UI wording must
  present it as extraction confidence only.
- Category drift can reduce retrieval quality. Version the category registry
  and extractor.
- Copied excerpts increase privacy exposure. Keep them minimal and delete them
  with their source.
- Concurrent extraction runs can create duplicates. Use source/run
  idempotency constraints.
- Retrying extraction after prompt changes can generate different candidates.
  Record `extractor_version`.
- PostgreSQL and SQLite differ in JSON querying and constraints. Core
  invariants should be enforced in both service validation and portable
  database constraints.
- `Base.metadata.create_all()` in application startup is not a migration
  strategy. The next milestone needs an explicit migration tool and deployment
  plan before adding production entities.
- The current Story Guide request trusts frontend identity context. Persisted
  sessions must load relationship/display name from the verified legacy.

## 15. Recommended database entities for the next milestone

### Required prerequisites

1. `Legacy`
   - `legacy_id`, `user_id`, `display_name`, `relationship`, timestamps
   - unique/ownership indexes appropriate to product rules
2. `StorySession`
   - `story_session_id`, `legacy_id`, `chapter`, `status`, timestamps
3. `StoryMessage`
   - `story_message_id`, `story_session_id`, `role`, `content`, `created_at`
4. `Conversation.legacy_id`
   - nullable during migration, required for memory-enabled conversations

### Core Memory Engine

5. `Memory`
6. `MemoryProvenance`
7. `MemoryRevision`
8. `MemoryParticipant`
9. `Tag`
10. `MemoryTag`
11. `MemoryExtractionRun`
12. `MemoryContradictionGroup`

### Optional after the core review path

13. `MemoryLink`
14. source-specific entities for voice, photo, video, and documents
15. an outbox/job entity for durable asynchronous extraction

## 16. Example contracts

The examples show API-facing snake_case objects. IDs are illustrative. Excerpts
are intentionally short.

### 16.1 Atomic memory candidate

```json
{
  "memory_id": 2401,
  "legacy_id": 42,
  "memory_type": "atomic",
  "category": "personal_detail",
  "title": "Birthplace and year",
  "summary": "Mom was born in Pune in 1968.",
  "details": {
    "places": [
      {
        "name": "Pune",
        "region": "Maharashtra",
        "country": "India",
        "certainty": "stated"
      }
    ],
    "temporal_references": [
      {
        "text": "1968",
        "start_date": "1968-01-01",
        "end_date": "1968-12-31",
        "precision": "year",
        "is_approximate": false,
        "certainty": "stated"
      }
    ]
  },
  "emotional_significance": null,
  "importance": null,
  "extraction_confidence": 0.96,
  "review_status": "candidate",
  "uncertainty_note": null,
  "participants": [
    {
      "name": "Mom",
      "relationship": "mother",
      "role": "subject"
    }
  ],
  "tags": ["early-life"],
  "provenance": [
    {
      "source_type": "story_session",
      "story_session_id": 310,
      "story_message_id": 881,
      "conversation_id": null,
      "message_id": null,
      "chapter": "Childhood",
      "speaker": "user",
      "excerpt": "I was born in Pune in 1968.",
      "source_locator": null,
      "extracted_at": "2026-07-29T14:30:00Z",
      "extractor_version": "memory-extractor-v1"
    }
  ],
  "contradiction_group_id": null,
  "superseded_by_memory_id": null,
  "created_at": "2026-07-29T14:30:00Z",
  "updated_at": "2026-07-29T14:30:00Z"
}
```

### 16.2 Narrative memory candidate

```json
{
  "memory_id": 2402,
  "legacy_id": 42,
  "memory_type": "narrative",
  "category": "story",
  "title": "First day at college",
  "summary": "Mom remembered travelling by bus with her father on her first day at college, and he waited outside until class ended.",
  "details": {
    "temporal_references": [],
    "places": []
  },
  "emotional_significance": "She remembered feeling supported and less afraid because her father stayed nearby.",
  "importance": null,
  "extraction_confidence": 0.91,
  "review_status": "candidate",
  "uncertainty_note": null,
  "participants": [
    {
      "name": "Mom",
      "relationship": "mother",
      "role": "subject"
    },
    {
      "name": "Her father",
      "relationship": "father",
      "role": "witness"
    }
  ],
  "tags": ["education", "family-support"],
  "provenance": [
    {
      "source_type": "story_session",
      "story_session_id": 312,
      "story_message_id": 910,
      "conversation_id": null,
      "message_id": null,
      "chapter": "Education",
      "speaker": "user",
      "excerpt": "Papa came on the bus and waited outside until my first class was over.",
      "source_locator": null,
      "extracted_at": "2026-07-29T15:10:00Z",
      "extractor_version": "memory-extractor-v1"
    }
  ],
  "contradiction_group_id": null,
  "superseded_by_memory_id": null,
  "created_at": "2026-07-29T15:10:00Z",
  "updated_at": "2026-07-29T15:10:00Z"
}
```

### 16.3 Uncertain memory candidate

```json
{
  "memory_id": 2403,
  "legacy_id": 42,
  "memory_type": "atomic",
  "category": "life_event",
  "title": "Possible move to Jaipur",
  "summary": "Mom may have moved to Jaipur around 1985.",
  "details": {
    "temporal_references": [
      {
        "text": "around 1985",
        "start_date": "1984-01-01",
        "end_date": "1986-12-31",
        "precision": "year",
        "is_approximate": true,
        "certainty": "uncertain"
      }
    ],
    "places": [
      {
        "name": "Jaipur",
        "region": "Rajasthan",
        "country": "India",
        "certainty": "possible"
      }
    ]
  },
  "emotional_significance": null,
  "importance": null,
  "extraction_confidence": 0.72,
  "review_status": "candidate",
  "uncertainty_note": "The speaker said both the year and place were uncertain.",
  "participants": [
    {
      "name": "Mom",
      "relationship": "mother",
      "role": "subject"
    }
  ],
  "tags": ["moving", "uncertain"],
  "provenance": [
    {
      "source_type": "conversation",
      "conversation_id": 88,
      "message_id": 1204,
      "story_session_id": null,
      "story_message_id": null,
      "chapter": null,
      "speaker": "user",
      "excerpt": "I think we moved in about 1985. It may have been Jaipur.",
      "source_locator": null,
      "extracted_at": "2026-07-29T16:00:00Z",
      "extractor_version": "memory-extractor-v1"
    }
  ],
  "contradiction_group_id": null,
  "superseded_by_memory_id": null,
  "created_at": "2026-07-29T16:00:00Z",
  "updated_at": "2026-07-29T16:00:00Z"
}
```

### 16.4 Contradictory memory pair

```json
{
  "contradiction_group": {
    "contradiction_group_id": 73,
    "legacy_id": 42,
    "topic": "Mom's birth year",
    "resolution_status": "unresolved",
    "resolution_note": null,
    "created_at": "2026-07-29T17:00:00Z"
  },
  "memories": [
    {
      "memory_id": 2404,
      "legacy_id": 42,
      "memory_type": "atomic",
      "category": "personal_detail",
      "title": "Birth year account: 1968",
      "summary": "Mom was born in 1968.",
      "review_status": "candidate",
      "extraction_confidence": 0.98,
      "uncertainty_note": null,
      "contradiction_group_id": 73,
      "superseded_by_memory_id": null,
      "provenance": [
        {
          "source_type": "story_session",
          "story_session_id": 310,
          "story_message_id": 881,
          "speaker": "user",
          "excerpt": "I was born in 1968.",
          "chapter": "Childhood",
          "extracted_at": "2026-07-29T14:30:00Z"
        }
      ]
    },
    {
      "memory_id": 2405,
      "legacy_id": 42,
      "memory_type": "atomic",
      "category": "personal_detail",
      "title": "Birth year account: 1967",
      "summary": "Mom was born in 1967.",
      "review_status": "candidate",
      "extraction_confidence": 0.94,
      "uncertainty_note": "This account differs from an earlier source.",
      "contradiction_group_id": 73,
      "superseded_by_memory_id": null,
      "provenance": [
        {
          "source_type": "document",
          "speaker": "source_document",
          "excerpt": "Date of birth: 14 August 1967",
          "source_locator": {
            "document_id": 501,
            "page": 1
          },
          "chapter": null,
          "extracted_at": "2026-07-29T17:00:00Z"
        }
      ]
    }
  ]
}
```

Neither candidate is silently overwritten, merged, approved, or rejected. The
group preserves both accounts until an authorized reviewer resolves them.

## 17. Phase 6.5.2 implementation clarification

Phase 6.5.2 implements the core schema described above with the following
clarifications:

- `Legacy.status` uses `active` and `archived`. No soft-delete column was added
  because soft deletion is not an existing backend convention.
- `StorySession.status` uses `in_progress`, `paused`, and `completed`.
- `StoryMessage.role` is limited to application-visible `user` and `assistant`
  messages. Hidden reasoning and provider payloads are not persisted.
- `Conversation.legacy_id` is nullable and uses `ON DELETE SET NULL`. Existing
  conversations remain unassigned; no synthetic legacy is created.
- Deleting a legacy cascades its Story Sessions, Story Messages, memories,
  revisions, participants, tags, contradiction groups, and provenance through
  their ownership chains. Deleting an individual source sets nullable
  provenance foreign keys to null so the minimal excerpt remains reviewable
  until the application-level privacy workflow is implemented.
- `MemoryExtractionRun`, `MemoryLink`, and source-specific media entities remain
  deferred because Phase 6.5.2 does not run extraction or process media.
- Alembic is now the schema migration authority. Startup-time
  `Base.metadata.create_all()` was removed so production schema changes cannot
  silently bypass versioned migrations.

The complete migration, index, compatibility, and repository decisions are
recorded in `docs/memory-schema-implementation.md`.
