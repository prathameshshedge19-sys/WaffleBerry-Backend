# L17 Phase B — Timeline Backend & Intelligence Report

Status: implementation complete for the backend foundation; production release is deferred. No deployment, production database change, or L17 tag was performed. Report date: 2026-09-08.

## 1. Scope implemented

Phase B adds the additive Legacy Timeline backend: four persistence tables, explicit date semantics, deterministic event structuring, many-to-many canonical-memory links, optional L16 evidence links, MemoryEntity links, conflict marking, owner review/mutation APIs, memory lifecycle synchronization, bounded timeline retrieval, a read-only conversation tool, and grounded conversational policy updates.

The polished Timeline frontend, mobile UI, visitor timeline page and final review UX remain deferred to Phase C.

## 2. Phase A decisions followed

LifeEvent is a chronological organization layer, not a second canonical-memory system. Human/family canonical memories are valid without documents. L16 evidence is additive support. Dates retain precision and uncertainty. Conflicts are not silently selected or widened into ranges. Visitor inference and ordinary conversational inference have zero durable effects.

## 3. Migration/schema

Added migration `0019_legacy_timeline` after `0018_media_intelligence`. It creates:

- `life_events`
- `life_event_memories`
- `life_event_evidence`
- `life_event_entities`

The migration also adds the scoped unique key required to enforce `(legacy_id, id)` foreign keys for existing `memory_entities` and `source_evidence`. It is additive and does not modify `0017` or `0018`.

Composite foreign keys prevent cross-Legacy event/memory/evidence/entity attachments. Named checks enforce date ordering, bounded values and status/origin/precision strings. Indexes support Legacy-scoped chronological, review, memory, evidence and entity access.

## 4. LifeEvent model

`LifeEvent` stores UUID identity, `legacy_id`, title, concise description, event type, interval bounds, sortable lower bound, precision, approximation, date label, explicit sequence hint, place label, structural confidence, origin, review state, lifecycle state, bounded conflict JSON, actors and timestamps. Event text is a presentation summary; canonical memory remains the factual text.

## 5. Date semantics

The service supports day, month, year, approximate date/year, range-compatible interval bounds, life periods and unknown dates. Month/year bounds are used for sorting only; API responses retain `date_precision` and `date_label`, so `1998` is never displayed as `1 January 1998`. Unknown dates sort after known dates using stable sequence/creation ordering. Overlapping intervals are not presented as exact before/after ordering.

## 6. Memory/event relationships

`life_event_memories` is many-to-many. It records primary support, additional support or alternative account, active/stale/removed state, actor/time and the source memory update timestamp. Historical or superseded memories remain available for lineage but do not count as active factual support.

## 7. Optional evidence integration

`life_event_evidence` links an event to an existing L16 `SourceEvidence` row. The owner-scoped `POST /timeline/{event_id}/evidence` endpoint verifies both Legacy scope and evidence availability. It does not duplicate L16 infrastructure. Removed/unavailable evidence reduces source support but does not delete a valid family memory or event.

## 8. Entity integration

`life_event_entities` reuses existing Legacy-scoped `MemoryEntity` rows for people, places and organizations. The implementation does not create a second person/place graph and does not infer people merely from names or relationship language.

## 9. Structuring and backfill

`TimelineService.structure_memory` deterministically recognizes explicit event wording such as moving to a named place, graduation and becoming/starting work, and extracts only explicit date/place text. `rebuild(legacy_id)` is a deterministic, Legacy-scoped, idempotent backfill path over active canonical memories. It does not send all memories through an unconstrained AI provider.

Canonical memory without a source is fully eligible. Unrecognized or materially inferential chronology is not automatically materialized. No motive, causality, emotional meaning, unstated relationship or unstated ordering is generated.

## 10. Consolidation

Consolidation is deterministic and conservative: matching event title/type and compatible explicit chronology can attach multiple memories to one event. The same event with incompatible explicit dates remains one event with conflict alternatives. Semantic similarity alone is not used to merge unrelated moves, jobs, graduations or trips.

## 11. Conflict handling

Incompatible explicit date accounts such as 1998 versus 1999 set `review_state=conflict` and preserve alternatives in `conflict_json` with supporting memory IDs. The implementation never silently chooses a date or converts alternatives into `1998–1999`. Owner resolution clears the event marker; if canonical meaning changes, the owner must use the existing Memory edit/correction/supersession path.

