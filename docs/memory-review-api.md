# Memory Review API

**Milestone:** Phase 6.5.6  
**Prefix:** `/api/v1`  
**Authentication:** Bearer token on every endpoint

## Purpose

Berry proposes reviewable Memory candidates. Only an authenticated Legacy
owner can decide whether a candidate is kept, edited, or removed from future
approved-memory use. Opening or listing the review page never approves a
Memory.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/legacies/{legacy_id}/memories/review` | List owner-scoped memories; defaults to candidates |
| `GET` | `/legacies/{legacy_id}/memories/{memory_id}` | Read safe review details |
| `POST` | `/legacies/{legacy_id}/memories/{memory_id}/approve` | Explicitly approve a candidate |
| `POST` | `/legacies/{legacy_id}/memories/{memory_id}/reject` | Explicitly reject without deletion |
| `PATCH` | `/legacies/{legacy_id}/memories/{memory_id}` | Edit a candidate and create a revision |

Every operation queries the Legacy using both its ID and the authenticated
user. Memory and relationship queries are constrained to the same Legacy.
Missing and inaccessible resources return the same generic `404`, avoiding
cross-owner existence disclosure.

## Listing, filters, and pagination

The list defaults to `review_status=candidate`, `offset=0`, and `limit=30`.
The maximum page size is 100. Supported filters are:

- review status;
- category;
- memory type;
- source type;
- contradiction presence;
- possible-enrichment presence;
- Story Session ID.

Results are ordered by importance descending, then creation time and Memory ID
descending. This is deterministic and prioritizes meaningful candidates while
keeping newer candidates first within the same importance.

## Response contract

`MemoryReviewResponse` contains only reviewer-facing fields:

- type, category, title, summary, structured details;
- emotional significance, importance, extraction confidence, and uncertainty;
- participants and tags;
- review state and timestamps;
- exact verified provenance excerpts and safe source labels;
- same-Legacy contradictory or possible-enrichment summaries.

It excludes normalized fingerprints, source locators, provider data, raw
validation codes, full conversations, assistant evidence, database internals,
and hidden prompts.

## Review transitions

Phase 6.5.6 permits:

```text
candidate -> approved
candidate -> rejected
candidate -> candidate (edit)
```

Approved, rejected, and superseded records cannot be casually reopened or
transitioned through this UI. Repeated approve/reject calls therefore return a
controlled `409 memory_changed` response. Rejection is a status transition,
not deletion; provenance and audit information remain.

Approval and rejection preserve the normalized fingerprint, provenance,
extraction confidence, contradictions, enrichment links, and other extracted
content. They set `reviewed_at` and `reviewed_by_user_id`.

## Editing and revisions

Editable fields are title, summary, category, memory type, validated details,
emotional significance, importance, uncertainty, participants, and tags.
Ownership, source/provenance identifiers, extraction confidence, fingerprint,
contradiction membership, IDs, and review timestamps are absent from the
request contract.

Before mutation, the service stores a validated snapshot of every editable
field in `MemoryRevision`, attributed to the authenticated user. The edited
Memory remains a candidate. Provenance remains attached and unchanged.

The deterministic normalized fingerprint is regenerated from edited content.
The service checks both populated fingerprints and nullable historical
fingerprints by computing the latter safely when comparing. If the edit would
be equivalent to another same-Legacy Memory, it rolls back and returns:

```json
{
  "detail": {
    "code": "equivalent_memory_exists",
    "message": "An equivalent memory already exists. Refresh before deciding what to keep."
  }
}
```

No merge or deletion occurs.

## Optimistic concurrency

Approve, reject, and edit requests require `expected_updated_at`, taken from
the current review response. The service locks the candidate row where the
database supports row locks, checks that it is still a candidate, and compares
the expected timestamp. A stale tab receives controlled HTTP `409`; the
frontend refreshes instead of overwriting newer work.

This is intentionally practical optimistic concurrency, not distributed
locking.

## Contradictions and enrichments

Contradictory memories remain independent. Responses include other
same-Legacy accounts, their summaries, states, and safe provenance. The API
does not select a winner, remove a group, or change another account when one is
reviewed.

Possible enrichments are exposed as related memories. Approving one does not
merge it into or modify the related Memory.

## Dependency lifetime

`get_memory_storage_pipeline()` is cached, but the pipeline contains only
stateless extraction and validation services. It retains no SQLAlchemy
session, authenticated user, Legacy, source ID, or transaction state.

The review service is created per request. The request-scoped `Session` is
supplied only to individual service methods by FastAPI and is closed through
the existing `get_db()` lifecycle.

## Test status

`backend/tests/test_memory_review.py` adds the 22 requested owner-scope,
transition, revision, fingerprint, provenance, relationship, concurrency,
response-boundary, dependency-lifetime, and pipeline-regression checks. It
makes no AI or network calls.

Python remains unavailable on this workstation (`py` reports no installed
Python and `python` is not found), so backend tests were not executed. From
`WaffleBerry_backend/backend`:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py"
```

Approved-memory browsing, retrieval, Companion intelligence, automatic
extraction, merging, embeddings, and lifecycle reopening remain out of scope.
