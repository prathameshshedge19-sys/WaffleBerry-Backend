# L17 Phase A — Legacy Timeline & Life Story Structure

Status: architecture only. No L17 implementation, migration, deployment, production change, or tag was performed. Audit date: 2026-09-07.

## 1. Executive summary

L17 should add a first-class `LifeEvent` chronology layer above the existing canonical-memory layer. A LifeEvent organizes approved facts into a human-readable event, but does not replace, rewrite, or become a second canonical memory store.

The smallest robust design is:

- `life_events`: one Legacy-scoped event with title, description, type, date representation, place label, lifecycle and review/conflict state.
- `life_event_memories`: many-to-many links from events to active or historical canonical memories, with the link's role and origin.
- `life_event_evidence`: optional links from an event to L16 `source_evidence`, only when the evidence is explicitly attached or safely inherited through an approved memory-source relationship.
- `life_event_entities`: optional links to existing Legacy-scoped `MemoryEntity` rows for people and places involved.

Canonical memory remains the factual preservation layer. A family memory can create or support a timeline event without any uploaded source. L16 evidence is additional support and must never be required for event creation, display, or retrieval.

Exact-string or exact-proposition retrieval is not the intended conversational behavior. Rya must consider direct and semantically related canonical memories, relevant L13 personality evidence, verified relationship context, L17 chronology, and reasonable commonsense implications before concluding that nothing is known. Conversation-time inference may make an answer intelligent and natural, but it has zero persistence effects unless a later normal explicit human-approved flow preserves it.

Safe automation is limited to structuring explicit, approved canonical content: for example, an active memory that explicitly says “moved to Pune in 1998” may produce a proposed or automatically materialized event with that same year and place. AI must not add motives, relationships, dates, or causal links that are not present in approved data. Contradictory dates remain visible as conflict alternatives; no source silently overwrites a memory.

The recommended materialization strategy is hybrid: create/update events incrementally when canonical memories or approved L16 links change, with a deterministic Legacy-scoped rebuild command for repair and backfill. Event creation that requires a new factual claim, inferred date, semantic merge, or conflict resolution requires owner review. Visitors remain read-only; collaborators may view and suggest or create ordinary events only if Phase B explicitly permits it, but owner-only canonical deletion and conflict resolution remain unchanged.

## 2. Existing architecture findings

### Verified audit boundary

The active checkout contains `WaffleBerry-Backend` and `WaffleBerry-Frontend`. The requested L16 release references are supplied by the brief: backend `159ea7e6866d52c328d52edc8e6ccf735ffadfb1`, frontend `66a4162c03b6c86b88b06162b3c2b038dfa26658`, tag `legarya-l16-media-sources`, and production migration `0018_media_intelligence`. This Phase A did not connect to production or change Git metadata.

The checked-in Alembic chain is `0018_media_intelligence` after `0017_media_sources`. Existing L16 release documentation records seven media/source tables, scoped foreign keys, worker generation fencing, review receipts, and source deletion behavior.

### Canonical memory

`Memory` is Legacy-scoped and has `active`, `superseded`, and `deleted` status values. It stores canonical text, category, confidence, contributor, conversation/message excerpt provenance, embeddings, entities and `story_key`. `MemoryRevision` records old/new text, change type, actor, and conversation/message references. `superseded_by_memory_id` preserves correction/supersession lineage.

`LivingMemoryService` owns provider analysis, explicit-save handling, confidence filtering, deduplication, embedding, entity synchronization, correction, supersession and deletion. It expects conversation/message context for normal conversational writes. L17 must call or extend this service for canonical-memory corrections; it must not create a parallel candidate-memory authority or fabricate a conversation to bypass its rules.

The current API scopes memory routes by both `legacy_id` and memory ID. Dashboard deletion and conversational canonical DELETE are owner-only; collaborators can contribute/edit through the existing philosophy but cannot delete canonical memories.

### L16 sources and provenance

L16 separates `MediaSource`, `MediaArtifact`, `MediaProcessingJob`, `SourceEvidence`, `SourceMemoryCandidate`, `SourceCandidateEvidence`, and `MemorySourceLink`. Evidence is bounded, attributed and generation-scoped. Candidates require human review before canonical promotion. `MemorySourceLink` describes approved, unavailable or stale support and stores the approved text digest.