## 12. Review and owner operations

Owner-only APIs support human event creation, display/chronology edits, soft deletion, approval, conflict resolution and optional evidence attachment. Events without canonical support enter `needs_review` and are excluded from approved/conflict Rya retrieval until reviewed. Event deletion never deletes memories, revisions, entities, source evidence or L16 sources.

Collaborators can view the builder timeline and continue contributing through existing canonical-memory flows. They cannot delete events, resolve factual conflicts, approve materially changing AI proposals or override disputed chronology. Visitors remain read-only.

## 13. Correction/supersession/deletion

Canonical `store`, dashboard edit and owner delete paths now reconcile affected timeline support. Active edits are re-evaluated; deleted/superseded support becomes stale; unrelated event support remains. Source deletion is represented as unavailable evidence while human-supported events remain. No timeline operation bypasses `MemoryRevision` or canonical supersession semantics.

## 14. Rebuild/invalidation

The Phase B implementation uses bounded synchronous reconciliation for known memory changes and a deterministic Legacy-level rebuild method for repair/backfill. It does not add a worker prematurely. Reconciliation is idempotent and only touches affected memory/event links. A future async job can adopt existing generation/fencing patterns if production measurements require it.

## 15. Timeline service

`TimelineService` supports Legacy-scoped sorted lists, date windows, event type, entity filtering, bounded query retrieval, conflict data and a conservative gap summary. Retrieval combines chronology/date cues, event type and lexical cues; it does not retrieve huge unrelated memory sets.

## 16. Conversation tool

Added provider-neutral read-only `retrieve_legacy_timeline` to the existing L14/L15 tool registry. The server binds Legacy and actor scope, applies existing authorization, bounds query/results, returns untrusted structured event data, and rechecks the current turn scope after awaited work. No timeline mutation tool is visible to the provider/realtime model.

## 17. Natural grounded reasoning

The persona contract now explicitly requires direct memories, semantically related memories, timeline context, personality context, verified relationship context and reasonable commonsense implications to be considered before a memory-absence response. Exact proposition matching is not the behavior target. Grounded patterns may be spoken naturally; unsupported motives, causality, superlatives, emotional certainty and specific facts remain prohibited. Internal retrieval/system language is not intended for ordinary responses. Conversation-time inference creates zero canonical memories, revisions, evidence, events, links, personality mutations or activity.

Persona preparation adds bounded timeline context only for relevant personal queries. Existing L13 personality remains style/context evidence, not factual biography. The L13 golden contract tests were adjusted to preserve their stable assertions while allowing this approved policy addition.

## 18. Authorization

Builder routes use `require_legacy`; owner-only operations verify `legacy_role == owner`. Persona reads use the existing persona authorization path. Every direct event route combines event ID and `legacy_id`. Evidence, memory and entity IDs are independently checked against the same Legacy. Visitor conversation remains mutation-free.

## 19. Cross-Legacy isolation

Application predicates and composite foreign keys protect event, memory, evidence and entity relationships. A guessed event ID under another Legacy returns 404 without disclosure. Cross-Legacy links are structurally rejected by the database.

## 20. Tests

Added `tests/test_timeline_l17.py` covering date precision, source-free canonical memory materialization, same-event support, conflicting years, Legacy isolation and event deletion preserving memory. Existing L14/L15 registry and migration contracts were updated for the intentional new read tool and migration head. Existing L13 persona contract assertions preserve the pre-existing stable policy checks while excluding the new approved reasoning language from the historical digest.

## 21. PostgreSQL acceptance

Final acceptance used the retained PostgreSQL **17.11** Windows runtime from `backups/l16-phase-c-runtime/pgsql`, on loopback `127.0.0.1:55442`, with fresh database **l17_test_phase_b_final** and a disposable SCRAM credential. Production `legarya` and all production data were untouched.

Migration upgraded through `0019_legacy_timeline`; all four L17 tables, indexes, checks, composite foreign keys and scoped uniqueness constraints were inspected. Downgrade to `0018_media_intelligence` removed only the four L17 tables and re-upgrade to 0019 passed.

