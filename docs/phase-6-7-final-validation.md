# Phase 6.7 final validation

## Architecture and pipeline

The completed retrieval path keeps its responsibilities separated:

1. Authenticated chat routes load a conversation by both conversation ID and
   current user ID.
2. `MemoryRetrievalService` verifies that the linked Legacy belongs to that
   owner and asks CRUD for approved memories from that Legacy only.
3. `MemoryRelevanceRanker` performs deterministic lexical ranking. Importance
   and recency cannot create relevance and are tie-breakers only.
4. `CompanionMemoryGrounding` deduplicates and selects whole memories within the
   configured count, character, and estimated-token budgets.
5. The selected title, summary, and category values are serialized as untrusted
   JSON data inside explicit prompt boundaries.
6. Both Companion response modes use the same preparation path. Story Guide,
   extraction, and title generation remain separate.
7. Only IDs from the selected grounding set are carried internally. After a
   successful response, assistant-message persistence writes ordered provenance
   in the same transaction.

## Security review

The API authentication dependency and owner-filtered conversation lookup are the
first authorization boundary. Retrieval repeats owner/Legacy validation, while
provenance persistence defensively verifies the owner, Legacy, current approved
state, and complete memory-ID set. Candidate, rejected, superseded, unrelated,
cross-Legacy, and cross-user memories are excluded.

Browser correlation identifiers are never authorization. Companion response
schemas and SSE payloads expose neither provenance nor ranking data. Grounding
does not include IDs, scores, confidence, review metadata, or source locators.
Instruction-like memory values remain JSON data and cannot change their system
classification merely by containing apparent boundary text.

## Transaction review

History and retrieval reads complete before provider execution, and the read
transaction is ended before generation or streaming. Provider retries therefore
hold no database transaction. A generation failure creates neither messages nor
provenance.

Non-streaming persistence commits its user message, assistant message, and
provenance atomically. Streaming preserves the established earlier user-message
commit, then commits the completed assistant message and provenance atomically.
Failure during provenance validation or insertion rolls back the assistant
transaction, preventing an orphan assistant/provenance pair.

The audit found that application SQLite connections did not enable foreign-key
enforcement. Declared cascades were therefore ineffective locally and could
leave orphan provenance after deletion. Connection initialization now executes
`PRAGMA foreign_keys=ON`; PostgreSQL and other database behavior is unchanged.

## Performance review

The pipeline ranks before budgeting and budgets before prompt construction.
Retrieval uses one owner lookup and one approved-memory query, with no per-memory
query. Ranking and selection are deterministic. Selection preserves rank order,
removes duplicate IDs, skips oversized memories without truncation, and feeds
the exact selected set to provenance. Configuration is loaded once through the
existing cached dependency factory.

Application ranking still reads every approved memory for the Legacy. This is
an explicit lexical-foundation limitation, acceptable for current expected
volumes; caching, database search indexes, embeddings, and benchmarking were not
introduced.

## Prompt review

The centralized Berry prompt remains unchanged. The additional grounding rules
explicitly require Berry to remain Berry and prohibit speaking as or claiming to
be the Legacy person. Natural relational wording such as “You shared that…” is
allowed; first-person identity simulation such as “I am Anita” is not.

## Test audit

Focused suites cover approved-only retrieval, neutral ownership failures,
ranking and normalization, empty and Unicode input, budgeting, duplicates,
oversized memories, configuration overrides, safe prompt framing, streaming
parity, transaction closure, provenance ordering and rollback, and public-schema
isolation. The final pipeline test uses a real in-memory relational database and
fake AI service to validate retrieval through persistence without network calls.

## Known limitations and deferred work

Lexical matching does not infer synonyms or semantic similarity. Token estimates
are provider-neutral approximations. Provenance records memories supplied to the
model, not proof that particular response wording used them. The development
search endpoint intentionally exposes ranking diagnostics to an authenticated
Legacy owner; Companion responses do not.

Phase 6.8, embeddings, vector databases, caching, analytics, user-visible
citations, explainability UI, memory editing/deletion workflows, export/archive,
Voice, and Legacy Persona remain deferred.
