# Companion memory grounding budgets

Phase 6.7.5 bounds approved-memory prompt context without changing retrieval
ranking, ownership, response behavior, provenance storage, or public APIs.

## Architecture and algorithm

Budgeting runs in `CompanionMemoryGrounding`, after deterministic ranking and
before prompt construction. Memories are considered in their existing ranking
order. The selector keeps a memory only when the complete grounding section
would remain within all configured ceilings. It never reorders or truncates a
memory. If one item is too large, it is skipped and later ranked items are still
considered.

Duplicate memory IDs keep their first occurrence, which is the highest-ranked
instance under the retrieval contract. No-match and empty selections continue
without a grounding section.

The selector returns the selected memory objects together with their rendered
context. `ChatService` derives internal provenance IDs from that selected set,
so skipped and duplicate memories cannot be persisted as grounding provenance.
Streaming and non-streaming use the same selector.

## Configuration

The existing application settings support these environment variables:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `MEMORY_GROUNDING_MAX_MEMORIES` | 8 | Maximum complete memories |
| `MEMORY_GROUNDING_MAX_ESTIMATED_TOKENS` | 1500 | Approximate grounding tokens |
| `MEMORY_GROUNDING_MAX_CHARACTERS` | 6000 | Complete rendered context characters |

All limits are positive and all must be satisfied. They are internal and are not
returned by any endpoint.

## Token estimation

Estimated tokens are `ceil(rendered_character_count / 4)`. This deterministic,
provider-neutral approximation includes the safety instructions, JSON framing,
and memory data. It avoids external or provider-specific tokenizers. It is not
linguistically exact—especially for some Unicode scripts—but gives a stable
safety budget alongside the exact character limit.

## Performance rationale and limitations

Bounding count and rendered size prevents an increasing approved-memory corpus
from expanding each Companion prompt without limit. Selection currently renders
candidate contexts in application memory; the deliberately small maximum count
keeps that work bounded in normal use. Retrieval itself remains the Phase 6.7.2
lexical implementation.

Provider-specific tokenization, caching, embeddings, vector search, benchmarks,
analytics, and adaptive prompt budgeting are possible future improvements and
remain outside this phase.
