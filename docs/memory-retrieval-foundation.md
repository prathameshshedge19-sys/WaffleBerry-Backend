# Memory Retrieval Foundation

## Architecture

Phase 6.7.1 adds a read-only retrieval path:

```text
GET /api/v1/legacies/{legacy_id}/approved-memories
    → MemoryRetrievalService
    → MemoryCRUD.list_approved_for_retrieval
    → Memory
```

The endpoint is authenticated. The service resolves the Legacy with both its
integer ID and the current user's ID before the CRUD query runs. Missing and
non-owned Legacies return the same neutral 404 response. Browser correlation
IDs are not accepted as ownership proof.

## Retrieval rules

Only records whose `review_status` is `approved` are retrieved. Candidate,
rejected, and superseded records are excluded at the database query boundary.
Both atomic and narrative memories are supported.

Each normalized item contains:

- `memory_id`
- `memory_type`
- `category`
- `title`
- `summary`
- `importance`
- `extraction_confidence`
- `created_at`
- `updated_at`

Review status, reviewer identity, fingerprints, contradiction structures,
revisions, and provenance are deliberately absent from this foundation
contract.

## Ordering

Results use this deterministic order:

1. importance descending, with null importance last;
2. updated time descending;
3. memory ID ascending.

Importance is an existing human-visible memory attribute, not query relevance.
No user-query filtering or ranking is performed.

## API contract

Example response:

```json
{
  "legacy_id": 5,
  "approved_memory_count": 2,
  "memories": [
    {
      "memory_id": 12,
      "memory_type": "atomic",
      "category": "personal_detail",
      "title": "Mother's name",
      "summary": "Mother's name is Anita.",
      "importance": 5,
      "extraction_confidence": "0.950",
      "created_at": "2026-07-30T12:00:00Z",
      "updated_at": "2026-07-31T12:00:00Z"
    }
  ]
}
```

An owned Legacy with no approved memories returns a count of zero and an empty
array.

## Database impact

The implementation reuses the existing `memories` table and the existing
`(legacy_id, review_status)` index. No model, table, column, migration, or
Alembic revision is added.

## Limitations and future milestones

Phase 6.7.1 performs complete approved-memory retrieval for development and
testing only. It does not add semantic search, embeddings, query understanding,
relevance ranking, limits, token budgeting, citations, prompt assembly, or
Companion behavior. Those capabilities remain in later Phase 6.7 milestones.
