# Memory Engine Final Validation

**Milestone:** Phase 6.5.8  
**Scope:** Phases 6.5.1–6.5.7  
**Assessment:** Ready for Phase 6.6 development; backend runtime verification
is still required before production release.

## Architecture and pipeline audit

The implemented flow is:

```text
Authenticated owner
  -> persisted Legacy
  -> persisted Story Session
  -> ordered visible Story Messages
  -> completion-bound extraction run
  -> MemoryExtractionService
  -> MemoryValidationService
  -> candidate-atomic MemoryStoragePipeline
  -> owner-scoped Memory Review
  -> explicit approved or rejected state
```

The audit verified:

- Legacy, Story Session, extraction-run, Memory, contradiction, enrichment,
  and provenance operations are scoped to an authenticated owner and Legacy;
- browser correlation IDs map local state but are never ownership evidence;
- request sessions and ORM objects are not passed to background work;
- cached AI, extraction, validation, and pipeline objects are stateless;
- the persisted streaming route commits the user message before provider work,
  closes its read transaction, and stores Berry only after a complete response;
- extraction is triggered only by explicit completion, never per token;
- extraction runs are idempotent by session, persisted message boundary, and
  trigger type;
- the pipeline uses that fixed boundary and retains per-candidate atomicity;
- all extraction-created memories begin as `candidate`;
- only the review service transitions a candidate to `approved` or `rejected`;
- Companion Chat imports no Memory service and receives no candidate or
  approved-memory context;
- review responses omit fingerprints, source locators, raw provider data,
  prompts, and full conversations.

No alternate automatic approval, merge, supersession, or Companion retrieval
path was found. The older stateless `/stories/stream` endpoint remains for
compatibility, but the current frontend uses the persisted owner-scoped route.

## End-to-end journey audit

1. A temporary frontend Legacy explicitly synchronizes through authenticated
   `POST /legacies`.
2. The backend correlation constraint prevents duplicate Legacies for the same
   owner/client identity.
3. The Dashboard hydrates persisted owned Legacies after local session loss.
4. Entering a chapter creates or resumes a persisted Story Session.
5. User and visible Berry messages receive deterministic server sequences.
6. User submission retries use namespaced client correlation IDs.
7. Finish Story commits the completed session and extraction run before
   scheduling.
8. The background task opens and closes its own database session.
9. Extraction, validation, provenance verification, and candidate persistence
   reuse the completed Memory Engine layers.
10. Candidates appear through the existing review API using `backendLegacyId`.
11. Edits snapshot the prior editable form in `MemoryRevision` and regenerate
    the fingerprint.
12. Approval is explicit, state-checked, owner-scoped, and preserves
    provenance.
13. Approved records remain stored for Phase 6.6 but are not yet injected into
    Companion Chat.

The final audit fixed the one continuity gap in this journey: persisted
Legacies can now be hydrated into a new frontend session instead of becoming
unreachable after session storage is cleared.

## Correctness and transaction audit

- Legacy creation is atomic and concurrency-safe through owner/correlation
  uniqueness plus conflict recovery.
- Story Message append locks the session where supported, uses unique sequence
  and client-message constraints, and has a bounded collision retry.
- User and assistant message transactions are independent from provider
  streaming.
- Completion commits before background scheduling.
- A failed background run never rolls back the Story Session.
- Each Memory, its provenance, participants, tags, and relationship metadata
  share one candidate transaction/savepoint.
- Partial candidate-persistence errors now mark the extraction run failed,
  while already committed candidates remain intact and exact retry duplicates
  are prevented.
- Review edits, revisions, tags, participants, and new fingerprints commit
  together.
- Review transitions require candidate state and a fresh `updated_at`.

SQLite does not provide the same row-lock behavior as production relational
databases. Unique constraints and bounded retries cover the principal
idempotency races, but high-contention multi-process behavior should be
retested against the intended production database.

## Security and privacy audit

Verified controls:

- Bearer authentication on every Memory Engine public route;
- generic not-found behavior for inaccessible Legacy-owned resources;
- same-Legacy checks for Story Sessions, Messages, extraction runs, related
  Memories, contradiction groups, and provenance;
- model-generated IDs are never trusted;
- assistant messages are rejected as factual provenance;
- direct CRUD persistence now also requires a user-authored database source
  and a verbatim excerpt, closing a defense-in-depth gap outside the normal
  pipeline;
- response schemas do not expose normalized fingerprints or internal source
  locators;
- provider exceptions and raw AI responses are not returned by Memory APIs;
- persisted Story stream failure logging now records a safe category and IDs,
  not a raw provider traceback;
- frontend review/story data uses `textContent` and no unsafe HTML;
- the only reviewed `innerHTML` paths are static dialog markup and
  DOMPurify-sanitized Companion markdown, neither part of Memory provenance
  rendering;
- tokens, prompts, transcripts, excerpts, and API keys are not logged by new
  Memory Engine code.

No review bypass or candidate-to-Companion path was found.

## Idempotency and concurrency audit

The following independent keys are present:

- Legacy: owner + client correlation ID;
- Story Message: Story Session + namespaced client message ID;
- Story ordering: Story Session + sequence;
- extraction run: Story Session + message boundary + trigger;
- Memory: Legacy + normalized fingerprint;
- enrichment: source Memory + target Memory + link type;
- revision: Memory + revision number.

