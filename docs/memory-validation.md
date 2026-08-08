# Memory Validation

**Milestone:** Phase 6.5.4 — Validation and Deduplication  
**Scope:** Deterministic validation only  
**Persistence:** None  
**AI calls:** None

## Purpose

The Memory Validation layer sits between extraction and a future persistence
phase:

```text
Story Session / Conversation
  ↓
AI Memory Extraction
  ↓
MemoryCandidateCreate
  ↓
MemoryValidationService
  ↓
MemoryValidationResult
  ↓
Future human review and persistence
```

It validates, normalizes, verifies provenance, and compares a candidate with
existing memories from the same legacy. It never saves, merges, edits,
approves, rejects, supersedes, or deletes a memory.

## Components

### Validation service

Location:

`backend/app/services/memory/validation.py`

Public methods:

- `MemoryValidationService.validate_candidate()`
- `MemoryValidationService.validate_candidates()`

Inputs:

- one or more existing `MemoryCandidateCreate` objects or candidate mappings;
- the verified target `legacy_id`;
- existing memories available for that legacy;
- a `ProvenanceVerifier`.

Output:

- one `MemoryValidationResult` for each candidate.

Candidates are validated independently. The service does not merge candidates
with each other or mutate the supplied existing memories.

### Validation contracts

Location:

`backend/app/services/memory/validation_contracts.py`

`MemoryValidationResult` contains:

- `status`
- `recommended_action`
- a human-readable `explanation`
- independent `validation_confidence`
- the normalized candidate when structurally valid
- related existing memory IDs
- safe validation issues

The recommendation is advisory. It is not a database or review-state
transition.

### Provenance verifier

Location:

`backend/app/services/memory/provenance.py`

`ProvenanceVerifier` is a provider-neutral protocol. The included
`RegisteredProvenanceVerifier` checks candidates against trusted source records
loaded by the application.

`ProvenanceSourceRecord` supports:

- Story Session messages;
- normal conversation messages;
- future voice, photo, video, document, and manual source adapters through
  stable source locators.

The validator does not query a database itself. A future orchestration layer
must load owner-scoped sources and existing memories, register those trusted
records, then call validation.

## Validation statuses

| Status | Meaning |
|---|---|
| `accepted` | Structurally valid, source-grounded, and no deterministic duplicate or conflict was found |
| `duplicate` | The normalized claim exactly matches an existing same-legacy memory |
| `possible_duplicate` | Deterministic token comparison indicates likely equivalent wording |
| `possible_enrichment` | The candidate appears related and may add compatible detail |
| `contradiction` | A certain structured or explicit claim conflicts with an existing account |
| `invalid` | Structure, required fields, ranges, or provenance failed validation |
| `insufficient_information` | Source-grounded but too vague to stand as a useful memory |

## Recommended actions

| Action | Used for |
|---|---|
| `accept_candidate` | `accepted` |
| `do_not_persist` | exact `duplicate` |
| `review_link` | `possible_duplicate` |
| `review_enrichment` | `possible_enrichment` |
| `review_contradiction` | `contradiction` |
| `reject_candidate` | structurally or provenance `invalid` |
| `request_more_information` | `insufficient_information` |

These values describe what a later human-review workflow should consider. They
do not automatically persist or permanently reject anything.

## Evaluation order

The validator applies outcomes in this order:

1. normalize and validate the candidate contract;
2. verify every provenance source;
3. detect insufficient standalone information;
4. detect exact duplicates;
5. detect conservative contradictions;
6. detect possible enrichments;
7. detect possible duplicates;
8. accept when no deterministic concern exists.

This priority prevents a malformed candidate from being treated as a match and
ensures a clear contradiction is not hidden behind a similarity score.

Rejected existing memories are not used for duplicate or contradiction
classification. Candidate, approved, and superseded records remain available
for historical comparison.

Existing memories whose `legacy_id` differs from the target legacy are ignored.
The validation result can never link across legacies.

## Structural validation

The validator revalidates every input through the existing
`MemoryCandidateCreate` contract, even when the caller already supplies a
Pydantic object.

It therefore checks:

- required memory type;
- controlled category;
- nonblank title and summary;
- importance from 1–5;
- extraction confidence from 0–1;
- validated temporal and place details;
- participant structure;
- tag limits;
- one or more provenance records;
- source-specific provenance shape.

Invalid importance or confidence values are rejected rather than silently
clamped. This avoids concealing extraction defects.

## Normalization

Normalization is intentionally meaning-preserving:

- Unicode compatibility normalization;
- leading, trailing, and repeated whitespace removal;
- first-character capitalization for titles and summaries;
- repeated `!`, `?`, `;`, and `,` punctuation collapsed;
- four or more periods normalized to an ellipsis;
- category spaces and hyphens converted to lowercase underscores;
- tags trimmed, lowercased, and deduplicated;
- participant and place labels trimmed without changing proper-name casing;
- participant relationship labels normalized like category slugs;
- temporal source text whitespace normalized.

Provenance excerpts are not rewritten. They must remain verbatim source
substrings.

Extraction confidence is preserved. Validation adds a separate
`validation_confidence`.

## Provenance validation

Every provenance record must resolve against a trusted registered source.

For Story Sessions:

- `story_session_id` and `story_message_id` identify the source;
- the registered source must belong to the target legacy;
- the source must be user-authored;
- the claimed speaker must match;
- the excerpt must occur verbatim in the source content.

For normal conversations:

- `conversation_id` and `message_id` identify the source;
- the same legacy, speaker, and excerpt rules apply.

For future media, documents, and manual entry:

