# L18 Phase A — Stories / Biography Architecture & Audit

Status: architecture only. No L18 implementation, migration, deployment, production change, or tag was made. Audit date: 2026-09-08.

## 1. Executive summary

L18 should turn preserved Legacy information into readable, evidence-aware narrative artifacts without creating a new factual authority. A Story is generated from approved canonical memories, L17 chronology, optional L16 provenance, and selected L13 style. Story text is never canonical memory and never becomes a MemoryRevision, SourceEvidence, LifeEvent, personality evidence, or a new preserved builder fact automatically.

The smallest useful v1 supports two deliberate narrative perspectives:

- `legacy_first_person`: a saved story written as “I moved to Pune in 1998.”
- `biography_third_person`: an archival biography written as “Pallavi moved to Pune in 1998.”

Both should ship because they represent two materially different user intents, not cosmetic tone variants. This does not alter direct Legacy conversation: the Legacy remains first-person. Rya, the builder companion, remains third-person about the Legacy subject.

The recommended v1 has four relational concepts: a Story identity, immutable Story versions, version-scoped chapters, and chapter provenance links. A version contains the complete accepted snapshot of its chapters. A new generation or human edit creates a new version; it never silently overwrites an owner-edited version. Version rows can also carry durable generation state and lease/idempotency metadata, avoiding an unnecessary fifth job table in v1.

Long biographies are generated in bounded chapters through a dedicated Story-generation worker or job path. Short requests may use the same service synchronously only when they fit strict limits. Existing memory, personality and media workers should not be overloaded with a different workload.

## 2. Existing architecture findings

The audit covered the checked-in backend and frontend, including:

- `backend/app/models/memory.py`, `services/memory.py`, memory routes and memory tests;
- `backend/app/models/timeline.py`, `services/timeline.py`, timeline routes and L17 reports/tests;
- `backend/app/models/media_intelligence.py`, media source/evidence services/routes, worker and L16 reports/tests;
- `backend/app/models/personality.py`, `services/personality_style.py`, `personality_invalidation.py` and personality routes/tests;
- `services/authorization.py`, access/collaboration/viewer models and routes;
- `services/legacy_persona.py`, `services/conversation_tools.py`, conversation routes and realtime services;
- `app/config.py`, `app/database.py`, `app/main.py`, Alembic history and the static builder frontend;
- `L16_PHASE_A_ARCHITECTURE.md`, L16 release reports, `L17_PHASE_A_ARCHITECTURE.md`, `L17_PHASE_B_REPORT.md` and `L17_PHASE_C_RELEASE_REPORT.md`.

Verified reusable boundaries:

- `Memory` is Legacy-scoped canonical factual storage. `MemoryRevision` records canonical edits and supersession lineage. `LivingMemoryService` owns normal durable memory changes.
- `LifeEvent` is a Legacy-scoped chronological organization layer over canonical memories. It carries date precision, approximation, conflict and lifecycle state; it is not a second memory store.
- L16 separates sources, artifacts, processing jobs, evidence, candidates and approved `MemorySourceLink` provenance. Evidence can be unavailable without invalidating a human memory.
- L13 personality is a derived, invalidatable style projection. `select_personality_style` is bounded and fail-open; it must influence manner, never factual content.
- `require_legacy` distinguishes owner/collaborator builder access, while `require_persona_legacy` protects Legacy conversation. Owner checks are already explicit.
- Legacy conversation instructions require first-person Legacy voice and read-only visitor behavior. Rya builder context is distinct and must remain third-person.
- Provider calls are abstracted by protocols and configured model settings. The current project has no L18 provider, story model, story migration, story API, or Story UI.
- Existing frontend is static HTML/CSS/JS with authenticated API clients, Legacy workspace selection, safe DOM rendering and stale-response suppression.

No current repository fact was treated as proof of an L18 feature that does not exist.

## 3. Reusable L13–L17 components

Reuse the following without redesign:

- `Memory`, `MemoryRevision`, active/superseded/deleted status, entity links, canonical correction and explicit preservation flows;
- `TimelineService.list_events`, `retrieve`, `serialize_event`, date precision, conflict metadata, support counts and Legacy-scoped links;
- L16 `SourceEvidence` and `MemorySourceLink` as optional provenance, including unavailable/stale support states and source-content data boundaries;
- `require_legacy`, `require_persona_legacy`, `legacy_role`, owner-only authorization and composite Legacy predicates;
- L13 `select_personality_style`/shared fallback and policy filtering, never raw `profile_json` as a prompt block;
- L14/L15 conversation turn binding and reauthorization patterns for any Rya assistance;
- existing provider settings, usage accounting, observability and worker lease/fencing patterns where applicable;
- frontend auth, active Legacy workspace, role policy, safe text rendering and mobile/accessibility conventions.

