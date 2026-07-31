# Legacy archive and restore

Phase 6.8.2 implements reversible, owner-scoped lifecycle transitions while
preserving every Legacy-related record.

## Endpoints and listings

- `POST /api/v1/legacies/{legacy_id}/archive`
- `POST /api/v1/legacies/{legacy_id}/restore`
- `GET /api/v1/legacies?status=active` (default)
- `GET /api/v1/legacies?status=archived`

All endpoints require authentication. Missing and foreign-owned identifiers use
the same neutral 404. Archive and restore accept no client-supplied target status.
Their response contains only Legacy ID, status, display name, relationship, and
updated time.

Repeated archive and restore requests are idempotent: they return `200` with the
current target state and do not refresh `updated_at` again. Real transitions lock
the owner-scoped Legacy row where supported, update only `status` and
`updated_at`, and commit atomically.

## Archived behavior

Archived Legacies are absent from the default active listing but remain available
through the explicit archived filter and direct owner lookup. The dashboard stays
available for read-only management inspection. Existing Story Sessions, Story
Messages, Memories, conversations, chat history, grounding provenance, and client
correlation ID remain unchanged.

Archived Legacies are read-only:

- settings changes return `409 legacy_archived`;
- Story creation/resume, message streaming, completion, extraction retry, and
  newly scheduled extraction are blocked;
- new Companion messages return `409 legacy_archived`, while existing history
  remains readable;
- Companion grounding rejects archived Legacies defensively;
- memory review lists/details remain readable, while approve, reject, and edit
  mutations return `409 legacy_archived`.

Direct approved-memory retrieval/search remains owner-only but is allowed for
read-only management and development inspection. This does not bypass Companion
blocking because internal grounding uses the active-only default policy.

## Concurrency and security

The lifecycle service owns ownership lookup, idempotency, transition validation,
and transaction orchestration. CRUD exposes only a focused status-update
primitive. Row locking serializes concurrent transitions on databases that
support `FOR UPDATE`; idempotent target-state handling prevents repeated actions
from producing conflicts or duplicate Legacies. No browser correlation ID is
used for authorization and no related content is logged.

No migration is required: the existing constrained status and `updated_at`
columns are reused. Phase 6.8.3 deletion, 6.8.4 export, 6.8.5 management UI, and
6.8.6 final lifecycle validation remain deferred.
