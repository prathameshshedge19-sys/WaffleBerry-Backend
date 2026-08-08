# Companion memory provenance

Phase 6.7.4 records which approved memories were supplied to the model for a
successfully persisted Companion reply. This is internal persistence only: no
public request, response, streaming event, or frontend contract exposes it.

## Architecture and storage

The grounding preparation result carries an internal ordered tuple of memory
IDs and one retrieval timestamp alongside provider messages. The provider sees
only the already-delimited memory text. After successful generation, message
persistence writes rows to `companion_memory_provenance`.

The table contains only:

- `assistant_message_id` and `memory_id`, together forming the primary key;
- zero-based `retrieval_order`;
- `retrieved_at`.

A per-message order uniqueness constraint prevents ambiguous ordering. Foreign
keys cascade when either the assistant message or memory is deleted. Scores,
prompts, provider output, extraction confidence, review metadata, provenance
excerpts, and hidden reasoning are not stored.

An existing metadata column could not be reused because messages have no such
column and the existing `memory_provenance` table describes the source evidence
used to create a memory—the opposite direction from response grounding. A small
association table therefore provides the normalized, queryable lifecycle needed
without redesigning message persistence.

## Lifecycle and transactions

No row is created for an unlinked conversation, a no-match result, failed
generation, or an empty grounding set. Non-streaming persistence writes the user
message, assistant message, and provenance in one transaction. Streaming keeps
the established earlier user-message commit; only after the stream completes are
the assistant message and provenance committed together.

If provenance validation or insertion fails, the transaction containing the
assistant message is rolled back. For non-streaming this also rolls back its user
message. For streaming, the already committed user message remains, while no
assistant message or orphan provenance is stored. The existing API/SSE failure
handling reports the persistence failure rather than silently accepting an
inconsistent record.

## Security

Retrieval already enforces conversation owner and Legacy isolation. Persistence
defensively verifies again that the conversation's Legacy belongs to its owner
and every referenced memory belongs to that Legacy and is still approved. This
also handles a review-state change between retrieval and persistence. Duplicate,
cross-Legacy, candidate, rejected, superseded, and missing memories are rejected.
The table is not included in any public schema or endpoint.

Story Guide, Memory Extraction, title generation, and Legacy Persona behavior do
not use this lifecycle and remain unchanged.

## Limitations and future use

The record means a memory was supplied to the model; it does not prove that the
model used it in its wording. User-visible citations, provenance excerpts,
explainability, analytics, retention policy, and retrieval performance work are
future concerns and are outside Phase 6.7.4.
