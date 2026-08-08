# Legacy export

Phase 6.8.4 provides `GET /api/v1/legacies/{legacy_id}/export` for an
authenticated Legacy owner. Missing and foreign-owned records share a neutral
404. Active and archived Legacies are exportable; exporting never restores,
updates, or otherwise mutates them. Deleted Legacies naturally return 404.

The response is a UTF-8 JSON attachment named
`waffleberry-legacy-{safe-name}-{YYYY-MM-DD}.json`. The name is normalized to a
short ASCII lowercase slug, strips path/control punctuation, and falls back to
`legacy-{id}` when no safe name characters remain.

The top-level contract is identified by `export_format:
"waffleberry_legacy"` and `export_version: 1`. It contains the safe Legacy
profile, ordered Story Sessions and Story Messages, every Memory review state,
participants, display tags, revisions, contradiction and supersession/link
relationships, safe source provenance, concise extraction history, only
Legacy-linked Conversations and their ordered Messages, and owner-readable
assistant-message-to-memory grounding links. Stable timestamps and IDs provide
traceability; lists use explicit deterministic ordering. Only `exported_at`
changes between exports of otherwise unchanged data.

The export excludes owner IDs, password/authentication material, browser
correlation IDs, normalized fingerprints, reviewer user IDs, normalized tag
keys, retry counts, raw exception/provider errors, retrieval scores and terms,
audio/server file paths, provider payloads, prompts, system instructions,
credentials, logs, and database metadata. Correlation IDs are browser
idempotency details rather than portable Legacy content. Source-locator keys
that indicate paths, files, provider/prompt data, tokens, credentials, or
secrets are removed. Extraction records retain only status, trigger, boundary,
counts, safe failure category, and lifecycle timestamps.

The query layer scopes every query by both the owned Legacy and, for
Conversations, the authenticated user. Related collections are eagerly loaded
or queried in focused batches to avoid obvious N+1 behavior. The current
implementation assembles JSON in application memory, which is appropriate for
development-scale Legacies but will eventually need streaming or background
generation for very large archives. Version 1 is a portable owner backup, not
an import contract, and contains no binary media.

PDF/media bundles, import/restore, frontend download/archive/delete controls,
and public sharing remain out of scope. Phase 6.8.5 may add management UI;
Phase 6.8.6 remains reserved for end-to-end validation and polish.