Source deletion revokes source/evidence access and leaves valid human memories in place with unavailable provenance. This is exactly the behavior L17 needs: deleting a file can remove optional support, but must not erase a family memory or its valid timeline event.

### Authorization and Legacy isolation

`require_legacy` grants builder access to the owner or active collaborator and supports `owner_only=True`. `require_persona_legacy` grants viewer/persona access. `legacy_role` distinguishes owner and collaborator. Current lookup patterns combine resource ID with `legacy_id`; L17 must retain that pattern and add composite SQL constraints for all new relationships.

Visitors use a separate viewer/persona path and existing visitor identity/privacy filtering. They may ask Rya questions but cannot edit memories or access builder mutation routes.

### Conversation and Rya

L14/L15 conversation turns bind server-side tools to an authorized Legacy and claimed turn. `conversation_tools.py` currently exposes read-only memory, personality, visitor relationship and current-information tools. Tool calls recheck authorization after awaited work and never mutate memory, personality, activity or lifecycle. L17 should add a narrowly scoped read-only timeline retrieval capability to this existing tool registry, not a second conversation brain.

`memory_grounding` already instructs Rya to qualify weak inference and acknowledge conflicting active records. Timeline retrieval should return structured chronology plus conflict metadata and let the existing builder/persona compose the response.

### Personality and activity

L13 personality is a derived projection invalidated transactionally by canonical-memory/entity changes and rebuilt by a leased worker. Timeline rows must not become personality evidence or traits. Timeline mutations may record existing builder activity where product policy requires it, but `BuilderActivity` is not an audit log and should not be repurposed.

### Frontend and test infrastructure

The builder UI is static HTML/CSS/JS. `chat.html` hosts the Memory Dashboard, Personality panel, collaborator/access panels and L16 Media & Sources entry point. `memory-dashboard.js` fetches `/memories`, renders safely with `textContent`, supports edit/delete, and passes memory data to the personality panel. `legacy-chat.html` is the visitor surface. `workspace-role.js` distinguishes owner/collaborator and locks collaborator navigation appropriately. Backend tests use SQLite by default with opt-in PostgreSQL concurrency/constraint suites; frontend tests use Node's test runner and static contracts.

## 3. Reusable components

Reuse:

- `Memory`, `MemoryRevision`, `MemoryEntity`, `MemoryEntityLink`, `LivingMemoryService`, canonical status values and active-only retrieval.
- L16 `SourceEvidence` and `MemorySourceLink` for optional source support; do not duplicate source artifacts or candidate review tables.
- `require_legacy`, `require_persona_legacy`, `legacy_role`, existing owner-only checks and explicit `legacy_id` predicates.
- L13 transactional invalidation and worker lease/generation ideas for a separate timeline projection/rebuild job if measurements justify it.
- L14 conversation turn/tool binding, result-size limits, fresh-session reauthorization and read-only tool contract.
- Existing frontend auth client, workspace selector, role policy, safe rendering and stale-response suppression.

Do not reuse `MemoryRevision` as an event revision table: it belongs to canonical memory text. Do not reuse personality tables for timeline state, `TurnEffect` for timeline mutations, or L16 candidates as a new event-candidate architecture.

## 4. Core L17 invariants

1. Every event and relationship belongs to exactly one Legacy.
2. No uploaded source is required for a valid event.
3. Canonical memory remains authoritative for preserved factual content.
4. An event may summarize linked memories, but must not silently contain unsupported claims.
5. L16 evidence is optional support, not a truth hierarchy or approval gate.
6. Conflicting dates/facts are represented, not silently selected or overwritten.
7. Unknown and approximate dates remain unknown/approximate; no fake precision is stored.
8. Human clarification that changes canonical meaning uses existing memory correction/supersession logic.
9. Visitors are read-only and cannot create personality effects.
10. A foreign Legacy ID or related resource ID must never disclose or mutate data.
11. Deleting source evidence cannot delete a still-valid human memory or event.
12. AI proposals cannot become canonical history without the existing human approval boundary.
13. Conversational retrieval must use relevant context and grounded implication, not exact-string matching; “I don't remember” is a last-resort fallback.
14. Conversational inference is ephemeral: it has no canonical, provenance, personality, timeline or activity effect.

