# Phase 6 final validation

## Scope and architecture

This audit covers Phase 6.6 My Legacy, Phase 6.7 Companion memory retrieval,
and Phase 6.8 lifecycle management. The backend preserves the established
Router → Service → CRUD/query → SQLAlchemy model boundaries. Dashboard,
settings, retrieval, grounding, archive/restore/delete, and export business
rules remain in services rather than routers. The frontend uses the shared
authenticated API client and a user-scoped Legacy state adapter.

Phase 6 uses existing relational data as its source of truth. Dashboard values
are aggregated rather than duplicated. Retrieval is limited to approved
memories, ranking is deterministic, prompt grounding treats retrieved text as
untrusted data, and grounding budgets bound memory count, characters, and
estimated tokens. Companion provenance records only response-to-memory links.

## Ownership and security

Legacy reads and mutations begin with a `legacy_id + owner_user_id` lookup.
Missing and foreign-owned records share neutral not-found behavior. Story,
Memory Review, retrieval, dashboard, settings, lifecycle, deletion, and export
queries remain Legacy-scoped. Companion conversations are additionally scoped
to the authenticated conversation owner. Browser correlation IDs synchronize
local records but never authorize lifecycle actions.

Archive and restore require authentication. Permanent deletion additionally
requires an exact, case-sensitive display-name confirmation after trimming
outer whitespace. Export excludes password/authentication data, owner IDs,
browser correlation IDs, normalized fingerprints, reviewer IDs, hidden prompts,
provider payloads, paths, credentials, retry internals, and ranking details.

The audit corrected cross-origin export handling: credentialed wildcard CORS is
disabled because the application uses Bearer headers rather than cookies, and
`Content-Disposition` is explicitly exposed so the browser can use the safe
server filename.

## Lifecycle and transactions

Persisted lifecycle states are Active and Archived. Archive and restore use an
owner-scoped locked lookup where supported and one commit/rollback boundary.
Archived dashboards, history, review data, and exports remain readable; Story,
Settings mutation, Companion generation, extraction retry, and memory mutation
are blocked until restoration.

Deletion accepts either active or archived Legacies. It explicitly removes the
Legacy-scoped Conversation graph because its schema intentionally uses `SET
NULL`, and removes Story, Memory, provenance, extraction, tag, contradiction,
revision, participant, link, and Companion-provenance data in dependency order.
One service-level commit makes the operation atomic; exceptions roll back.
Foreign keys and cascades remain an integrity backstop. No migration was needed.

Two lifecycle integration defects were corrected during final validation. The
older Companion settings action now calls authenticated permanent deletion
instead of hiding only local state. After confirmed backend deletion, the
frontend now clears any old hidden-ID tombstone rather than creating one, so a
future SQLite row-ID reuse cannot hide an unrelated Legacy.

## SQLite, PostgreSQL, and Alembic

SQLite connections enable foreign keys. Lifecycle deletion uses portable
SQLAlchemy operations and explicit dependency ordering rather than depending on
SQLite-only behavior. PostgreSQL can additionally enforce row locks used by
settings and lifecycle transitions; SQLite serializes writes without emulating
those locks. Optimistic timestamp comparison is normalized to UTC for both
dialects.

Static migration inspection confirms one linear chain:

`0001_existing_schema → 0002_memory_engine → 0003_memory_pipeline →
0004_story_background → 0005_companion_provenance`.

No Phase 6.8.6 schema or migration was added. Runtime `alembic heads` and
`alembic current` could not be executed in the Codex environment because it has
no installed Python interpreter.

## Performance

Dashboard aggregation uses focused count queries. Retrieval bounds candidate
results before prompt construction and grounding enforces fixed budgets. Export
uses focused Legacy-scoped queries and eager collection loading to avoid obvious
N+1 behavior. The JSON export is intentionally assembled in memory for current
development-scale Legacies; large production exports may eventually require
streaming or background generation.

## Frontend, accessibility, and responsive behavior

The management UI provides authoritative Active and Archived tabs, immediate
archive/restore synchronization, export download, and typed permanent-deletion
confirmation. It avoids duplicate backend IDs and removes stale cards. Archived
pages display a read-only banner and block Settings, Story Studio, transition,
and Companion entry routes.

Native buttons, links, form labels, focus restoration, live status/error
regions, progress labels, reduced-motion rules, exact confirmation feedback,
and arrow/Home/End tab navigation are present. The existing three/two/one-column
grid supports desktop, tablet, and mobile layouts, with dark-mode styles and a
stacked mobile destructive dialog.

## Testing and regression audit

The full frontend suite completed with 98 passing tests and zero failures.
Coverage includes API streaming, Companion identity, Guided Stories, dashboard,
progress, settings, Memory Review, persistence, lifecycle management, archive,
restore, delete, export, synchronization, error handling, accessibility, dark
mode, and responsive rules. `git diff --check` passed with only line-ending
conversion warnings.

The requested backend suite could not run in the Codex environment because
`python` and the Python launcher report no installed interpreter. For the same
reason, backend import execution and live Alembic commands remain unverified in
this environment. Static inspection found no prompt-text changes: Story Guide
identity separation, Memory extraction separation, approved-memory untrusted
boundaries, budgeting, and grounding semantics remain intact.

Run from `WaffleBerry_backend/backend` in the configured Python environment:

```powershell
python -m unittest discover -s tests -p "test_*.py"
python -m alembic heads
python -m alembic current
```

Expected head: `0005_companion_provenance`.

Run from `WaffleBerry_website`:

```powershell
node --test
```

## Known limitations and Phase 7 boundary

- Backend runtime and live-database validation must be rerun where Python is
  installed before declaring the release gate fully green.
- Frontend tests are primarily deterministic unit/static contract checks; a
  real-browser API smoke test remains useful in the final deployment environment.
- JSON export is memory-resident and does not contain binary media.
- SQLite cannot provide PostgreSQL-style row locking, although transaction and
  optimistic-concurrency protections remain in place.
- Retrieval is deterministic lexical relevance, not embeddings or semantic
  vector search.

Embedding retrieval, semantic indexing, Legacy Persona, Voice, and all other
Phase 7 work were deliberately not started. Subject to the outstanding backend
runtime commands passing, the audited Phase 6 architecture is ready for the
Phase 7 planning gate.
