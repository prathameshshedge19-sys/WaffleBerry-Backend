# AI Memory Extraction

**Milestone:** Phase 6.5.3 — AI Memory Extraction  
**Scope:** Provider-neutral candidate generation only  
**Persistence:** None

## Purpose

The Memory Extraction layer identifies information worth preserving from a
persisted Story Session or a legacy-associated normal conversation. It returns
zero, one, or many validated `MemoryCandidateCreate` objects.

It does not:

- summarize the conversation;
- chat with the user;
- persist candidates;
- approve or reject memories;
- merge duplicates;
- detect or create contradiction groups;
- create revisions or supersession links;
- retrieve memories for Companion Chat;
- use embeddings or vector search.

## Components

### Dedicated prompt

Location:

`backend/app/services/ai/prompt_builder.py`

`PromptBuilder.build_memory_extraction_system_prompt()` returns the centralized
Memory Archivist instructions. The prompt is separate from Berry's Companion
and Story Guide prompts.

The model is instructed to:

- extract enduring, source-grounded legacy information;
- ignore small talk, temporary plans, weather, assistant claims, and meta
  conversation;
- return atomic facts and meaningful narrative memories;
- use only the controlled category contract;
- preserve partial, approximate, uncertain, and disputed information;
- assign importance from 1–5;
- assign extraction confidence from 0–1;
- cite exact excerpts from eligible user-authored messages;
- return only the supplied JSON contract;
- never generate database IDs, review decisions, contradiction groups,
  supersession links, or provenance timestamps.

### Extraction contracts

Location:

`backend/app/services/memory/contracts.py`

`MemoryExtractionResult` contains up to 50 `ExtractedMemory` records. Each
record includes:

- `memory_type`
- `category`
- `title`
- `summary`
- validated `details`
- optional `emotional_significance`
- `importance`
- `extraction_confidence`
- optional `uncertainty_note`
- participants
- tags
- one or more evidence references

Evidence contains only:

- `source_message_id`
- an exact, short source excerpt

The model does not construct `MemoryProvenanceCreate` directly.

### Extraction service

Location:

`backend/app/services/memory/extractor.py`

Public methods:

- `MemoryExtractionService.extract_story_session()`
- `MemoryExtractionService.extract_conversation()`

Both return:

```text
list[MemoryCandidateCreate]
```

These are the existing Phase 6.5.2 candidate contracts. They have no database
identity and no review status. When candidates are persisted in a later phase,
the existing repository will create them as `candidate`, never `approved`.

### Exceptions

Location:

`backend/app/services/memory/exceptions.py`

- `MemoryExtractionError`
- `MemoryExtractionSourceError`
- `MemoryExtractionResponseError`

These extend the existing safe AI exception hierarchy and expose
machine-readable categories without including provider payloads or source
conversation text.

## Extraction flow

```text
Persisted Legacy and source container
  ↓
Validate same-legacy source relationship
  ↓
Validate and deterministically order visible source messages
  ↓
Build provider-neutral system + user AIMessage input
  ↓
Existing shared AIService
  ↓
Existing configured AIProvider / OpenAI Responses API
  ↓
Parse strict JSON into MemoryExtractionResult
  ↓
Validate categories, ranges, dates, participants, tags, and evidence
  ↓
Verify every excerpt against its real user-authored source message
  ↓
Build server-owned MemoryProvenanceCreate records
  ↓
Return unpersisted MemoryCandidateCreate objects
```

Extraction uses `AIService.generate_response()` rather than streaming. A
complete result is required before JSON and cross-field validation can succeed.
This does not change or duplicate the existing streaming implementation.

## Input construction

The provider receives two provider-neutral messages:

1. the dedicated Memory Extraction system prompt;
2. one JSON data envelope containing:
   - legacy display name;
   - relationship;
   - optional chapter;
   - source type and container ID;
   - deterministically ordered visible messages;
   - whether each message is eligible as evidence;
   - the generated Pydantic JSON output schema.

Source message contents are explicitly data, not instructions. Only
user-authored messages are evidence-eligible. Assistant messages may provide
conversational context but cannot support a candidate.

If a source has no eligible user-authored messages, the service returns an
empty list without calling the provider.

## Story Session extraction

`extract_story_session()` requires:

- a persisted `Legacy`;
- a persisted `StorySession`;
- `StorySession.legacy_id == Legacy.legacy_id`;
- persisted Story Messages belonging to that session;
- positive unique message IDs and positive deterministic sequence values.

Generated provenance contains:

- `source_type = "story_session"`
- `story_session_id`
- `story_message_id`
- `chapter`
- `speaker = "user"`
- the verified excerpt
- `extractor_version = "memory-extractor-v1"`

## Conversation extraction

`extract_conversation()` requires:

- a persisted `Legacy`;
- a persisted `Conversation`;
- `Conversation.legacy_id == Legacy.legacy_id`;
- persisted Messages belonging to that conversation.

Existing conversations with `legacy_id = NULL` cannot be extracted for a
legacy. They remain valid normal conversations, but they lack the ownership
association required for memory provenance.

Generated provenance contains:

- `source_type = "conversation"`
- `conversation_id`
- `message_id`
- `speaker = "user"`
- the verified excerpt
- `extractor_version = "memory-extractor-v1"`

## Provenance safety

Database provenance references are built by application code, not accepted
from model output.

For every proposed memory:

