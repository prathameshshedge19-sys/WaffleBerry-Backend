# Phase 6.6 Final Validation

## 1. Scope

Phase 6.6 delivers the authenticated My Legacy experience: an owner-scoped,
read-only dashboard; factual story-session, memory, extraction, and conversation
summaries; persisted-session progress; and non-destructive identity settings.

## 2. Completed milestones

- 6.6.1: dashboard architecture and API contract
- 6.6.2: My Legacy overview
- 6.6.3: progress, health, activity, and date presentation
- 6.6.4: Story Session Progress
- 6.6.5: Legacy Settings
- 6.6.6: final audit, hardening, tests, and documentation

## 3. Architecture

Dashboard requests flow from the authenticated router through
`LegacyDashboardService` to focused aggregate queries in
`LegacyDashboardCRUD`. Settings requests flow through `LegacySettingsService`
and owner-scoped `LegacyCRUD` primitives. The frontend uses the shared
authenticated API helper and keeps backend Legacy IDs separate from local
correlation IDs.

## 4. Dashboard contract

`GET /api/v1/legacies/{legacy_id}/dashboard` returns identity, lifecycle status,
timestamps, story-session statistics, memory statistics, extraction-run
statistics, normalized session groups, linked-conversation count, and factual
approved-memory availability. `title` is the dashboard presentation of the
model's `display_name`; settings uses `display_name` because it edits the model
field directly.

All aggregates are filtered by `legacy_id` after owner resolution. Counts use
independent aggregate queries, avoiding join multiplication and N+1 behavior.
Empty Legacies return zero values and an empty session-group list.

## 5. Story Session Progress semantics

Story Session Progress measures completed persisted `StorySession` rows divided
by all persisted sessions in a normalized chapter group. It does not claim to
measure unstarted or planned stories because no planned-story catalogue exists.
Chapter keys are trimmed and lowercased for grouping, distinct counting, and
deterministic ordering. The frontend uses canonical Guided Story titles where
known and a safe deterministic fallback otherwise.

## 6. Legacy Settings contract

`PATCH /api/v1/legacies/{legacy_id}` accepts `expected_updated_at` and one or
both of `display_name` and `relationship`. Text is trimmed and bounded by the
existing 255- and 100-character model limits. Unknown fields and attempts to
set status, ownership, IDs, timestamps, correlation data, or relationships are
rejected. The response excludes owner and correlation data.

Status remains read-only because `archived` is lifecycle management deferred
to Phase 6.8. No-op updates return the current projection without changing
`updated_at`.

## 7. Ownership and security

Dashboard and settings access resolve the Legacy with authenticated `user_id`
and `legacy_id`. Browser correlation IDs are never authorization. Missing and
non-owned records share neutral 404 responses. Settings uses an explicit
forbid-extra schema, so protected fields cannot be mass-assigned. Queries use
SQLAlchemy expressions without user-built SQL, and Phase 6.6 adds no sensitive
logging.

## 8. Concurrency

The settings request first validates `expected_updated_at`, then performs an
atomic owner- and timestamp-guarded update. A racing change makes the guarded
update affect zero rows and returns controlled `409 legacy_changed`; it cannot
silently overwrite. Real changes explicitly advance `updated_at` and commit
atomically. Failures roll back. The frontend exposes a keyboard-accessible
"Reload latest settings" action after conflicts.

## 9. Frontend state synchronization

Successful settings updates replace only the matching local entry's name and
relationship while retaining its local ID, creation date, active selection,
and backend Legacy ID. If the entry is unexpectedly absent, authoritative
hydration repairs local state. Correlation-matched hydration now refreshes name
and relationship as well as attaching the backend ID, preventing stale browser
identity from winning. Existing tombstone behavior remains intact.

## 10. Accessibility

The overview uses semantic headings and labeled progressbars with numeric and
text alternatives. Settings inputs have visible labels, associated help and
error text, native form controls, focus placement after validation, live status
feedback, and keyboard-accessible save, cancel, retry, and conflict-reload
actions. Information is not communicated by color alone.

## 11. Responsive behavior

Existing CSS provides desktop grids, tablet reflow, and single-column mobile
layouts. Settings actions stack at mobile width, controls maintain usable touch
height, metadata wraps, and long content can reflow without fixed-width
containers. Dark-mode variables and reduced-motion rules remain in use. This
review validated source rules but did not perform live visual QA at 375, 768,
and desktop widths.

## 12. Backend tests

Before Phase 6.6.6, the project reported 170/170 backend tests passing. Phase
6.6.6 added assertions for normalized distinct-chapter counts and a simulated
racing settings update. This workstation's current shell has no Python runtime
or project virtual environment, so the updated focused and full backend suites
could not be executed here and must be rerun in the project's Python 3.10+
environment.

## 13. Frontend tests

Modified JavaScript syntax checks passed. Focused Legacy overview, settings,
and state tests passed 40/40. The complete frontend suite passed 90/90. New
behavioral coverage verifies that correlation-matched hydration replaces stale
identity; source-level coverage verifies the conflict reload control and save
synchronization path.

## 14. Manual verification

No live browser or authenticated end-to-end session was run in this shell.
No development data was changed. Runtime login, populated/empty Legacy views,
save-and-return behavior, deliberate stale update, responsive widths, dark
mode, and stopped-backend retry behavior remain manual verification items.

## 15. Known limitations

- Backend tests and Alembic CLI commands require an available Python runtime.
- Browser-level rendering and interaction were not exercised during this run.
- Story Session Progress cannot include unstarted planned stories because no
  planned-story catalogue exists.
- Approved memories are reported factually but are not retrieved by Companion.

## 16. Deferred Phase 6.7

Approved-memory retrieval, relevance ranking, embeddings, Companion grounding,
prompt assembly, and citations remain Phase 6.7 work.

## 17. Deferred Phase 6.8

Archive, restore, export, deletion, ownership transfer, sharing, invitations,
and other Legacy lifecycle management remain Phase 6.8 work.

## 18. Readiness decision

Phase 6.6 is functionally complete with documented environment-validation
limitations. Final release readiness requires the updated backend suite,
Alembic `heads/current`, and the manual browser journey to pass in environments
where Python and the running application are available.
