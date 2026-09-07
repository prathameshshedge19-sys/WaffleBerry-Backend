# L18 Phase B — Story Engine & Backend Report

Status: implementation complete and accepted in an isolated clean L17+L18 worktree; active-worktree commit is now permitted. Report date: 2026-09-08.

## 1. Scope implemented

Implemented the backend Story/Biography engine from `L18_PHASE_A_ARCHITECTURE.md`:

- additive migration `0020_legacy_stories`;
- Legacy-scoped Story identity, visibility and staleness;
- immutable version snapshots with idempotent generation request keys;
- version-scoped chapters with bounded narrative text;
- Legacy-scoped memory, LifeEvent and optional SourceEvidence support links;
- bounded deterministic outline/chapter generation and provider abstraction;
- server-side perspective, date, quote, causality and bounded-output audit floor;
- first-person Legacy Story and third-person Biography modes;
- owner Story creation, generation, chapter editing, publication and archive APIs;
- support-change staleness marking without rewriting saved Story text;
- PostgreSQL constraints and concurrency tests;
- bounded live-provider smoke using synthetic non-private facts.

No polished Stories dashboard, reader, editor UX, mobile Stories UI, visitor Story presentation, deployment or production change was made.

## 2. Phase A decisions followed

Story text is a narrative artifact and is never canonical memory. Generation and Story editing do not call canonical memory persistence, `MemoryRevision`, timeline mutation or personality mutation. Canonical memories remain authoritative; L17 supplies chronology; L16 evidence is optional provenance; L13 personality supplies style only.

Direct Legacy conversation remains first-person. Rya’s existing builder contract remains third-person about the Legacy subject. Story perspective is explicit and stable per Story.

Memory-only Stories are valid. No source-required or evidence-required gate was added. Deleted/unavailable evidence is optional provenance state and does not delete Story, memory or LifeEvent.

## 3. Migration/schema

Added `alembic/versions/0020_legacy_stories.py` after `0019_legacy_timeline`. It creates only:

- `stories`
- `story_versions`
- `story_chapters`
- `story_support_links`

The migration is additive and does not modify migrations 0017–0019. Checks bound scope, perspective, visibility, lifecycle, staleness, version status, chapter text and support kind/state. Composite foreign keys prevent cross-Legacy Story/version/chapter/memory/LifeEvent/evidence links. Unique keys protect scoped IDs, version numbers, generation request idempotency and chapter order.

Disposable SQLite and PostgreSQL migration round trips both passed: upgrade through 0020, downgrade to 0019, and re-upgrade to 0020.

## 4. Story/version/chapter model

`Story` stores title, scope, narrative perspective, draft/published/archived visibility, soft lifecycle, current version pointer and staleness state. `StoryVersion` stores a complete version snapshot, generation request key, bounded input IDs, provider/policy metadata, audit state and human-edited state. `StoryChapter` stores bounded title/text, order, generation state, audit summary and human-edited state.

Regeneration creates a new version and never silently overwrites an owner edit. Row locking serializes version allocation/current-pointer decisions. A stale generated result is marked superseded when a newer version already won. Failed audit output is recorded as a failed/audit-failed version and cannot be accepted or published.

## 5. Provenance

`story_support_links` attaches a chapter to exactly one Legacy-scoped target kind: canonical Memory, L17 LifeEvent or optional L16 SourceEvidence. Approved available MemorySourceLink evidence is inherited as optional source provenance where available. Support state can become stale/unavailable/removed. No file is required for Story creation or generation.

## 6. Generation pipeline

The bounded pipeline is:

`authorize owner → retrieve bounded L17 events → retrieve bounded active memories → inherit optional evidence → select shared L13 style → generate structured outline → generate chapters → deterministic audit → save private ready version + support links`.

The default test provider is deterministic. The configured provider uses strict structured output through `OpenAIStoryProvider`; model ID comes from existing settings and is not hard-coded into domain logic. Inputs are bounded to 24 events, 32 memories, 8 chapters and 8 supports per chapter. Chapter text is limited to 12,000 characters and the provider path does not receive write tools or authority over IDs/permissions/publication.