## 5. LifeEvent domain model

Recommended fields for `life_events`:

| Field | Meaning |
| --- | --- |
| `id` | Application-generated UUID string. |
| `legacy_id` | Required FK to `legacies.id`; composite scope key for relationships. |
| `title` | Short display title, e.g. “Moved to Pune”. Human-editable. |
| `description` | Optional concise event summary; must be supported by linked canonical memories. |
| `event_type` | Controlled category such as `move`, `education`, `career`, `relationship`, `family`, `health`, `achievement`, `travel`, `other`. This is organization, not a claim. |
| `date_start`, `date_end` | Nullable calendar dates. Same date for an exact day; month/year represented by precision rather than fake day values. |
| `date_precision` | `day`, `month`, `year`, `range`, `life_period`, or `unknown`. |
| `is_approximate` | Explicit flag for “around”, “about”, or similarly qualified dates. |
| `date_label` | Optional bounded human-facing wording such as “during college” or “around 1998”; not used as an unvalidated sort key. |
| `sequence_hint` | Nullable owner/reviewed integer used only when an explicit relative order is known but dates are unavailable. |
| `place_label` | Optional human-facing place wording. |
| `confidence` | Bounded structural confidence, not a measure of a person’s truthfulness; derived from support/review state and exposed carefully. |
| `origin` | `canonical_structured`, `human_created`, `source_supported`, or `ai_proposed`. |
| `review_state` | `approved`, `needs_review`, `conflict`, `superseded`, `archived`. |
| `lifecycle_state` | `active` or `deleted`; use soft deletion for auditability. |
| `created_by_user_id`, `updated_by_user_id` | Nullable actor FKs. |
| `created_at`, `updated_at` | Existing UTC timestamp convention. |

Do not copy full memory text into each event. The event's title/description is a presentation summary and must be regenerated or edited when support changes. The linked canonical memories remain the factual records.

Places and people should initially reuse `MemoryEntity`: event-entity links carry roles such as `subject`, `person_involved`, `place`, `organization`, or `mentioned`. If an entity is not yet known, retain the bounded event `place_label` rather than inventing an entity. A later phase may add structured geocoding, but L17 does not need it.

## 6. Date and precision model

Represent dates as a pair of nullable calendar dates plus explicit precision:

- exact day: `date_start=1998-06-12`, `date_end=1998-06-12`, `precision=day`, `approximate=false`;
- month: use the first and last day of June 1998 as machine bounds, `precision=month`, and display “June 1998”; never display the first day as exact;
- year: use `1998-01-01` and `1998-12-31`, `precision=year`, display “1998”;
- approximate year: same year bounds, `precision=year`, `approximate=true`, display “around 1998”;
- range: explicit start/end bounds, `precision=range`, optionally approximate;
- life period: null dates, `precision=life_period`, bounded `date_label="during college"` and only a reviewed `sequence_hint` if order is explicit;
- unknown: null dates, `precision=unknown`, no invented label beyond “Date unknown”.

For sorting, use the lower bound of `date_start` as `sort_date`, then `sequence_hint`, then stable `created_at/id`. Approximate and coarse events remain interleaved by their known bounds; the UI must communicate precision. A range overlaps every event whose bound intersects it; retrieval must not claim exact ordering where intervals overlap. “Before/after” answers should be qualified when the intervals overlap or one event has an unknown date.

The builder may edit date fields only with an explicit human action. An AI extractor may populate a proposal from an explicit memory phrase, but an inferred date or inferred order creates `needs_review`.

## 7. Memory-event relationship

Use `life_event_memories` as a many-to-many table because one memory can support multiple events and an event can have multiple perspectives/memories. Minimal fields: `legacy_id`, `event_id`, `memory_id`, `link_role` (`primary_support`, `additional_support`, `alternative_account`), `link_state` (`active`, `stale`, `removed`), `linked_by`, `linked_at`, and optional `source_memory_updated_at`/digest for stale detection.

All three IDs must be checked in application code and by composite foreign keys: `(legacy_id,event_id)`, `(legacy_id,memory_id)`. A link to a superseded/deleted memory is retained only as historical lineage and is not active support. Multiple active memories may support one event; a single memory may support multiple distinct events when its text explicitly covers them.

