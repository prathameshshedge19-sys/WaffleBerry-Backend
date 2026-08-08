# Phase 10.19 automatic memory learning

Automatic learning reuses the canonical `Memory`, provenance, validation, fingerprint,
contradiction, enrichment, identity-projection, and approved-retrieval architecture.
`AUTO_MEMORY_LEARNING_ENABLED` defaults to `false`.

After a completed Chat response, or after a final Live Call user transcription, a detached
best-effort task opens its own database session. The response/audio path never waits for
extraction. Failures produce counts only and never fail or notify the conversation.

The existing strict structured extractor accepts only user-authored evidence. Unknown model
fields are rejected; the server supplies authenticated user and Legacy scope. Browser Live
Call input contains only a bounded final transcription and turn number tied to an already
authenticated session—it cannot select an owner or Legacy. Raw audio is never persisted.

The cheap prefilter drops known greetings, filler, temporary moods, and technical call
chatter. The model then applies the existing durability prompt. Automatic persistence
requires importance 4/5 and extraction confidence at least 0.85. Exact fingerprint and
semantic validation prevent duplicates; enrichments remain linked and contradictions use
the existing contradiction group. Corrections therefore preserve history rather than
silently overwriting it.

Accepted automatic memories are immediately `approved`, because all Companion retrieval
paths intentionally read only approved memories. Identity facts are projected through the
existing F2/F3 service. Provenance retains `conversation` or `live_call` source and an
extractor version ending in `-auto`; logs contain counts and durations, never memory text.

Stored Memories appears in the existing Legacy Dashboard. It lists approved memories in
bounded pages. Content is rendered with `textContent`. Editing preserves revisions,
recomputes fingerprints, invalidates stale embeddings, and rebuilds identity projections.
Deletion is owner/Legacy scoped and removes the canonical memory so it cannot be retrieved.
No database migration is required.