1. the evidence message ID must exist in the supplied source;
2. that source message must be user-authored;
3. the cited excerpt must be a verbatim substring of that message;
4. duplicate evidence pairs are removed;
5. at least one usable source remains;
6. the service constructs the source type, container ID, message ID, chapter,
   speaker, and extractor version.

This prevents a model from inventing cross-legacy IDs, citing its own assistant
text, or attaching unsupported provenance.

Only the short cited excerpt is copied into the returned provenance contract.
The complete source conversation is not duplicated.

## Candidate structure

Example returned Story Session candidate:

```json
{
  "memory_type": "atomic",
  "category": "personal_detail",
  "title": "Birthplace and year",
  "summary": "Mom was born in Pune in 1968.",
  "details": {
    "places": [
      {
        "name": "Pune",
        "region": "Maharashtra",
        "country": "India",
        "certainty": "stated"
      }
    ],
    "temporal_references": [
      {
        "text": "1968",
        "start_date": "1968-01-01",
        "end_date": "1968-12-31",
        "precision": "year",
        "is_approximate": false,
        "certainty": "stated"
      }
    ]
  },
  "emotional_significance": null,
  "importance": 5,
  "extraction_confidence": 0.96,
  "uncertainty_note": null,
  "participants": [
    {
      "name": "Mom",
      "relationship": "mother",
      "role": "subject"
    }
  ],
  "tags": [
    "early-life"
  ],
  "provenance": [
    {
      "source_type": "story_session",
      "story_session_id": 310,
      "story_message_id": 881,
      "excerpt": "I was born in Pune in 1968",
      "speaker": "user",
      "chapter": "Childhood",
      "extractor_version": "memory-extractor-v1"
    }
  ]
}
```

The result intentionally has no:

- `memory_id`
- `legacy_id`
- `review_status`
- `contradiction_group_id`
- `superseded_by_memory_id`
- persistence timestamps

The caller already owns the verified legacy context. Persistence remains an
explicit later operation through the existing owner-scoped repository.

## Importance

Importance is an extraction-time prioritization hint:

- `1` — minor but enduring personal detail;
- `2` — modest long-term personal context;
- `3` — meaningfully useful legacy context;
- `4` — important identity, relationship, or family-history information;
- `5` — central identity, major life event, or deeply significant family
  history.

The model should ignore trivia instead of assigning it a low score. Birthplace
and enduring family traditions will often be high; today's weather should
produce no memory. Importance is reviewable and is not an approval decision.

## Confidence and uncertainty

`extraction_confidence` records how confident the extractor is that it
interpreted the source correctly. It does not claim the memory is factually
true.

Source uncertainty remains separate:

- the original qualifying excerpt is preserved;
- `uncertainty_note` explains the qualification;
- temporal references retain original text;
- `is_approximate`, `precision`, and `certainty` retain partial or fuzzy dates;
- unknown components remain `null`.

For “I think we moved around 1985,” a valid candidate may have high extraction
confidence while still marking the claim and date as uncertain.

## Failure handling

Provider and transport failures continue through the existing `AIService`
exception and retry behavior.

The extraction layer raises safe custom errors for:

- source containers from another legacy;
- unpersisted, duplicate, blank, or cross-container source messages;
- malformed JSON;
- output that does not match the candidate schema;
- unsupported categories;
- invalid importance or confidence;
- nonexistent evidence IDs;
- assistant-authored evidence;
- non-verbatim evidence excerpts.

Exceptions never include:

- API keys;
- OpenAI SDK objects;
- provider request payloads;
- complete source conversations;
- invalid provider response text.

No partial candidate list is returned. If one proposed candidate is invalid,
the entire extraction result fails validation and may be safely retried or
reviewed operationally.

## Provider reuse

`backend/app/dependencies/ai.py` now exposes one cached
`get_ai_service()`.

Both:

- `get_chat_service()`
- `get_memory_extraction_service()`

use that same `AIService`, provider adapter, retry policy, configuration, and
underlying asynchronous OpenAI client. The extraction layer contains no OpenAI
SDK import and creates no second provider client.

## Testing

`backend/tests/test_memory_extraction.py` uses a fake `AIService` that returns
mocked text. It makes no OpenAI or network calls.

Coverage includes:

- zero candidates;
- multiple atomic and narrative candidates;
- existing `MemoryCandidateCreate` output;
- Story Session provenance;
- conversation provenance;
- approximate dates and uncertainty;
- dedicated prompt separation;
- malformed JSON;
- invalid candidate fields;
- unknown or assistant-authored evidence;
- non-verbatim evidence;
- cross-legacy source rejection before provider invocation;
- no-user-message short circuit.

## Future integration

A later milestone can:

1. load a legacy and source through owner-scoped repository methods;
2. call the appropriate extraction method after source persistence;
3. present the returned candidates for review or explicitly persist them as
   `candidate`;
4. record extraction runs and idempotency keys;
5. add durable background execution;
6. add review APIs and UI;
7. eventually allow only approved memories into retrieval.

Before persistence, candidates should continue through:

- the existing Pydantic contract;
- source/legacy ownership validation;
- duplicate-run/idempotency protection;
- any future policy or privacy checks.

Structured Outputs can later be added as a provider-neutral capability while
retaining the same Pydantic validation and provenance construction. The
current implementation deliberately keeps provider SDK features behind the
existing text-returning provider contract.