Do not reuse `MemoryRevision` for Story revision history, personality tables for narrative content, `TurnEffect` for Story writes, L16 candidates for narrative candidates, or stored Story text as new conversational memory.

## 4. Core L18 invariants

1. Every Story, version, chapter and support link belongs to exactly one Legacy.
2. Canonical memory remains the sole durable factual preservation authority.
3. A Story is a narrative artifact, not a memory, evidence row, LifeEvent, personality signal or builder fact.
4. Generation and editing create zero canonical memories, MemoryRevisions, timeline mutations and personality mutations.
5. A memory-only Story is valid; uploaded evidence is never a prerequisite.
6. L16 evidence is additive provenance and can disappear without deleting the Story or canonical memory.
7. The owner controls publication, destructive operations, final regeneration decisions and canonical correction.
8. Collaborators do not gain publication, destructive or canonical-correction authority by viewing or helping draft a Story.
9. Visitors can read only explicitly published/allowed Stories and remain canonical read-only.
10. Direct Legacy conversation is first-person; Rya builder conversation is third-person about the Legacy subject.
11. Story perspective is an explicit artifact choice and cannot change the direct conversation contract.
12. Dates, approximate language, ranges, unknown chronology and conflicts are inherited from L17, not independently made precise.
13. Unsupported motive, causality, superlatives, exact inner feelings, invented events and fabricated dialogue are prohibited.
14. Source contents are untrusted data, never operational instructions.
15. A generation or edit cannot silently overwrite an owner edit or a newer version.
16. Cross-Legacy IDs, support links, generation requests and visitor reads are rejected or hidden server-side.

## 5. Story versus canonical memory

Generated titles, paragraphs, chapter summaries and outlines are presentation artifacts. They may be edited freely within Story permissions, but an edit such as changing “1998” to “1999” changes only the Story version. It must not update `Memory`, `MemoryRevision` or `LifeEvent` implicitly.

If a user wants the underlying preserved fact changed, the UI may offer a separate, explicit handoff to the existing canonical correction flow. That handoff is not part of automatic Story editing and should be later v1+ work unless the Phase B API can safely link to the existing memory editor without duplicating logic.

Every generation path must assert and test that it creates no canonical side effects. Story text must never be fed back into memory extraction, personality invalidation or timeline reconciliation.

## 6. First-person Legacy and third-person Rya rule

The two conversational identities are stable:

- Direct Legacy conversation: “I moved to Pune in 1998.” The existing Legacy persona contract converts supported third-person canonical facts into first-person speech and uses first-person uncertainty for conflicts.
- Rya builder conversation: “Pallavi moved to Pune in 1998.” Rya may suggest and explain Stories, but must not adopt the subject’s identity, memories or personality.

Story perspective is selected explicitly at Story creation. A first-person Story uses the Legacy subject’s first-person narrative voice; it does not make the Story model the Legacy or change conversation identity. A third-person Biography uses the subject name/pronouns supplied by authorized server context. The provider must never choose the Legacy ID, perspective permissions or publication state.

## 7. Story modes and taxonomy

Recommended v1 modes: `legacy_first_person` and `biography_third_person`. No additional “voice,” “tone,” “audience,” or “reading level” mode matrix is needed initially; tone comes from bounded L13 style and owner editing.

Recommended v1 scopes:

- `full_biography`
- `childhood`
- `education`
- `career`
- `family`
- `relationship`
- `place`
- `event`
- `custom`

These are retrieval scopes, not assumptions about a universal life path. The system should only create chapters that have relevant preserved material. “No university,” “no marriage,” or “no children” must not be inferred from an empty scope. A custom scope has a bounded owner prompt, treated as a request for retrieval and organization rather than a source of facts.

## 8. Minimal Story domain model

### `stories`

One Legacy-scoped Story identity:

- `id`, `legacy_id`, `title`;
- `scope`, `narrative_perspective`;
- `visibility` (`draft`, `published`, `archived`) and `lifecycle_state` (`active`, `deleted`);
- `current_version_id`;
- `staleness_state` (`current`, `stale`, `needs_review`) and bounded `staleness_reason`;
- owner creator and timestamps.

The Story identity is not the text. Deletion is soft and removes visitor visibility; it does not cascade into memories, events, evidence, sources or personality.

### `story_versions`

An immutable narrative snapshot or an explicitly mutable private working draft before acceptance:

- `id`, `legacy_id`, `story_id`, integer `version_number`;
- `status` (`draft`, `generating`, `audit_failed`, `ready`, `accepted`, `superseded`, `failed`);
- generation request key/idempotency key, provider/model and prompt-policy version metadata;
- source-generation snapshots for memory/timeline/personality/evidence inputs;
- `human_edited`, creator, audit result summary, bounded failure code, timestamps and optional lease fields.

The content itself belongs to its chapter rows. An accepted/current pointer is updated transactionally. Provider prompts and private source text are not stored in logs or this metadata.

### `story_chapters`

Chapter rows belong to one version, so prior versions remain restorable without Google-Docs-level history:

- `id`, `legacy_id`, `story_version_id`, `title`, `ordinal`;
- bounded `narrative_text`;
- `generation_status`, `human_edited`, audit status and timestamps.

The unique key `(legacy_id, story_version_id, ordinal)` prevents duplicate ordering. Reordering creates a new version or an explicit owner draft operation; it must not mutate a published snapshot in place.

## 9. Provenance / support links

### `story_support_links`

One row represents one chapter’s factual support, with a required single support kind and exactly one target:

- `id`, `legacy_id`, `story_version_id`, `chapter_id`;
- `support_kind` (`memory`, `timeline_event`, `source_evidence`);
- nullable `memory_id`, `life_event_id`, `evidence_id`, with a check that exactly one is set;
- support status (`available`, `stale`, `unavailable`, `removed`), source-generation snapshot and timestamps.

PostgreSQL composite foreign keys must scope every target by `legacy_id`. The database should enforce version/chapter Legacy consistency with composite keys where practical; application authorization must independently validate the same scope. A source-evidence link does not imply that a source is required or more truthful than a family memory.

The user-facing “What is this chapter based on?” view can show “family memory,” “timeline event,” and optional “source support.” It should show a provenance-unavailable state when evidence is removed, without exposing raw private source contents or internal model mechanics.

## 10. Narrative generation pipeline

Bounded pipeline:

`owner request → authorize Legacy/scope → retrieve L17 events → retrieve linked active canonical memories → retrieve optional available L16 provenance → select L13 style → build bounded outline → generate one chapter → deterministic/structured grounding audit → rewrite/remove unsupported claims → save draft version + support links → owner review → optional publish`

For a full biography, the pipeline repeats per bounded chapter. It must not send the complete Legacy history or all source text in one giant provider call. L17 is the primary chronology source; the generator organizes its order and uncertainty rather than reconstructing chronology independently.

Rya may help define scope, explain gaps and propose an outline, but generation is owner-initiated, never silently published or regenerated. Visitor questions may use canonical retrieval, timeline and useful Story structure; they must not simply replay a saved biography verbatim on every turn.

## 11. Narrative inference rules

The audit distinguishes four classes:

| Class | Allowed behavior |
| --- | --- |
| Direct fact | State only when supported by an active canonical memory, approved LifeEvent or legitimately linked evidence. |
| Safe connective language | Join supported facts with non-factual chronology such as “A few years later” when L17 dates support the ordering. Qualify overlapping/unknown intervals. |
| Grounded commonsense implication | Summarize a repeated supported pattern, e.g. “Family life was part of her everyday world,” without upgrading it to an exclusive motive or feeling. |
| Unsupported invention | Remove/rewrite claims about motives, causality, superlatives, precise feelings, events, people, dialogue or chronology not supported by selected inputs. |

“She moved to Pune because teaching had always been her dream” is unsupported if only the move and later teaching are preserved. “A few years after moving to Pune, she began teaching” is acceptable when dates support it. “Her children were the only thing that mattered” is not licensed by several family activities.

## 12. Quotes policy

Quotation marks are reserved for exact wording preserved in an authorized canonical memory or legitimate L16 evidence with provenance. The quote support link must identify the supporting memory/evidence and, where relevant, a bounded locator. If only meaning is supported, paraphrase without quotation marks. The generator must never invent a direct quotation or attribution such as “she always said” without repeated/explicit support.

