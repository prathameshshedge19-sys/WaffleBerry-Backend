# Companion memory grounding

Phase 6.7.3 grounds Berry's Companion responses with relevant approved memories
without introducing Legacy Persona behavior, citations, embeddings, or frontend
changes.

## Eligibility and isolation

Grounding runs only for a nonblank Companion message in a persisted conversation
that has a `legacy_id`. The existing chat route first verifies conversation
ownership. `MemoryRetrievalService` then verifies that the linked Legacy belongs
to the conversation owner and searches only that Legacy's approved memories.
Candidate, rejected, superseded, unrelated, cross-Legacy, and cross-user data
cannot enter grounding. Browser correlation identifiers are not authorization.

The current user message alone is the retrieval query. Conversation history,
assistant output, AI query expansion, and embeddings are not used. A no-match
result adds no grounding section and normal Companion behavior continues.

## Prompt boundary

`CompanionMemoryGrounding` serializes only title, summary, and category as JSON
between explicit approved-memory data markers. The surrounding system text says
that every value is untrusted data, including instruction-like text or apparent
boundary markers. IDs, scores, confidence, review state, provenance, and other
internal metadata are excluded.

Berry is instructed to use a memory only when relevant, preserve uncertainty,
qualify conflicts rather than resolve them, avoid unnecessary repetition, and
never mention retrieval internals. Berry remains Berry and must never impersonate
or speak in first person as the Legacy person.

## Response paths and transactions

Both non-streaming and streaming Companion paths use the same
`ChatService.prepare_ai_input` method, so their grounding context is identical.
Retrieval and history loading complete before provider generation begins. The
read transaction is ended before either normal generation or provider streaming;
the existing route performs its separate persistence commits at its established
boundaries. Story Guide, Memory Extraction, and title generation do not use this
grounding path and remain unchanged.

## Failure policy

Security/ownership failures and SQL retrieval failures fail closed as a controlled
`memory_grounding_failed` AI service error. The API maps it through its existing
safe service-error response. It does not silently generate an answer that might
appear grounded, and it does not log raw queries or memory contents.

## Deferred work

Phase 6.7.4 may add citations and provenance. Phase 6.7.5 may introduce explicit
budgets, limits, and performance work. Phase 6.7.6 owns broader end-to-end
validation. This phase adds no audit persistence, semantic retrieval, caching,
persona mode, voice behavior, or visible citation UI.
