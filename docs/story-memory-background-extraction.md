# Story Memory Background Extraction

**Milestone:** Phase 6.5.7

## Persisted Legacy integration

`POST /api/v1/legacies` synchronizes a temporary frontend Legacy using an
owner-scoped `client_correlation_id`. The unique
`(owner_user_id, client_correlation_id)` constraint makes retries idempotent
without treating display name as identity. The returned integer `legacy_id` is
the only identifier used for Story and Memory APIs. Listing and retrieval are
authenticated and owner-scoped.

The browser UUID remains a local correlation key only. It is never ownership
proof and is not accepted in integer Legacy route parameters.

## Story Session and message lifecycle

Creating a Story Session resumes the newest `in_progress` or `paused` session
for the same owned Legacy and chapter, or creates a new one. Completed sessions
are not silently reused. A new persisted session may be created when the user
continues that chapter later.

Application-visible user and Berry messages are stored in `story_messages`.
The server assigns sequence numbers while locking the Story Session. A nullable
`client_message_id`, unique per session, makes frontend submission retries
idempotent. Client sequence numbers are never accepted.

The persisted streaming flow is:

1. authenticate and verify Legacy/Story Session ownership;
2. persist and commit the user message;
3. copy ordered visible history into provider-neutral contracts;
4. close the read transaction before provider streaming;
5. reuse the existing Story Guide AI stream;
6. persist Berry only after a complete visible response.

On stream failure the committed user message remains. No fabricated complete
assistant message, partial provider payload, hidden reasoning, prompt, or
system instruction is stored. Extraction is not triggered.

## Completion and extraction trigger

Completion is explicit through the Story Session `complete` endpoint. It sets
`completed`, records `completed_at`, creates or reuses an extraction run, and
commits before FastAPI schedules the background task.

The sole automatic trigger is `session_completed`. The idempotency boundary is:

```text
Story Session ID + highest persisted StoryMessage sequence + trigger type
```

Repeated completion at an unchanged boundary reuses the run. Additional
persisted messages create a new boundary and permit a new run. The storage
pipeline receives the boundary and ignores later messages, ensuring the
durable run represents exactly the committed source version.

## Background execution and session lifetime

FastAPI `BackgroundTasks` is used because the current application is a
single-process deployment with no queue infrastructure. Only immutable
integer IDs are passed. The task:

- creates its own `SessionLocal`;
- reloads the run, Legacy, and Story Session;
- rechecks ownership and same-Legacy scope;
- invokes the existing cached, stateless `MemoryStoragePipeline`;
- records counts or a controlled error category;
- closes its session in `finally`.

The request session and ORM objects are never passed to background work.
Pipeline candidate-level atomicity and fingerprint uniqueness remain intact.
All created Memories remain `candidate`.

If any eligible candidate fails its atomic persistence requirements, the run
is marked `failed` with `partial_persistence_failure`. Successfully committed
candidates remain safe, and a manual retry can reprocess the same boundary;
fingerprints prevent exact duplicates.

## Extraction-run tracking and retry

Migration `0004_story_background_extraction.py` adds
`memory_extraction_runs` with pending, running, completed, and failed states;
attempt count; source boundary; safe error code; counts; and timestamps.
Transcripts, excerpts, provider payloads, prompts, and raw exceptions are not
stored.

The existing AI retry policy handles bounded transient provider retries during
one execution. Failed durable runs can be manually retried after owner
verification. Pending, running, and completed runs cannot be duplicated or
retried. Ownership and cross-Legacy failures are classified as permanent and
are not automatically retried. There is no infinite retry loop.

## Endpoints

- `POST /legacies`
- `GET /legacies`
- `GET /legacies/{legacy_id}`
- `POST /legacies/{legacy_id}/story-sessions`
- `GET /legacies/{legacy_id}/story-sessions/{story_session_id}`
- `POST /legacies/{legacy_id}/story-sessions/{story_session_id}/messages/stream`
- `POST /legacies/{legacy_id}/story-sessions/{story_session_id}/complete`
- `GET .../extraction-runs/{run_id}`
- `POST .../extraction-runs/{run_id}/retry`

All require authentication and return generic not-found responses for
inaccessible resources.

## Transactions and observability

Legacy creation, Story Session creation, each visible message, completion, and
run-state changes commit independently. A failed extraction cannot roll back
the saved Story. Structured logs contain IDs, boundary, status, attempt,
counts, duration from the pipeline, and controlled failure category—never
story content or secrets.

## Infrastructure limitations

FastAPI background tasks are not a durable distributed queue: process shutdown
after the response can leave a pending run. The explicit status and retry API
make this visible and recoverable, but Phase 6.5.8 should verify startup
recovery and end-to-end failure behavior before production scale.

Python is still unavailable on this workstation, so the new 30-test backend
suite was statically inspected but not executed. Use the documented Python
3.10+ environment and run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p "test_*.py"
```

Companion retrieval, prompt injection, approved-memory browsing, embeddings,
semantic search, merging, and distributed jobs remain out of scope.