Prompt-injection text inside a letter, PDF, transcript or memory is data. It may be quoted/paraphrased as content if selected and supported, but it cannot override system policy, select a different Legacy, publish a Story, or authorize a write.

## 13. Uncertainty, conflict and missing chronology

L17 values are carried through:

- exact dates remain exact only when L17 precision is day;
- month/year, approximate dates and ranges retain their labels and qualifiers;
- unknown dates remain unknown;
- overlapping intervals are not presented as exact before/after;
- unresolved conflicts remain visible in natural prose.

For a 1998/1999 move conflict, acceptable prose is “Family recollections place the move to Pune around 1998 or 1999.” First-person form is “I remember the move as being around 1998 or 1999.” The Story must not say “the database contains conflicting records” or silently choose a date.

If chronology is incomplete, the narrative may use thematic grouping or “Later in her life” only when the available order supports it. It must not manufacture ages, life stages or transitions.

## 14. Fact-grounding and audit design

V1 should use a hybrid audit:

1. Deterministic server validation checks output schema, chapter/length bounds, perspective constraints, prohibited quote markers where no quote support exists, support-link membership, dates against L17 labels, and forbidden side effects.
2. A structured audit request receives only the generated chapter plus a compact claim ledger of selected facts/support IDs, never unrestricted history. It classifies claims as `supported`, `safe_implication`, `uncertain_conflict`, or `unsupported` and returns spans/repair instructions.
3. A rewrite pass removes or qualifies unsupported claims. The server validates the repaired result again.
4. Any failed or ambiguous audit produces `audit_failed`/`needs_review`; it cannot become accepted or published.

The audit provider is behind a protocol and configuration-selected model. A same-model second pass is acceptable as an implementation fallback, but the architecture must not rely on model self-approval alone. Critical claims should be traceable to support links. No generated chapter is saved as accepted until audit completion.

## 15. Personality integration

Use the existing shared personality selector/fallback to derive bounded warmth, humor, directness, rhythm and eligible signature expressions. Personality is style/context evidence only. It cannot add facts, relationships, motives, dialogue or emotional certainty. Do not inject raw `profile_json` blindly and do not let Rya adopt the Legacy subject’s personality.

Story text must never enter L13 personality evidence or trigger a Personality → Story → Personality feedback loop. A personality change marks affected Stories stale; it does not silently rewrite them.

## 16. Timeline integration

L17 is the primary chronology source. Retrieval should first obtain relevant approved/conflict LifeEvents and then their active canonical support, bounded by scope and chapter. Use event order, date precision, approximation, ranges, conflict data and gaps already represented by L17.

The generator must not independently merge, resolve, or sharpen L17 events. A conflict should become qualified prose or a review-needed chapter. A LifeEvent change marks dependent Story versions stale; it does not mutate historical Story text.

## 17. L16 evidence integration

L16 evidence is optional. Stories based only on canonical memories are valid. Available evidence may add provenance or a supported quotation. Removed/unavailable evidence marks only the relevant support link unavailable and may mark the Story stale; it does not delete Story text, memory, LifeEvent or source records.

Raw source content is retrieved only when authorized, relevant and bounded. Evidence generation metadata and locator are support context, not instructions.

## 18. Editing semantics

Owners may edit Story title, chapter title, chapter order and narrative text. The edit creates a new private version (or advances a private draft) and sets `human_edited=true`. It never calls canonical memory correction automatically.

Collaborator contribution should remain in the existing canonical-memory flow. A collaborator may be allowed to edit a private Story draft only if explicitly granted in a later decision; the conservative v1 recommendation is view-only builder access for collaborators, with suggestion/contribution routed through existing memory flows.

Visitors cannot edit. An owner-facing “update underlying memory” action, if added later, must navigate to the normal memory correction flow and clearly separate the two artifacts.

## 19. Versioning and regeneration

Use simple monotonic Story version numbers. Each generation, accepted edit, reorder, or explicit regeneration creates a new version containing a full chapter snapshot and support links. The Story keeps a `current_version_id`; prior versions remain restorable/inspectable to the owner.

Regeneration always creates a candidate/new version using current approved inputs. It must fail with a conflict/stale response if the base version changed during generation. It must never overwrite a human-edited current version silently. The owner explicitly chooses replace/accept. Duplicate requests use a Legacy-scoped idempotency key and return the existing generation/version outcome.