Long-running full-biography worker execution remains a Phase C/operations follow-up; Phase B provides durable version status, attempt bounds and request idempotency for the bounded synchronous engine.

## 7. Outline and chapter generation

Outline IDs are server-filtered against the selected Legacy’s retrieved facts. Universal life-stage chapters are not created without relevant preserved material. Generated chapters use natural prose rather than database-record narration. A memory-only Story is valid.

## 8. Narrative inference and causality

The audit distinguishes direct facts, safe chronology/connective language, grounded pattern summaries and unsupported invention. It rejects unsupported dates, causality, absolute claims and perspective mismatch. “A few years later” is allowed only where selected chronology supports it; “because teaching had always been her dream” is rejected without causal support.

## 9. Quote policy

Quotation marks are accepted only when the quoted wording appears in selected supported source text. Meaning without exact wording must be paraphrased. Fabricated quotations such as “She always said, ‘Never give up’” are rejected; the audit does not merely lower confidence.

## 10. Uncertainty and conflict

The engine consumes L17 dates, labels and conflict metadata and does not independently resolve chronology. Exact, approximate, range and unknown semantics remain bounded. Conflicting 1998/1999 alternatives remain available to the provider/audit and are not silently selected or converted to fake precision.

## 11. Fact-grounding audit

Every generated chapter is checked before a ready version is persisted. The deterministic server floor checks output bounds, supported date tokens, supported quotation text, unsupported causal/absolute language and perspective. Failed audit output creates an `audit_failed` version without chapters and returns a sanitized API error. A structured provider output is validated with Pydantic before persistence.

## 12. Provider and prompt-injection handling

The provider sees facts as data and cannot choose Legacy IDs, support IDs, permissions, publication state or canonical writes. Source/memory prompt-injection text is not operational instruction. No prompts, credentials, full source contents or full Story text are logged by the Story engine.

## 13. Editing, regeneration and staleness

Owner chapter editing creates a new ready human-edited version and changes only Story text. It does not modify Memory, MemoryRevision, LifeEvent, SourceEvidence or Personality. Canonical memory, LifeEvent and SourceEvidence changes mark dependent Stories stale through a session hook; saved narrative text is not silently rewritten. Source removal marks support unavailable while preserving Story/memory validity.

## 14. Publication and permissions

Generated Stories start as drafts. Owner-only publication/unpublication and archive routes use Legacy authorization and row-locked active-state checks. Collaborators can view builder Stories through the current implementation; they cannot create, edit, generate, publish or archive through owner-only routes. Visitor Story presentation is deferred to Phase C and no visitor mutation route was added.

## 15. APIs

Implemented routes:

- `POST /api/v1/stories?legacy_id=`
- `GET /api/v1/stories?legacy_id=`
- `GET /api/v1/stories/{story_id}?legacy_id=`
- `POST /api/v1/stories/{story_id}/generate?legacy_id=`
- `PATCH /api/v1/stories/{story_id}/chapters/{chapter_id}?legacy_id=`
- `POST /api/v1/stories/{story_id}/publish?legacy_id=`
- `POST /api/v1/stories/{story_id}/archive?legacy_id=`
- `GET /api/v1/stories/{story_id}/provenance?legacy_id=`

Outline generation is the first internal step of the idempotent generation endpoint, as recommended by Phase A; no unnecessary standalone outline table was added.

## 16. Tests

Added:

- `tests/test_stories_l18.py`: **5 passed** focused domain/API tests covering zero canonical side effects, both perspectives, edit boundary, audit rejection and authorization/scoping;
- `tests/test_stories_postgresql_l18.py`: **6 passed** live PostgreSQL tests covering migration shape/checks, cross-Legacy support FKs, duplicate generation idempotency, generation/edit fencing, staleness and publish/archive race coherence.

Updated only the migration-head expectations in existing L13/L15 migration contract tests from 0019 to 0020 and excluded the new L18 tables from the historical snapshot comparison. Existing unrelated realtime edits remain unstaged.

## 17. PostgreSQL acceptance

