# Legacy lifecycle architecture

Phase 6.8.1 defines the canonical lifecycle and service boundary without
activating archive, restore, deletion, export, or frontend behavior.

## Canonical lifecycle

```text
                    archive (6.8.2)
        ACTIVE --------------------------> ARCHIVED
          ^                                  |
          |                                  |
          +--------------- restore ----------+
                           (6.8.2)

        ACTIVE or ARCHIVED -- permanent delete (6.8.3) --> [record absent]
```

`ACTIVE` and `ARCHIVED` are the only persisted states and already exist in
`LegacyStatus`. `ACTIVE` is the application and database default. `ARCHIVED` is
recoverable through the future restore operation. Neither persisted state is
terminal.

`DELETED` is not a persisted status. In Phase 6.8.3, successful permanent
deletion will remove the Legacy record and its governed dependent data. It is an
irreversible terminal outcome represented by record absence. Adding a fake
`deleted` row state would complicate ownership, foreign keys, and retention
without representing permanent deletion accurately.

## State rules

| Capability | Active | Archived |
| --- | --- | --- |
| Normal-list visibility | Visible | Hidden |
| Identity editing | Allowed | Blocked |
| Companion | Available | Unavailable |
| New/continued Story Sessions | Allowed | Blocked |
| Dashboard | Available | Available read-only by direct owner access |
| Mutation policy | Mutable | Read-only |
| Recovery | Not applicable | Restore allowed |

Phase 6.8.2 now enforces these policies. Archived dashboards and direct data
reads remain available for owner-only management inspection, while mutations and
active Companion/Story experiences are blocked.

## Service boundary and transitions

`LegacyLifecycleService` is a side-effect-free policy boundary. It owns state
capabilities and validates the only reversible persisted transitions:

- `ACTIVE → ARCHIVED`
- `ARCHIVED → ACTIVE`

Same-state requests, unknown states, and attempts to persist `DELETED` are
invalid. The service does not query, update, commit, delete, export, or register
routes. Phase 6.8.2 should coordinate owner lookup, row locking, transition
validation, and persistence through this service rather than duplicating status
rules in routers or CRUD.

## Authorization

Every lifecycle operation is authenticated and owner-only. The existing
`LegacyCRUD.get_user_legacy` and `get_user_legacy_for_update` owner filters remain
the authoritative persistence boundary. Foreign-owned and missing Legacy IDs
must produce the same neutral 404 response. Client correlation IDs and other
browser-provided values are never authorization evidence.

## Future API contracts

These contracts are documentation only in 6.8.1; no placeholder routes are
registered:

- `POST /api/v1/legacies/{legacy_id}/archive` — owner-only, transitions active
  to archived, idempotency/concurrency behavior finalized in 6.8.2.
- `POST /api/v1/legacies/{legacy_id}/restore` — owner-only, transitions archived
  to active in 6.8.2.
- `DELETE /api/v1/legacies/{legacy_id}` — permanent owner-confirmed deletion in
  6.8.3; never implemented as a `deleted` status update.
- `GET /api/v1/legacies/{legacy_id}/export` — owner-only export in 6.8.4 with
  content and streaming policy defined there.

Existing response schemas are not changed. Later mutation contracts should use
the project's optimistic-concurrency convention where applicable.

## Compatibility

No model redesign is required. The existing status column and owner/status index
support future active-list filtering and archive transitions. Dashboard, Settings,
Story Sessions, Companion retrieval, Review Queue, Memory Engine, conversation
history, and provenance all remain linked by the same `legacy_id`.

Phase 6.8.2 will apply archived-state filtering and guards consistently across
those entry points. Conversation history and dependent data remain stored while
archived. Phase 6.8.3 must explicitly define deletion scope and transaction
behavior. Phase 6.8.4 defines export contents; Phase 6.8.5 adds management UI;
Phase 6.8.6 performs final lifecycle validation.

## Database decision

No migration is required. `legacies.status` already uses the constrained
`LegacyStatus` enum with `active` and `archived`, defaults to `active`, and is
covered by the existing owner/status index.