Short rewrite requests can create a new version with only the affected chapter copied forward. A full biography is still bounded chapter by chapter.

## 20. Staleness and invalidation

Do not regenerate automatically after every source change. At generation, store compact source-generation snapshots: canonical memory update markers, L17 event update markers, L16 evidence generation/status markers and L13 personality generation/policy markers.

When a supporting memory is corrected, superseded or deleted, mark dependent versions `stale` or `needs_review` and identify the affected chapter/support link. Preserve the historical text and support snapshot; do not silently rewrite it. The owner can review and regenerate.

When a LifeEvent changes, apply the same stale marking. When evidence disappears, mark its support unavailable and retain readable text where canonical support remains. When personality changes, mark style-dependent current drafts stale only if the owner chooses style regeneration; factual support remains unchanged.

## 21. Publication, visitor access and visibility

V1 visibility should be minimal: `draft`/`owner_only`, `published`, `archived`. Generated output starts as a private draft. Publication/unpublication is owner-only and transactional with the current version pointer. Archived/deleted Stories are not visitor-readable.

Visitors may read only published Stories authorized for that Legacy. They cannot see owner drafts, audit details, private support text or unpublished versions. Visitor reads have zero canonical, personality, timeline, Story-generation or activity side effects. Visitor conversation can use relevant published Story structure as supplementary context, never as a second conversation brain or automatic verbatim answer.

## 22. Collaborator permissions

Recommended conservative v1:

- owner: create, generate, edit, reorder, regenerate, inspect provenance, accept versions, publish/unpublish, archive/delete;
- collaborator: view allowed builder Stories and provenance labels; contribute facts only through existing canonical-memory flows;
- visitor: read published Stories only; no builder access or mutation.

If product research requires collaborator drafting, grant draft-only edit with optimistic version checks and no publication, deletion, canonical correction or final replacement. Do not infer this permission from ordinary collaborator access.

## 23. Cross-Legacy isolation

Every query and mutation includes `legacy_id` plus the resource ID. PostgreSQL composite foreign keys should cover Story/version/chapter/support relationships and target `(legacy_id, id)` keys on memories, LifeEvents and SourceEvidence. Unique keys include `legacy_id`.

The API must not disclose whether a guessed Story/version/chapter/support ID exists under another Legacy. Regeneration, publication, deletion and provenance reads must reauthorize after any awaited provider/job work. Provider output cannot choose or widen the Legacy scope.

## 24. Deletion semantics

- Story deletion: soft-delete/archive Story and hide it; retain historical Story/version rows as policy permits. Never delete canonical memories, LifeEvents, L16 sources/evidence or personality.
- Chapter deletion: create a new owner version without the chapter; retain prior versions. Do not delete support targets.
- Memory correction/supersession/deletion: mark dependent support stale/unavailable and Story stale/needs review; preserve historical text.
- LifeEvent changes: same stale/needs-review behavior.
- Source/evidence deletion: mark provenance unavailable; preserve Story and canonical support.
- Personality changes: stale style snapshot as appropriate; do not treat Story text as personality evidence.

## 25. Provider/model architecture

Introduce provider protocols for outline generation, chapter generation and grounding audit. The domain receives a structured request with server-selected Legacy context, perspective, scope, bounded claim ledger and style block. The provider returns strict structured output; it does not receive permission or persistence tools.

Model IDs come from configuration (for example, an L18 generation model and audit model), not domain constants. Prompt policy and schema versions are stored as short metadata. Usage accounting must record model/cost metadata without full prompts, biography text or source contents.

## 26. Background job strategy

Recommendation: hybrid execution. A short single-chapter rewrite may run synchronously under strict request/time/token limits. Full biography, multi-chapter generation, retryable audit and provider latency should use a durable Story version status/lease path and a dedicated Story worker/job service.

Do not overload the personality or media worker. Reuse their lease, retry, idempotency, observability and stale-result patterns conceptually, but give Story generation its own bounded queue and failure codes. A provider failure leaves the draft/version non-published and retryable; it never creates canonical effects.

## 27. Proposed minimal API surface

All endpoints are Legacy-scoped and server-authorized:

- `POST /api/v1/stories?legacy_id=` — owner creates a private Story shell with scope/perspective/title.
- `GET /api/v1/stories?legacy_id=` — owner/collaborator list; visitor list only published stories through the persona-appropriate read path.
- `GET /api/v1/stories/{story_id}?legacy_id=` — authorized Story/version/chapter read.
- `POST /api/v1/stories/{story_id}/generate?legacy_id=` — owner creates an idempotent generation/version request.
- `POST /api/v1/stories/{story_id}/chapters/{chapter_id}/regenerate?legacy_id=` — owner creates a new bounded version for one chapter.
- `PATCH /api/v1/stories/{story_id}/versions/{version_id}/chapters/{chapter_id}?legacy_id=` — owner edits narrative text/title only.
- `POST /api/v1/stories/{story_id}/versions/{version_id}/accept?legacy_id=` — owner accepts the audited version/current pointer.
- `POST /api/v1/stories/{story_id}/publish?legacy_id=` and `/unpublish` — owner-only visibility changes.
- `GET /api/v1/stories/{story_id}/versions/{version_id}/provenance?legacy_id=` — authorized bounded support display.
- `POST /api/v1/stories/{story_id}/archive?legacy_id=` — owner archive; hard deletion is not v1.

An outline preview can be represented as a private generating/draft version rather than a separate domain table. A separate “suggest themes” endpoint is not required initially.

## 28. Frontend plan for later Phase C

Add a builder Stories destination with:

- Story library cards and clear draft/published/stale labels;
- create flow for title, scope and the two perspectives;
- bounded outline preview before generation;
- chapter reader/editor with save-as-version semantics;
- “What this chapter is based on?” provenance drawer;
- conflict/approximation/unknown date wording inherited from L17;
- explicit regenerate/review actions and no silent overwrite;
- owner publication controls; collaborator read-only display unless separately approved;
- visitor published-story reading only;
- mobile reading/editing, keyboard focus and safe DOM rendering.

PDF/book export is not an L18 v1 requirement. Narrative quality, grounding, editing and provenance come first.

## 29. Test matrix for Phase B/C

### Narrative and grounding

- direct fact is preserved correctly;
- chronological connective language is allowed only when supported by L17 order;
- unsupported causality, motive, emotional intensity, superlatives and invented events are rejected or rewritten;
- fabricated quotes are prohibited;
- genuine quote with legitimate memory/evidence provenance is allowed;
- cross-memory unsupported combinations are rejected;
- audit failure blocks accepting/publishing a malformed chapter;
- output schema, chapter count/length and perspective constraints are server-validated.

### Identity and modes

- direct Legacy conversation remains first-person;
- Rya builder conversation remains third-person about the subject;
- Rya does not adopt Legacy identity or personality;
- first-person Story mode and third-person Biography mode render correctly;
- mode choice does not change direct conversation behavior.

### Memory, timeline, evidence and uncertainty

- memory-only Story is valid and no source is requested as proof;
- optional L16 source appears as provenance only;
- deleted/unavailable source becomes unavailable without deleting Story or memory;
- exact, approximate, month/year, range and unknown dates retain precision;
- unresolved conflict is inherited naturally without fake precision or silent selection;
- L17 gaps and overlapping intervals are not invented into chronology.

### Side effects and editing

- Story generation creates zero canonical memories and zero MemoryRevision rows;
- generation creates zero timeline mutations and zero personality mutations;
- Story edit changes no canonical memory or LifeEvent;
- regeneration does not silently overwrite owner edits;
- canonical correction/supersession/deletion marks dependent Story/chapter stale;
- new evidence can refresh provenance without forced global regeneration;
- visitor reads create zero canonical effects.

### Authorization and privacy

- owner can create/generate/edit/accept/publish/archive/delete;
- collaborator cannot publish, delete or perform canonical correction; draft editing, if enabled, is explicitly tested;
- visitor reads only published Stories;
- private drafts are never returned through visitor routes;
- guessed Story/version/chapter/support IDs and cross-Legacy IDs fail without disclosure;
- source contents and private memory text are not logged unnecessarily;
- malicious source instructions do not alter generation policy or scope.

### PostgreSQL-required concurrency/idempotency

- simultaneous regenerate versus owner edit preserves the owner edit and refuses stale replacement;
- publish versus delete has one coherent final visibility state;
- Story generation versus underlying memory correction results in stale/review-needed handling, not stale accepted publication;
- duplicate generation request keys create one effective version/job;
- cross-Legacy support constraints reject foreign memory, LifeEvent and evidence links;
- concurrent chapter reorder/version acceptance cannot create duplicate current pointers or invalid order;
- rollback leaves no partial Story, chapter or support-link graph.