## 8. Optional evidence model

Use `life_event_evidence` only for optional L16 `SourceEvidence` attachments that are visible to the authorized builder. Minimal fields: `legacy_id`, `event_id`, `evidence_id`, `link_state` (`available`, `unavailable`, `removed`), `linked_by`, `linked_at`, and optional `approved_memory_id` when the evidence is attached through a canonical memory path.

The table must use composite FKs to `(legacy_id,event_id)` and `(legacy_id,evidence_id)` and a unique constraint on `(legacy_id,event_id,evidence_id)`. It must not make `evidence_id` non-null on `life_events`. If a source is deleted or provenance is unavailable, keep a tombstone/status for the relationship but continue showing the event when canonical support remains. UI language should say “family-contributed”, “supported by 2 memories”, or “source-backed”, never “unverified” solely because no file exists.

## 9. Origin and trust model

`origin` answers how the event entered the timeline, not how truthful a family member is. Recommended meanings:

- `canonical_structured`: deterministic organization of one or more active canonical memories;
- `human_created`: owner-created event summary with an explicit memory link or direct owner statement;
- `source_supported`: a human-approved event/memory relationship additionally linked to L16 evidence;
- `ai_proposed`: a non-visible-to-Rya or review-visible proposal that has not become approved timeline state.

Only the first three can be active/approved. AI proposals cannot be used as canonical retrieval context until reviewed. A source-backed event is not inherently more trustworthy than a family-contributed event; provenance counts should be additive metadata.

## 10. Human review rules

No review is needed for deterministic structuring that preserves an explicit approved fact: exact year to year, explicit place to place, explicit named person to involved-person link. Review is required for inferred dates, inferred people/relationships, causal claims, relative ordering not stated in the memories, materially rewritten descriptions, merging distinct accounts, or choosing between conflicting alternatives.

Review should be a lightweight event detail action: approve proposed fields, edit them, link/unlink support, mark alternatives, or resolve conflict. It must not ask for a document merely because one is absent. If the owner clarifies “it was 1999, not 1998”, canonical correction/supersession is the durable change; L17 then re-evaluates the affected event.

## 11. Conflict model

Keep one event when accounts clearly refer to the same occurrence, but represent disagreement in `review_state=conflict` and retain alternative date/value assertions in a small structured `conflict_json` or a dedicated conflict table only if Phase B finds multiple-field conflicts need querying. Phase A recommends the minimal JSON initially: field name, alternatives, supporting memory IDs, supporting evidence IDs, detected reason, status, resolution actor/time.

Do not widen `1998` and `1999` into `1998–1999` automatically; that changes meaning. Display “two dates preserved: 1998 and 1999” and ask the owner for clarification when relevant. A certificate saying 1988 does not silently overwrite a 1987 family memory. Once clarified, use `LivingMemoryService.edit`/correction/supersession, record `MemoryRevision`, then regenerate the affected event.

## 12. Consolidation and deduplication

Consolidation is conservative and Legacy-local. A deterministic candidate may match an existing event only when normalized subject/place/type and compatible date intervals agree, and the supporting memories have no unresolved conflict. Exact same event fingerprints can idempotently attach a new memory. Semantic similarity alone must not merge events.

When ambiguous, create `needs_review` or leave separate events with a “possibly related” review cue; do not create a new relation table in Phase A. Preserve distinct perspectives when wording, time, place, or event type suggests they may be separate.

## 13. Correction, supersession and deletion

- Memory edit/correction: event support is re-read after the canonical transaction. Rebuild title/date/place/description only from the new active text; preserve unrelated supports.
- Memory supersession: deactivate the old link, attach the superseding memory when it describes the same event, and retain lineage. If no active support remains, mark the event `needs_review` or archive it according to owner policy; never leave stale active factual text.
- Owner memory deletion: remove active support, preserve a deletion/audit tombstone, and archive an event only if it has no other active memory or approved human event support.
- Source deletion: mark event evidence unavailable; do not delete a valid event or memory.
- Evidence unavailable/stale: remove it from active support counts and show the state to builders who can see provenance.
- Event deletion: owner-only soft delete; it must not delete canonical memories, revisions, entities, or source rows.