Used fresh disposable loopback PostgreSQL 17.11 database `l18_test_phase_b` on `127.0.0.1:55442`. The database was not production `legarya`. PostgreSQL migration upgrade/downgrade/re-upgrade passed. The live L18 suite passed **6/6**.

Concurrency coverage proves:

- duplicate generation request produces one effective version;
- generation versus owner edit leaves the owner edit current;
- canonical correction marks dependent Story stale without rewriting historical text;
- publish versus archive cannot leave `published + deleted`;
- cross-Legacy support is rejected structurally;
- failed transactions leave no partial Story chapter/support graph.

The disposable PostgreSQL runtime/database were local test resources only. Cleanup is pending final shutdown after this report review; no production connection was used.

## 18. Live-provider smoke

The configured provider was available locally. Bounded smoke calls used only synthetic facts and no production data. Results:

- third-person Biography generation: passed;
- first-person Legacy Story generation: passed;
- audit acceptance of grounded output: passed;
- unsupported causality detection: passed;
- fabricated quote detection: passed;
- 1998/1999 conflict wording preservation: passed;
- prompt-injection fixture did not produce the injected “President of India” claim: passed.

No live-provider smoke call wrote the database; no canonical memory, MemoryRevision, LifeEvent, personality or activity rows were created.

## 19. Regression result

The active worktree contained these unrelated pre-existing edits and they were not modified: `backend/app/services/realtime_provider.py`, `backend/tests/test_realtime_l15.py`, and `backend/docs/L16_PHASE_A_ARCHITECTURE.md`. The active unfiltered run collected **888 tests** and reported **849 passed, 37 skipped, 1 failed**. The sole failure was `tests/test_realtime_l15.py::test_logout_ends_call_and_denies_old_token`.

To determine causality without altering the active worktree, a detached temporary worktree was created from exact accepted L17 commit `693e70b0d486c52455782edc2f56464869d6f277`. Only the intended L18 files, L18 reports, and migration-head contract updates were copied into it; the three unrelated files were excluded. Its genuinely unfiltered run collected **888 tests**, with **832 passed, 56 skipped, 0 failed, 0 errors**. The formerly failing logout test executed from the committed L17 baseline and passed. This proves the active-worktree failure is caused by the unrelated realtime edit rather than L18.

The temporary worktree was removed after acceptance. The active worktree remains dirty only with the listed unrelated edits plus the intended L18 files. The L18 Phase B commit gate is satisfied for the isolated L18-only patch; no deployment or L18 tag was made.

## 20. Files changed

L18 implementation:

- `backend/app/models/story.py`
- `backend/app/schemas/story.py`
- `backend/app/services/stories.py`
- `backend/app/api/routes/stories.py`
- `backend/app/main.py`
- `backend/app/models/__init__.py`
- `backend/alembic/versions/0020_legacy_stories.py`
- `backend/tests/test_stories_l18.py`
- `backend/tests/test_stories_postgresql_l18.py`
- migration-head contract updates in `tests/test_personality_migration_l13.py` and `tests/test_realtime_migration_l15.py`

Report: `backend/docs/L18_PHASE_B_REPORT.md`.

Pre-existing and excluded:

- `backend/app/services/realtime_provider.py`
- `backend/tests/test_realtime_l15.py`
- `backend/docs/L16_PHASE_A_ARCHITECTURE.md`

## 21. Known limitations / Phase C contract

- No polished Stories dashboard, Story cards, create modal, chapter reader, editor UX, provenance panel, mobile UI or visitor Story presentation was implemented.
- No production deployment, production migration, production data change or L18 tag was made.
- Full-biography durable worker supervision remains for later operational work; bounded synchronous generation is implemented.
- Visitor published-Story API/presentation remains deferred until authorization and privacy UX are finalized.
- The deterministic audit is a required safety floor; Phase C may improve claim-level audit UX only without weakening it.
- Collaborator Story drafting remains conservative/view-oriented.

Exact Phase C items still unimplemented: polished builder Stories library, create flow, outline preview UX, chapter reader/editor, provenance UI, mobile Stories UI, visitor published Story presentation, and production release.
