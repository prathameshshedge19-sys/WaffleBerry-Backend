# Memory relevance ranking

Phase 6.7.2 adds deterministic lexical ranking on top of the approved-memory
retrieval boundary introduced in Phase 6.7.1. It does not feed memories into
prompts or change Companion, Story Guide, or AI behavior.

## Request and response

The authenticated development/testing endpoint is:

`POST /legacies/{legacy_id}/approved-memories/search`

The JSON body is `{"query": "jasmine tea"}`. Queries are trimmed, must contain
at least one character, and are limited to 2,000 characters. The response has
`legacy_id`, `matched_memory_count`, and `memories`. Each result retains the
normalized Phase 6.7.1 projection and adds a bounded `relevance_score` from 0
to 1 plus `matched_terms`. Internal review and provenance data remain excluded.

Missing and non-owned Legacies deliberately return the same 404 response.

## Normalization and scoring

Text is Unicode NFKC-normalized, case-folded, split into Unicode word tokens,
and filtered through a deliberately small structural stop-word list. Distinct
query terms are used so repetition cannot inflate a score.

The score is transparent and deterministic:

- exact normalized phrase in title: 0.35 bonus;
- otherwise, exact normalized phrase in summary: 0.25 bonus;
- title term coverage: up to 0.40;
- summary term coverage: up to 0.20;
- category term coverage: up to 0.05.

Scores are capped at 1. Results with zero lexical relevance are omitted. No
fallback returns unrelated memories. This minimum threshold is intentionally
simple for the retrieval foundation and can be calibrated with real evaluation
data in a later, explicitly authorized phase.

Ordering is relevance descending, then importance descending, update time
descending, and memory ID ascending. Importance never creates relevance and is
only a tie-breaker. The last two keys make repeated calls stable.

## Safety and limitations

The existing owner-scoped CRUD query remains the source of candidates, so only
approved memories can be ranked. Candidate, rejected, and superseded memories
cannot enter the results. Ranking is local and has no AI provider, network call,
embedding, vector database, schema change, migration, prompt integration, or
logging of query or memory content.

Lexical ranking understands textual overlap, not synonyms or semantic meaning.
The small English stop-word list is conservative and language-independent word
tokenization still permits useful non-English literal matches. Future semantic
retrieval, prompt use, caching, pagination, and production search policy are
outside Phase 6.7.2.