The dedicated live suite `tests/test_timeline_postgresql_l17.py` ran **13 tests, 13 passed**. It covers schema inspection, concurrent duplicate event admission, retry idempotency, canonical correction/reconciliation, rollback, cross-Legacy memory/evidence/entity/event rejection, Legacy deletion semantics, source unavailability versus evidence attachment, and conflict review versus stale rebuild. The duplicate-admission race used two independent PostgreSQL connections and left exactly one event and one active link. Generation/lease fencing is not implemented in L17 and is recorded as not applicable.

The conflict race initially reproduced the defect: owner resolution committed `approved`/no conflict, then a stale rebuild recomputed the two active date alternatives and restored `review_state=conflict`. The narrow fix adds `conflict_resolved_at`; rebuild preserves a committed owner resolution, while newly attached incompatible canonical support clears the marker and reopens review. The event admission race fix adds scoped `admission_key` uniqueness and savepoint-safe duplicate recovery. The source race passed without weakening optional evidence semantics: removed evidence yields zero effective active source support while the event and canonical memory remain.

## 22. Exact regression results

- Focused L17 tests: **5 passed**.
- Live L17 PostgreSQL acceptance: **13 passed, 0 failed**.
- Focused canonical/L14/L15/L16 integration suites: **passed**.
- Disposable SQLite migration round trip: **passed** — upgrade to `0019`, downgrade to `0018`, re-upgrade to `0019`.
- Full backend regression from the backend repository root with the live L17 URL: **840 passed, 37 skipped**, no test failures. The skips are unrelated opt-in L14/L15/L16 PostgreSQL suites whose separate URLs were not configured.
- An initial full run from the workspace root encountered pytest's import-path mismatch with the separate legacy checkout; rerunning from `WaffleBerry-Backend/backend` passed.

## 23. Files changed

L17 implementation files:

- `backend/app/models/timeline.py`
- `backend/app/schemas/timeline.py`
- `backend/app/services/timeline.py`
- `backend/app/api/routes/timeline.py`
- `backend/alembic/versions/0019_legacy_timeline.py`
- `backend/tests/test_timeline_l17.py`

Integration files:

- `backend/app/main.py`, model registry, memory/L16 models, memory service, persona preparation/persona contract, conversation tools/schema, realtime tool descriptions.
- Related L14/L15 migration, registry and test-contract updates.

No frontend application code was changed.

## 24. Known limitations

- Live PostgreSQL migration and all required dedicated L17 race tests passed.
- Deterministic structuring intentionally covers a narrow explicit vocabulary; broader event types need reviewed expansion.
- The gap endpoint currently returns a bounded known-year summary rather than a culturally prescriptive life-stage model.
- Timeline evidence attachment is owner-only and does not yet expose raw source content.
- No async timeline worker was added; production load should determine whether bounded synchronous reconciliation remains sufficient.
- No polished UI, mobile chronology, visitor timeline page or final review experience was built.

## 25. Exact Phase C frontend/API contract

Phase C may consume:

- `GET /api/v1/timeline?legacy_id=&start=&end=&event_type=&limit=` — approved/conflict events sorted chronologically with precision, approximation, conflict, support counts and IDs.
- `GET /api/v1/timeline/{event_id}?legacy_id=` — one Legacy-scoped event detail.
- `GET /api/v1/timeline/gaps?legacy_id=` — bounded gap/known-year summary.
- Owner-only `POST /api/v1/timeline/events?legacy_id=` — human event creation; unsupported no-memory events are `needs_review`.
- Owner-only `PATCH /api/v1/timeline/{event_id}?legacy_id=` — display/chronology changes.
- Owner-only `DELETE /api/v1/timeline/{event_id}?legacy_id=` — soft delete only.
- Owner-only `POST /api/v1/timeline/{event_id}/review?legacy_id=` — approve, resolve or archive.
- Owner-only `POST /api/v1/timeline/{event_id}/evidence?legacy_id=` — attach an existing L16 evidence ID.

Phase C must render `date_label` and `date_precision` rather than internal interval bounds, show approximate/unknown/conflict states, display family/source support additively, use safe DOM rendering, preserve mobile accessibility, and never rely on hidden controls for authorization. Visitor UI must consume only read endpoints and cannot expose mutation affordances.

Disposable cluster `l17_test_phase_b_final` and its credential file were stopped and removed after validation. Production remains untouched and no L17 tag was created. Commit follows only after the scoped diff review.