Conversational canonical DELETE remains owner-only. L17 event deletion and conflict resolution should also be owner-only in the first release unless product explicitly introduces a narrower collaborator capability.

## 14. Timeline invalidation and rebuild

Use an incremental invalidation key per Legacy, conceptually `timeline_source_generation`, advanced in the same transaction as canonical memory changes and event-support changes. Affected event IDs can be queued when known; a Legacy-wide rebuild is reserved for migration/backfill, parser changes, or repair.

Triggers: new active canonical memory, canonical edit/supersession/deletion, approved memory-source link changes, explicit event edit, evidence availability/deletion, and conflict resolution. Work outside DB transactions; publish only if the generation and Legacy still match. Rebuild is deterministic and idempotent. No trigger should invoke an expensive full rebuild for an unrelated Legacy.

L17 may initially perform synchronous small event-link updates and a bounded async regeneration job for summaries. If a job table is needed, follow the existing leased worker/generation pattern; do not add timeline jobs to the personality table.

## 15. Rya builder behavior

For owners and collaborators, Rya may describe preserved chronology and say “we have a few memories from her college years” or “there is a gap in the timeline”. Gaps are invitations, not errors and not evidence failures. It may ask one useful clarification for a meaningful conflict, not repeatedly request proof or uploads.

Timeline context should be selected by query: relevant date window, event type, place, or before/after relation. Do not inject the entire timeline blindly. Return compact structured event records with date display, uncertainty, conflict state, support counts, and canonical-memory references; the existing prompt builder remains responsible for wording and privacy.

## 16. Visitor behavior

Visitors can browse only events allowed by existing persona/viewer authorization and privacy filtering, and can ask chronological questions through the read-only Legacy conversation. They cannot create/edit/delete events, link memories/sources, resolve conflicts, create canonical memories, or cause personality effects. Unknown/approximate dates and conflict labels should be phrased gently without exposing private builder-only provenance or source content.

## 17. Collaborator permissions

Active collaborators retain builder read access and existing memory contribution/edit behavior. Phase B should start with timeline viewing and contribution through the existing canonical memory flow. Owner-only operations: canonical deletion, event deletion, conflict resolution, changing a materially disputed date, and approving an AI proposal that changes meaning. A collaborator may be allowed to edit a clearly human-created event summary only after explicit product confirmation; this is not implied by current collaborator access.

## 18. Cross-Legacy isolation

Every route accepts or derives one `legacy_id`, authorizes that Legacy first, and queries every event/link/evidence/memory/entity with matching scope. Use composite unique keys and foreign keys so a valid ID from Legacy A cannot be paired with a row from Legacy B. Direct item routes must return the same non-disclosing 404 policy used by existing resources. Add tests for guessed event IDs, foreign memory-event links, foreign evidence links, unauthorized reads, and conflict mutations.

## 19. Personality interaction

Do not add timeline dates or events as personality traits. Existing L13 personality evidence remains authoritative and continues to derive from active canonical memories. Timeline changes may invalidate personality only indirectly when the underlying canonical memory changes; a pure event title/order edit must not rebuild personality. If future prompts use timeline context, keep it query-scoped and factual, never as style evidence.

## 20. Conversation and retrieval integration

Phase B should add a `TimelineService` read API and extend the existing `ConversationTools` registry with one read-only `retrieve_legacy_timeline` tool for authorized builder/persona turns. The service should support:

- chronological list/window;
- event detail by Legacy-scoped ID;
- before/after a named event when ordering is known;
- period queries such as “around 2005” or “during college”;
- conflict/gap summaries.

It should reuse `analyze_legacy_query` routing and current turn authorization. If a query can be answered by existing memory retrieval, the timeline tool is optional; no second planner or provider brain is needed. The tool output is untrusted data, bounded like existing tools, and reauthorized after any awaited work.

### Mandatory conversational reasoning invariant

L17 must separate “how Rya speaks now” from “what the system stores”. Retrieval is not exact-string lookup and must not depend on the question's wording being present verbatim in a memory. The conceptual response policy is internal:

| Internal support tier | Generation behavior |
| --- | --- |
| A — directly supported | Answer naturally and confidently from the relevant memory/event. |
| B — strongly implied by preserved context | State the reasonable interpretation and include the supporting details. |
| C — partial, ambiguous or conflicting | Give what is known and qualify uncertainty naturally. Do not silently choose an alternative. |
| D — unsupported after contextual retrieval | Use a natural memory-absence response as a last resort. |