User and assistant correlations are now explicitly namespaced, preventing a
malicious or accidental client value from colliding with a stored assistant
response. Duplicate completion tasks may both be scheduled, but only the task
that atomically sees `pending` proceeds.

Review concurrency uses row locking where supported plus expected timestamp
and candidate-state checks. A truly simultaneous SQLite write remains limited
by SQLite locking semantics; no distributed lock was added.

## API consistency audit

Public routes follow the `/api/v1/legacies/{legacy_id}/...` pattern.
Contracts use snake_case and controlled enums. Authentication, not-found,
conflict, pagination, and retry behavior are consistent with the existing
FastAPI application.

Terminology is consistent:

- **Legacy** for the preserved person/context;
- **Story Session** and **Story Message** for Guided Story persistence;
- **Memory candidate** or **Needs Review** before approval;
- **Revision** for pre-edit history;
- **Conflicting Memory** and **Related Memory** in human-facing UI.

## Performance audit

Existing beneficial controls include:

- indexed Legacy ownership/status and correlation uniqueness;
- indexed Story Session chapter/status and ordered message lookup;
- indexed extraction run status/source boundary;
- indexed Memory review/category/fingerprint fields;
- review pagination capped at 100, defaulting to 30;
- deterministic ordering;
- background extraction outside the response path;
- no extraction per token or per message;
- exact fingerprint lookup before persistence.

The review response eagerly loads provenance, participants, tags, and links.
Contradiction/related detail projection can still issue additional queries per
listed Memory. The default bounded page keeps this acceptable for the current
candidate volumes, but Phase 6.6 should batch related-memory projections if it
builds larger browsing pages.

FastAPI BackgroundTasks is lightweight but not durable across process
termination. Pending-run startup recovery remains a production limitation.

## Defects corrected during final validation

1. Namespaced user/assistant Story Message correlation keys.
2. Added bounded retry for concurrent Story sequence conflicts.
3. Strengthened direct CRUD provenance checks to match pipeline guarantees.
4. Marked partial candidate persistence as a retryable failed extraction run.
5. Removed raw exception traceback logging from persisted Story streaming.
6. Added authenticated persisted-Legacy hydration to restore continuity after
   browser session loss.
7. Updated older documentation to reflect fixed message-boundary metadata and
   partial-run behavior.

Focused regressions were added in
`backend/tests/test_memory_engine_hardening.py`.

## Test execution

### Executed

Frontend:

```text
node --test tests/*.test.js
```

Result: **56 passed, 0 failed**.

Every frontend JavaScript file also passed `node --check`.

The executed coverage includes existing streaming, Companion identity, Guided
Stories state, Legacy state, Memory Review, and persisted Story/Memory
integration checks.

### Statically verified only

Backend test modules were inspected, including extraction, validation,
persistence, storage pipeline, review, background extraction, streaming
persistence, and final hardening regressions. Migration lineage is continuous:

```text
0001 -> 0002 -> 0003 -> 0004
```

Backend tests did not execute because this workstation has the Windows Python
launcher but no installed Python runtime. `python` is not found, `py
--list-paths` reports no installed Python, and no Docker, Podman, Compose,
project virtual environment, or CI runtime is available.

From `WaffleBerry_backend/backend`, run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py"
```

Backend behavior is therefore implemented and statically reviewed, but cannot
be represented as runtime-passing in this environment.

## Milestone acceptance review

- **6.5.1 Architecture:** implemented without changing the Legacy-centered,
  provenance-first, human-review philosophy.
- **6.5.2 Schema:** entities, constraints, relationships, and migrations are
  present with continuous lineage.
- **6.5.3 Extraction:** dedicated prompt/service and structured candidates;
  no persistence inside extraction.
- **6.5.4 Validation:** deterministic normalization, provenance, duplicate,
  enrichment, contradiction, uncertainty, and confidence handling.
- **6.5.5 Storage:** candidate-only, provenance-atomic, idempotent persistence.
- **6.5.6 Review:** owner-scoped list/detail/edit/approve/reject, revisions,
  safe projections, and optimistic concurrency.
- **6.5.7 Background integration:** persisted Legacies/Stories/Messages,
  completion-bound runs, independent sessions, status, and retry.

No documented acceptance failure was found through static audit. The single
unresolved verification item is execution of the backend suite.

## Known limitations and recommendations

Before production deployment:

1. run the full backend suite under Python 3.10+;
2. run migrations against a disposable copy of the intended production
   database and test downgrade/upgrade;
3. add an application-startup recovery scan for stale `pending`/`running`
   extraction runs, or adopt a durable worker later;
4. exercise concurrent Story append, completion, extraction, and review calls
   against the production database;
5. replace permissive CORS configuration with deployed frontend origins;
6. add HTTP/SSE integration tests with a mocked provider.

These recommendations do not block Phase 6.6 development. They do block an
unqualified production-readiness claim.

## Readiness assessment

The Memory Engine preserves its intended boundaries: Berry proposes,
deterministic services validate, transactions preserve traceability, and the
owner explicitly decides. Candidate memories remain isolated from Companion
Chat.

**Result:** ready for Phase 6.6 development, with backend runtime execution and
durable background recovery explicitly carried forward as pre-production
requirements.