## 30. Migration/schema plan

Phase A creates no migration. Phase B will likely require additive migration `0020_legacy_stories`, subject to implementation review. The migration should create the four concepts above, composite Legacy-scoped keys/FKs, indexes on `(legacy_id, visibility, updated_at)`, `(legacy_id, story_id, version_number)`, generation status/lease fields and support lookups.

Use check constraints for perspective, scope, visibility, lifecycle, version status, support kind/state and nonblank/bounded titles/text. Use unique `(legacy_id, story_id, version_number)` and a Legacy-scoped idempotency key for generation requests. `current_version_id` must reference a version from the same Story/Legacy, enforced by composite constraints or a guarded transactional update.

Do not create a separate outline, quote, claim, job, audience, export or full audit table until Phase B proves that the four-table design cannot satisfy a concrete requirement. Structured audit details may remain bounded JSON on the version/chapter initially, with no private prompt storage.

## 31. Security and privacy

Canonical, evidence and Story scopes are server-authoritative. Provider outputs are untrusted. Prompt injection defenses apply to memory text, evidence text, locator metadata and custom owner prompts. The model cannot choose Legacy ID, change permissions, publish, delete, edit canonical memory or mutate timeline.

Do not log full biographies, chapter text, source text, provider prompts, credentials or broad private memory dumps. Log only structured lifecycle events such as generation started/completed/failed, chapter regeneration, audit failure, accept, publish/unpublish, archive/delete and bounded error codes. Usage accounting may retain model/token/cost metadata under existing policy.

Story content is highly personal. Visitor responses must use published authorization and the existing persona privacy boundary. Drafts, provenance details and owner-only controls must not leak through guessed IDs, cache keys, API errors or frontend hidden controls.

## 32. Risks and open questions

- Whether the current provider abstraction should gain a shared structured-output protocol or a dedicated Story provider module.
- Exact production model/configuration, audit model strategy and budget limits.
- Whether collaborator draft editing is needed in v1; recommendation remains view-only.
- Whether chapter-level provenance is sufficient or whether claim-level spans become necessary after quality testing.
- Whether version snapshots should retain deleted/unavailable support links indefinitely or apply a retention policy.
- Exact stale semantics when only personality style changes.
- How much published Story structure should supplement visitor conversation without causing verbatim repetition.
- Whether the existing worker supervision/deployment conventions can host a dedicated Story worker without operational expansion.
- Whether an owner may accept a chapter with unresolved conflict language, or must explicitly acknowledge the conflict.
- Accessibility and localization requirements for long-form reading/editing.

## 33. Recommended Phase B sequence

1. Convert this architecture into explicit invariants and provider-neutral schemas.
2. Implement additive migration `0020_legacy_stories` only after schema/FK review.
3. Implement Story/version/chapter/support services with zero-canonical-side-effect tests.
4. Implement bounded retrieval using L17 first, then active memories, optional L16 evidence and selected L13 style.
5. Implement structured generation plus deterministic and model-assisted grounding audit.
6. Add owner-only API and idempotent version acceptance/publish semantics.
7. Add PostgreSQL concurrency tests before any production consideration.
8. Add builder and visitor UI only after API/grounding acceptance; add mobile/provenance/review behavior.
9. Run focused/full regression and a separate production release review.

## 34. Audit conclusion

L18 should ship both first-person Legacy Story and third-person Biography modes in v1, with a deliberately small scope taxonomy and versioned chapters. The factual boundary is canonical memory; L17 supplies chronology; L16 supplies optional provenance; L13 supplies style only. Narrative implication is permitted when it safely connects repeated or dated supported material, while invented motives, causality, feelings, dialogue and precision are not.

The minimum robust design preserves owner edits, makes support inspectable, marks stories stale rather than silently rewriting them, keeps visitors read-only, and prevents all cross-Legacy or provider-authority failures. Phase A is complete when these decisions are accepted; implementation, migration and release belong to later phases.

Exact report path: `backend/docs/L18_PHASE_A_ARCHITECTURE.md`.

Confirmation: no L18 implementation, migration, deployment, production database change, production data change, or tag was made during this Phase A audit.