- a stable `source_locator` identifies the registered source;
- the locator is canonicalized so object key order does not matter;
- the registered source must belong to the same legacy;
- assistant sources remain forbidden;
- the speaker label and exact excerpt must match the registered record.

Possible provenance issue codes include:

- `invalid_source_reference`
- `missing_source`
- `cross_legacy_source`
- `assistant_source`
- `speaker_mismatch`
- `invalid_text_speaker`
- `fabricated_excerpt`

The validator returns safe explanations without including full conversations,
provider payloads, or credentials.

## Exact duplicate strategy

Exact duplicate detection compares meaning-preserving normalized summaries
within the same legacy.

Example:

```text
Existing:  Mom was born in 1968.
Candidate: mom   was born in 1968!!
Result:    duplicate
Action:    do_not_persist
```

Punctuation, capitalization, Unicode form, and spacing do not create a new
memory.

The existing memory IDs are returned for human inspection. The service does
not attach new provenance or modify the existing memory.

## Possible duplicate strategy

Possible duplicates use deterministic normalized token overlap. No embeddings,
vector database, external model, or semantic API is used.

The comparator:

- lowercases and tokenizes Unicode words;
- removes a small fixed stop-word set;
- applies a small transparent alias set such as `teacher`, `teaching`, and
  `taught` → `teach`;
- computes Jaccard token overlap;
- marks high-overlap, non-enriching claims as `possible_duplicate`.

Example:

```text
Existing:  Mom valued honesty and independence.
Candidate: Mom valued honesty & independence.
Result:    possible_duplicate
Action:    review_link
```

The candidate is not automatically linked or discarded.

## Possible enrichment strategy

A candidate may be a possible enrichment when:

- category matches;
- the subject overlaps;
- deterministic token overlap passes a conservative threshold;
- the candidate adds words, dates, places, or tags not present in the existing
  memory;
- no contradiction was found.

Example:

```text
Existing:  Mom worked as a teacher.
Candidate: Mom taught mathematics.
Result:    possible_enrichment
Action:    review_enrichment
```

The service does not combine these summaries. A later reviewer may preserve
both, link them, or create a revision with combined provenance.

## Contradiction strategy

Contradiction detection is deliberately conservative. It requires:

- the same legacy;
- the same normalized category;
- compatible subject identity;
- no source uncertainty on either account;
- incompatible explicit values in otherwise equivalent claim structures.

### Date contradiction

Explicit years are read from summaries and structured temporal references.

```text
Existing:  Mom was born in 1968.
Candidate: Mom was born in 1967.
Result:    contradiction
Action:    review_contradiction
```

The years must be disjoint, and the claim structure after replacing the year
must match or have very high deterministic overlap.

### Place contradiction

Place differences are considered contradictory only for singular identity
claims such as birthplace or origin, where the claim structures otherwise
match. Different travel or residence locations are not automatically treated
as contradictions.

### Uncertain accounts

Candidates with:

- `uncertainty_note`;
- `is_approximate = true`;
- `certainty` equal to `approximate`, `uncertain`, `disputed`, or `possible`

are not automatically labeled contradictions. They may be accepted or flagged
as possible enrichment/duplication for human review.

Contradictory accounts are never overwritten, merged, rejected, or
superseded by this service.

## Insufficient information

A candidate is `insufficient_information` when:

- the normalized summary has fewer than three meaningful tokens;
- it has no participants;
- it has no temporal, place, or category-specific structured details.

Example:

```text
Candidate: It was nice.
Result:    insufficient_information
Action:    request_more_information
```

Valid provenance remains attached to the normalized candidate so a later
workflow can ask for context without losing traceability.

## Confidence

Two independent confidence values are retained:

### Extraction confidence

Comes from Phase 6.5.3 and measures confidence that the source was interpreted
as intended. Validation never replaces it.

### Validation confidence

Describes confidence in the deterministic classification:

- exact structural/provenance invalidity: `1.000`;
- exact duplicate: `1.000`;
- conservative contradiction: `0.900`;
- possible duplicate/enrichment: deterministic overlap score;
- insufficient information: `0.900`;
- accepted with no deterministic match: `0.800`.

Validation confidence is not factual certainty and is not an approval score.

## Failure and mutation behavior

The validation service:

- accepts no database session;
- imports no CRUD module;
- invokes no AI service or provider;
- performs no network operations;
- mutates neither existing memories nor the original candidate;
- creates no contradiction group;
- creates no revision or supersession link;
- performs no automatic approval or permanent rejection.

An invalid result is returned as structured data rather than raised as a
business exception. Programming/configuration errors in source registration,
such as registering the same source key twice, raise `ValueError`.

## Tests

`backend/tests/test_memory_validation.py` performs no OpenAI calls and covers:

- normalization;
- independent extraction and validation confidence;
- exact duplicate detection;
- possible duplicate detection;
- possible enrichment detection;
- certain date contradiction detection;
- uncertain dates not being forced into contradiction;
- missing provenance sources;
- assistant-source rejection;
- cross-legacy provenance rejection;
- fabricated excerpt rejection;
- invalid range handling;
- insufficient information;
- cross-legacy memory isolation;
- future document-source validation.

## Future semantic similarity

The current validator is intentionally deterministic. A future semantic
comparator can implement a separate comparison interface and contribute
additional `possible_duplicate` or `possible_enrichment` evidence.

Future semantic support must:

- remain scoped to one legacy;
- preserve deterministic and provenance validation as mandatory gates;
- return review suggestions rather than automatic merges;
- retain both extraction and validation confidence;
- never hide contradictions;
- be versioned and testable;
- avoid changing `MemoryValidationResult`.

Adding embeddings later therefore does not require redesigning validation
outcomes, provenance verification, or persistence contracts.