These labels, along with “canonical support”, “retrieval confidence”, “inference”, database wording and internal provenance details, must remain out of ordinary visitor-facing responses. Avoid phrases such as “I don't have a preserved memory that states that directly” or “the database contains conflicting records”. Prefer human language: “We spent many Sundays together at the park” or “I remember it as around 1998 or 1999; I have two recollections of the year.” “I don't remember” is appropriate only after direct, semantic, personality, verified-relationship, timeline and reasonable-implication context has been considered.

Grounded implication is allowed for ordinary summaries and patterns: weekly outings may support “we spent many Sundays together”; several family activities may support “family was a big part of my life”. Do not exaggerate into unsupported superlatives, motives, causality or inner states. “Moved in 1998” plus “became a teacher in 2001” does not support “moved because of teaching” unless an approved memory supports that causal link. Personality may shape warmth, humor, directness and phrasing, but cannot manufacture a specific life fact. Relationship context may be used only when verified under existing visitor authorization; a visitor's claimed relationship alone is never sufficient.

Timeline-aware retrieval should combine relevance with chronology and domain context: retrieve nearby intervals for “around 2000”, ordered successors for “after college”, predecessors for “before teaching”, and related memories for the selected events. Do not solve the old refusal behavior by simply increasing top-N results. Rank a bounded set using semantic relevance, event/date overlap, event type/place/entity cues and directness, then apply the existing authorization, privacy, conflict and result-size boundaries.

Conversation-time inference must not create a canonical memory, `MemoryRevision`, L16 evidence, personality mutation, L17 event/link, activity receipt or other durable effect. Visitor-side inference has zero canonical effects. Existing read-only conversation tools and turn reauthorization remain in force. Naturalness never overrides factual integrity: when support is insufficient, answer modestly; when accounts conflict, acknowledge the conflict naturally.

## 21. L18 preparation boundaries

LifeEvents provide stable chronological anchors and support counts for future Stories/Biography chapters. L17 should not add narrative chapters, prose generation, chapter ordering, biography drafts, or a new story store. `event_type`, `story_key` where useful, memory links, date intervals and conflict metadata are sufficient preparation.

## 22. Proposed schema and migration plan

Conceptual next migration: `0019_legacy_timeline`, additive after `0018_media_intelligence`; no migration file was created in Phase A.

Proposed tables:

1. `life_events`: UUID PK; `legacy_id` RESTRICT/CASCADE only according to existing Legacy deletion convention; bounded title/description/type/date fields; `is_approximate`; `date_label`; `sequence_hint`; `place_label`; origin/review/lifecycle; actor/timestamps; optional bounded `conflict_json`.
2. `life_event_memories`: composite-scope event/memory link, role/state, actor/time, optional digest; unique `(legacy_id,event_id,memory_id,link_role)`.
3. `life_event_evidence`: composite-scope event/evidence link, availability state, actor/time; unique `(legacy_id,event_id,evidence_id)`.
4. `life_event_entities`: composite-scope event/entity link and role; unique `(legacy_id,event_id,entity_id,role)`.

Indexes: `(legacy_id,lifecycle_state,sort_date,sequence_hint,id)`, `(legacy_id,review_state,updated_at)`, `(legacy_id,memory_id)`, `(legacy_id,evidence_id)`, and `(legacy_id,entity_id)`. Add checks for bounded text, valid precision/state/origin, nonnegative/ordered date bounds, and approximate/display consistency. All relationships require composite FKs to existing scoped unique keys. Prefer status strings and named constraints per current migrations; do not use PostgreSQL-only enums.

The migration should be additive, idempotent at the application command level, and followed by a deterministic backfill from active canonical memories. Backfill must not auto-create events from implicit/inferred dates without review. A rollback must remove only L17 rows/tables, not canonical memories or L16 provenance.

## 23. API plan

Minimal Phase B/C API surface:

| Endpoint | Authorization | Purpose |
| --- | --- | --- |
| `GET /timeline?legacy_id=...` | owner/collaborator builder; persona viewer where allowed | List sorted events with date display, uncertainty, conflicts and support counts. |
| `GET /timeline/{event_id}?legacy_id=...` | same, with privacy filtering | Event detail, linked memory summaries and permitted provenance. |
| `POST /timeline/events` | owner initially | Create a human event or approve a deterministic proposal. |
| `PATCH /timeline/{event_id}` | owner initially | Correct display/date/type/place; material changes require explicit owner action. |
| `DELETE /timeline/{event_id}` | owner only | Soft-delete event, never linked memories. |
| `POST /timeline/{event_id}/review` | owner only | Approve proposal, mark conflict resolved, or record clarification reference. |
| `GET /timeline/gaps?legacy_id=...` | builder/persona read policy | Summarize underrepresented periods without fabricating events. |

Do not expose raw source artifacts through timeline routes. Reuse existing source-provenance authorization for optional evidence detail. API responses should distinguish `family_contributed`, `source_backed`, `memory_count`, `source_count`, `date_precision`, `is_approximate`, `has_conflict`, and `review_state`.

## 24. Frontend plan

Add a Timeline entry to the builder navigation near Memories and Media & Sources. Keep the existing visual language and safe DOM rendering. Phase C should provide chronological groups, coarse/approximate labels, unknown-date grouping, event detail, memory/source provenance, conflict indicator and lightweight owner review. On mobile, cards must remain readable without relying on hover or dense horizontal layouts.

The visitor surface should get a read-only chronology view only after the backend policy is stable. Do not place owner edit/delete controls in visitor markup and do not rely on hiding controls as authorization. Gaps should use inviting language such as “There are fewer preserved memories from this period”; never “missing proof”.

## 25. Test plan

### SQLite/service/API tests

- canonical memory with no source creates a valid event;
- source-backed canonical memory creates/links an event;
- lack of source never blocks event creation or retrieval;
- a later L16 source/evidence link supports an existing event;
- source deletion marks optional support unavailable but preserves the event/memory;
- exact day, month, year, approximate year, range, life-period and unknown-date serialization/display;
- interval sorting, overlap qualification, stable unknown-date ordering;
- one memory to many events and many memories to one event;
- conservative duplicate consolidation and refusal to merge ambiguous events;
- conflicting dates remain alternatives and trigger clarification state;
- owner conflict resolution uses canonical correction/supersession where applicable;
- memory correction, supersession and deletion remove stale active support without removing unrelated support;
- event soft deletion does not affect canonical memory or L16 rows;
- no AI factual invention or unreviewed proposal in Rya retrieval;
- exact wording absent but a related behavior is preserved: answer from the grounded implication rather than returning bare “I don't remember”;
- multiple related family memories support a natural pattern summary without unsupported emotional intensity;
- separate move and career dates are stated without inventing a causal relationship;
- a school attendance memory alone does not invent that school was difficult;
- personality warmth affects wording but does not create specific facts;
- an unverified visitor relationship does not unlock familial assumptions;
- conversation-time grounded inference produces zero canonical memories, revisions, source evidence, personality mutations, timeline writes, builder activity, or visitor canonical effects;
- ordinary responses do not expose exact-memory, database, retrieval-confidence or canonical-support language;
- gap summaries do not fabricate events;
- incremental invalidation and deterministic rebuild are idempotent;
- owner/collaborator/visitor route behavior and no personality mutation from timeline changes;
- API result bounds and safe rendering contracts.

### PostgreSQL-required tests

Use PostgreSQL for composite-FK enforcement, concurrent duplicate event admission, concurrent memory correction versus regeneration, conflict review versus rebuild, source deletion versus evidence linking, Legacy deletion behavior, transaction rollback, and generation/lease races. SQLite remains useful for fast service and response-shape tests but is insufficient proof for these locking and constraint guarantees.

### Retrieval acceptance

Test “what happened after she moved to Pune?”, “tell me about college years”, “what was happening around 2005?”, and “what happened before she became a teacher?” with exact, approximate, range, unknown and conflicting data. Also test the grounded examples: weekly Saras Baug outings answering a question about time with children; homework, school functions and weekend outings answering a family-importance question; 1998 move plus 2001 teaching without invented causality; St. Mary's attendance without invented difficulty; conflicting 1998/1999 move years acknowledged naturally; and college → Pune → teaching chronology used for “what happened after college?”. Assert that answers qualify uncertainty, do not invent causal links, do not expose another Legacy, do not default to exact-memory refusal, and do not ask for documents when a family memory is already sufficient.

