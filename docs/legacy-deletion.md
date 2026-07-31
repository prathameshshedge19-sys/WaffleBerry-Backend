# Legacy deletion

Phase 6.8.3 adds permanent, owner-scoped deletion through `DELETE
/api/v1/legacies/{legacy_id}`. The authenticated owner must send JSON containing
`confirmation_text`; after trimming outer whitespace, it must exactly match the
Legacy `display_name`. Unicode is preserved and comparison is case-sensitive.
A missing or foreign-owned Legacy returns the same neutral 404. A mismatch
returns a controlled 400, and a successful deletion returns 204 with no body.

Deletion uses a hybrid cascade strategy. The CRUD layer explicitly removes the
Legacy-scoped Story, Memory, extraction, provenance, Companion provenance, and
Conversation graphs in dependency order. This is necessary because Conversation
uses `ON DELETE SET NULL`, while deletion semantics require its removal. Existing
foreign keys and cascades remain enabled as an integrity backstop on SQLite and
PostgreSQL. No schema change or migration is required.

The lifecycle service locks the owner-scoped Legacy where the database supports
row locking, validates confirmation, stages all cleanup, and commits once. Any
exception rolls the transaction back, so partial deletion is not retained.
Browser correlation IDs are never consulted for authorization or confirmation,
and row counts, owner IDs, and database metadata are never exposed.

Permanent deletion applies equally to active and archived Legacies. It differs
from archive: archive is read-only and recoverable, whereas deletion removes the
record and cannot be restored. Subsequent dashboard, Companion, restore, or
repeat-delete lookups naturally receive 404. A future management UI should show
the exact Legacy name, require the user to type it, explain permanence, and send
the confirmation request only after an explicit destructive-action step.