## 26. Risks and open questions

- Event summaries can drift from canonical text; Phase B must choose synchronous deterministic summaries versus a bounded regeneration worker.
- “Same event” is inherently ambiguous; conservative non-merging is safer but can leave duplicates for owner review.
- Month/year interval bounds need careful UI wording and overlap semantics.
- Direct event evidence links may be unnecessary if all evidence flows through memory provenance; measure actual use before expanding the table.
- Existing collaborator policy does not prove collaborator event-edit authority; keep owner-only initially.
- Timeline gap categories must remain optional prompts and must not encode a culturally universal life path.
- The production brief's release references should be rechecked against repository Git metadata in Phase B using the operator's normal safe-directory workflow; this Phase A did not assert a fresh production or Git checkout state.

## 27. Recommended Phase B sequence

1. Freeze API/domain contracts and confirm the final source/evidence attachment policy.
2. Add ORM models and `0019_legacy_timeline` with composite constraints; run migration tests only in a disposable database.
3. Implement deterministic date parsing/formatting and conservative event-link service, with no provider dependency.
4. Implement owner event review/correction and canonical-memory integration, including invalidation and deletion matrix.
5. Add PostgreSQL concurrency/isolation tests and rebuild/invalidation worker if measurements require async work.
6. Add the bounded timeline retrieval service/tool to existing conversation architecture.
7. Add owner/collaborator builder APIs and frontend timeline dashboard; add visitor read-only presentation only after privacy tests.
8. Perform focused acceptance, documentation, backup and release review. Deployment/tagging remain outside Phase A.

## Audit record and requested handoff

Files/docs inspected included:

- `backend/docs/L16_PHASE_A_ARCHITECTURE.md`
- `backend/docs/L16_PHASE_B_REPORT.md`
- `backend/docs/L16_PHASE_C_REPORT.md`
- `backend/docs/L16_PHASE_D_RELEASE_REPORT.md`
- `backend/app/models/{legacy,memory,collaboration,media_source,media_intelligence,personality,conversation,visitor}.py`
- `backend/app/services/{memory,authorization,legacy_access,conversation_tools,media_provenance,personality_invalidation,legacy_personality,progression}.py`
- `backend/app/api/routes/{memories,media_sources,legacy_conversations,conversations,personality,legacies,collaborations}.py`
- `backend/app/schemas/{memory,media_source,personality,conversation_tools}.py`
- `backend/alembic/versions/0017_media_sources.py` and `0018_media_intelligence.py`
- `backend/tests/conftest.py` and relevant L4/L5/L13/L14/L15/L16 test modules
- `WaffleBerry-Frontend/chat.html`, `legacy-chat.html`, `js/{memory-dashboard,personality-dashboard,workspace-role,media-sources,chat,legacy-chat}.js`, and relevant frontend tests

Existing components to reuse are the canonical `Memory`/`MemoryRevision` lifecycle, `LivingMemoryService`, MemoryEntity graph, L16 `SourceEvidence`/`MemorySourceLink`, authorization helpers, L13 invalidation/worker patterns, L14 read-only conversation tools, and existing builder/persona frontend role boundaries.

Exact proposed architecture: a Legacy-scoped `LifeEvent` with explicit interval/precision/approximation, optional date label and sequence hint, many-to-many canonical memory links, optional L16 evidence links, reused MemoryEntity people/place links, explicit origin/review/conflict/lifecycle state, and soft deletion. Canonical memories without evidence participate fully. L16 evidence can attach later as additive support. Contradictions stay as alternatives requiring owner clarification; they are not silently merged or overwritten.

The conceptual migration is `0019_legacy_timeline`, but it was not created. No implementation, migration, deployment, production modification, or tag occurred during this Phase A audit. The exact report path is [`WaffleBerry-Backend/backend/docs/L17_PHASE_A_ARCHITECTURE.md`](C:\Users\Saee\Desktop\Waffleberry-new\WaffleBerry-Backend\backend\docs\L17_PHASE_A_ARCHITECTURE.md).
