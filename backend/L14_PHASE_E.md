# L14 Phase E — passive observability and usage hooks

Phase E adds backend timing, safe structured events and provider-neutral usage
hooks. Message transactions, provider requests, retrieval/selection algorithms,
JSON/SSE contracts and frontend behavior remain unchanged. No migration, billing
calculation, realtime transport or session behavior was added.

Status: COMPLETE. Backend: 599 passed, zero skipped across the final full-suite
and separate stress invocations. Frontend: 80 passed. All 25 live PostgreSQL
stress iterations passed; the temporary database, role, tunnel and local control/
credential files were removed. Production checks remained unchanged.

## A. File scope

New files:

- `app/schemas/observability.py`
- `app/services/turn_observability.py`
- `app/services/usage_accounting.py`
- `tests/test_turn_observability_l14.py`
- `L14_PHASE_E.md`

Targeted integration modifies 16 existing files:

- Orchestration: `conversation_turns.py`, `builder_turns.py`, `persona_turns.py`,
  `conversation_tools.py`, `turn_lifecycle.py`, `turn_effects.py`.
- Existing route boundaries: `api/routes/conversations.py`,
  `api/routes/legacy_conversations.py`, `api/routes/voice.py`.
- Confirmed unsafe bootstrap exception log: `api/routes/legacies.py`.
- Usage metadata extraction only: `services/rya.py`, `services/legacy_persona.py`,
  `services/memory.py`, `services/web_search.py`, `services/voice.py`.
- Timer around the existing invalidation call: `personality_invalidation.py`.

Provider request construction, prompts, return types and error contracts were not
refactored. Memory ranking, L13 derivation/selector, visitor identity, schemas,
migrations, progression logic and frontend assets were not edited.

## B. Observation context

`TurnObservation` uses a task-local `ContextVar`. It carries a server-generated
request UUID, Phase C `conversation_turn_id`, a generation-attempt UUID, fixed
mode/role/input/route categories, counters and monotonic timings. `session_id` is
an optional UUID slot only; it creates no session behavior. The context is
rebound around deferred SSE iteration and reset afterward, including cancellation.

Completed replay events correlate with the original turn ID and have no new
generation attempt. Provider invocation attempt IDs are separate from the turn's
generation-attempt correlation. No raw Phase C claim token or request digest is
logged. Request IDs are generated here because the existing core had no request
ID contract to reuse; no public response/header change was made.

## C–D. Timings and first response

Distinct stage timers cover preparation, history loading, classification, memory
analysis/retrieval, relationship context, L13 personality selection, current-info
lookup, main generation, assistant persistence, post-turn effects, memory effect,
activity effect and triggered personality invalidation. Each Phase D tool emits
its own fixed-name duration and outcome. Voice provider calls have separate timers.

The accepted marker is captured after durable Phase C admission, before claiming
and preparation. `accepted_to_preparation_ms` measures the subsequent wait.
`provider_start_ms` records the accepted-to-generation-start offset.

For streaming, `provider_time_to_first_delta` measures provider start to the first
nonempty text delta. `time_to_first_text_delta` measures accepted-to-first-delta
availability at the server. Neither claims to measure arrival at the browser.
Nonstreaming records `provider_first_result_ms`; no first delta is fabricated.
Streaming generation duration is the observed iterator lifetime, including any
consumer backpressure while yielding; it is not a provider-internal compute metric.

`time_to_assistant_durable` is captured after the existing commit/refresh boundary.
`time_to_durable_completion` is captured after scheduled post-turn work succeeds,
or at assistant persistence for the read-only persona path. Later SSE delivery
delay cannot inflate that durable marker. Failed post-processing leaves assistant
durability observable without inventing a completed post-processing timer.
The turn summary's outer duration separately covers the whole observed invocation.
Nested stage timings describe components and should not be summed twice.

## E–H. Dimensions, taxonomy, logging and privacy

Metric dimensions are allowlisted: mode, role, route, input mode, outcome,
personality availability, fixed tool name and provider kind. User/Legacy/
conversation IDs, names, email, query strings and memory references are never
metric labels. UUIDs and positive turn IDs exist only in correlation fields.

Stable internal errors include authorization/access changes, invalid scope,
conflict/already-processing, provider connection/timeout/failure, memory retrieval,
personality/current-info unavailability, invalid/denied tools, persistence,
post-processing, cancellation and unknown internal errors. Categories are not
substituted for existing useful client errors. Provider/HTTP exception text,
unchecked provider kinds, SQL and bound parameters are never serialized.

`app.conversation.telemetry` emits safe JSON messages and also attaches the same
dictionary as `LogRecord.telemetry`. DEBUG provides stage/tool details; INFO
provides completion/replay and provider-usage events; WARNING signals degraded
components or failed usage export; ERROR records failed turns or failed work
after an assistant was already durable. A completed Phase C row can therefore
have a completion event carrying a post-processing error at ERROR level.

The existing logging configuration controls visibility. Enable this logger at
INFO for summaries, or DEBUG for stage detail, using the deployment's existing
handlers. No root logger, production handler or collector was reconfigured here.
There is no per-delta logging: only the first timestamp and an aggregate count.

The default sink has a bounded queue of 1,024 events and a daemon consumer.
Request paths enqueue without waiting for handlers. Full queues drop telemetry;
broken handlers increment a drop counter without retries or exception dumps.
There is no SQL, export I/O or logger callback inside a message transaction.

The audit found unchecked `exc.kind` interpolation in builder/persona/voice
logging and `logger.exception` in Legacy setup bootstrap. These specific logs now
use fixed categories; the bootstrap HTTP status/code/message is unchanged. The
unrelated invitation-email exception logger is outside this phase and unchanged.
New events contain no user/assistant text, memory/profile/tool bodies, prompts,
audio, transcripts, access/API tokens or credentials. No raw stack traces are
added to this telemetry stream.

## I–N. Usage contract, failure behavior and idempotency

Frozen `ProviderUsage`, `UsageValue` and `UsageEvent` structures support text
input/output/cached tokens, duration, characters, audio input/output tokens,
tool-call usage and configured model metadata. Every usage quantity carries
`measured`, `estimated` or `unavailable`; the default is unavailable. Negative,
nonfinite, malformed and inappropriate fractional token counts are discarded.
No tokens, audio duration, costs or prices are inferred from content.

Orchestration wraps actual logical provider invocations. Existing SDK providers
only report metadata already returned: Responses input/output/cached tokens,
embedding prompt tokens and STT token/duration usage where supplied. Known fields
were checked against the installed SDK types. SDK responses are never dumped.
TTS bytes currently supply no billed usage, so those values remain unavailable.
The observed request count is explicitly measured; cache hits record zero requests.
It counts application provider invocations, not invisible SDK transport retries.

`UsageSink.record` is a nonblocking enqueue contract. The default routes safe
usage events through buffered structured logging. `BufferedUsageSink` is provided
for a potentially slow external consumer, with a bounded queue (default 256),
safe failure warnings and no retries. Neither sink uses the assistant session or
transaction. Sink, serialization, clock and logging failures are fail-open.

No durable ledger/table is needed for L15 hook preparation. This is best-effort
telemetry, not billing or guaranteed accounting: process crashes, queue overflow
or exporter failure may lose observations. A future accounting consumer must
choose its durability and retention policy before relying on these events.

Each actual invocation gets a distinct attempt ID. Repeated usage snapshots from
one invocation update its snapshot and emit one event on exit. Known provider
request IDs are hashed and yield stable event IDs for downstream deduplication;
unknown IDs use the invocation attempt ID. Separate actual attempts retain
separate attempt identities, even when a provider returns the same request ID.
Completed Phase C replay performs no provider call and emits no fake usage.
The hooks do not promise durable deduplication without a downstream consumer.

Usage is emitted on success, failure and interruption, including metadata reported
during generator cleanup. If a provider fails without supplying usage, quantities
remain unavailable. A valid completed provider result can coexist with a later
persistence failure; correlation joins it to the actual turn outcome.

## O–Q. Tools, voice and personality

All four Phase D tools emit fixed tool name, duration and safe success/failure/
interruption category. Tool results and model arguments are not logged. Access
revocation emits `access_changed`; the existing safe tool response is preserved.
The four read-only capabilities, server binding and argument/result limits remain
unchanged. Visitor telemetry adds no canonical/profile/activity writes.

L12 STT, TTS and TTS cache-hit hooks retain the existing request, pronunciation,
return, cache and frontend behavior. Client-supplied recording duration is not
treated as provider-measured usage. Failed usage export cannot change the returned
transcript or audio response.

L13 invalidation duration is observed around its existing transactional function.
Derivation, selector and standalone worker behavior are unchanged. Optional worker
rebuild/status/discard instrumentation is deferred to keep this phase within the
conversation core; no worker task or queue behavior was added.

## R–S. Reproducible local benchmarks and overhead

Windows Python 3.12.10, in-memory SQLite fixtures, existing deterministic fake
providers and an in-memory capture sink. Each scenario uses five warm-up pairs
and 100 measured enabled/disabled pairs. These measure incremental collection
overhead with hooks already present; they are not pre-L14, external-provider or
production PostgreSQL latency measurements. Negative differences are noise, not
speedup claims. The p99 values are sample tails from 100 pairs, not a production SLO.

| Scenario | Disabled p50 / p95 / p99 ms | Enabled p50 / p95 / p99 ms | Paired median overhead ms |
| --- | --- | --- | --- |
| Owner | 14.460 / 15.654 / 17.482 | 15.126 / 16.490 / 17.534 | 0.650 |
| Collaborator | 14.913 / 15.761 / 18.283 | 15.646 / 16.513 / 17.482 | 0.621 |
| Visitor personal | 16.714 / 18.114 / 18.885 | 17.036 / 18.430 / 20.822 | 0.273 |
| Visitor fresh | 16.581 / 17.444 / 17.625 | 16.795 / 17.720 / 18.167 | 0.248 |
| Memory tool | 7.599 / 9.546 / 31.665 | 7.251 / 9.144 / 31.555 | -0.358 |
| Personality tool | 11.134 / 12.600 / 34.358 | 11.377 / 12.358 / 23.754 | 0.092 |
| Relationship tool | 7.160 / 18.744 / 30.486 | 7.026 / 8.963 / 11.626 | -0.197 |

Representative enabled stage distributions (p50 / p95 / p99 milliseconds):

| Scenario / stage | Distribution |
| --- | --- |
| Owner preparation | 4.066 / 5.855 / 7.005 |
| Owner assistant persistence | 1.752 / 3.192 / 3.633 |
| Owner post-turn effects | 2.134 / 3.948 / 4.586 |
| Collaborator preparation | 4.200 / 5.834 / 5.954 |
| Collaborator assistant persistence | 1.819 / 3.035 / 3.518 |
| Collaborator post-turn effects | 2.157 / 3.494 / 4.053 |
| Visitor classification | 0.227 / 0.305 / 0.399 |
| Visitor memory retrieval | 0.853 / 1.466 / 1.696 |
| Visitor personality selection | 1.890 / 2.950 / 3.466 |
| Visitor relationship context | 1.285 / 1.654 / 1.925 |
| Fresh current-info fake provider | 0.057 / 0.093 / 0.108 |
| Fresh main fake provider | 0.029 / 0.036 / 0.043 |

The tests also print all observed stage distributions. Reproduce with:

```text
python -B -m pytest tests/test_turn_observability_l14.py -s -k distributions
```

The coarse timing gate requires enabled median latency to remain below twice the
same run's disabled p95. Exact SQL statement sequences and provider-call counts
must also match with collection enabled/disabled for all three roles. Those
count/parity gates are stronger than a fragile fixed millisecond assertion.
Deterministic fake-clock tests verify preparation, provider first-result/delta,
generation, persistence, post-processing and tool timing. Controlled 25 ms fake
provider delay is preserved through owner, collaborator and visitor routes.

## T–AD. Verification and final state

| Gate | Final result |
| --- | --- |
| T. New Phase E tests | 70 cases, including seven benchmarks; all pass in the final full suite |
| U. Backend | 599 passed, zero skipped: 598 cases in 120.84 seconds, plus the separate live stress case |
| V. Frontend | 80 passed, zero skipped; unchanged |
| W. PostgreSQL | 25/25 iterations, 75 races, two distinct connections; passed in 687.55 seconds; zero deadlocks/conflicts |
| X. Compile/syntax/migrations | 143 Python files and 48 JavaScript files pass; migration tests pass; single head/current `0015_conversation_turns` |
| Y. New Chat/onboarding | Automated New Chat, first/second send, refresh/history persistence, onboarding, daily-question, collaborator and visitor gates pass |
| Z. L12/L13 | Voice/pronunciation/cache, personality/selector/cadence and existing L13 JSON/SSE provider-input regressions pass |
| AA. Security/logging | Privacy, sink/clock failure, real JSON logging, cancellation, usage identity, tool revocation, visitor no-write and exact SQL/provider-count checks pass |
| AB. Frozen files | 230 of 246 preexisting repository files remain byte-for-byte unchanged; only the 16 scoped integrations differ |
| AC. Blockers/concerns | No Phase E blocker; best-effort export, deferred worker metrics and the preexisting collaborator DELETE decision are documented limitations |
| AD. Working trees | Frontend clean; backend intentionally contains unstaged/uncommitted B+C+D+E work; index empty; branches and L13 checkpoint unchanged |

The full suite was rerun after the final durable-timing correction. The existing
live PostgreSQL lifecycle test is enabled in that invocation. The longer stress
case runs separately to avoid repeating its 25-iteration loop unnecessarily.
Two existing dependency deprecation warnings remain. Product gates are automated;
no manual browser session was run.

The Phase E test module contains 70 cases, including seven distribution benchmarks.
All original tests remain unedited. Baseline hashing found 230 of 246 preexisting
repository files unchanged; the 16 differences are the integrations listed above.
Frontend, migrations, ORM models, derivation/selector, identity, auth architecture,
progression and preexisting tests are unchanged.

The remaining design limitations are intentional: usage export is best-effort,
no costs or unavailable usage are invented, and optional standalone worker metrics
are deferred. The existing collaborator analyzed-DELETE finding remains a policy
decision before any future model-directed write tool. Phase E exposes no writes.

### PostgreSQL isolation, production checks and cleanup

The already-authorized Hetzner connection was used only to provision
`l14_test_phase_e_20260906_102953` and role `l14_test_role_e_20260906_102953`.
Actual local uncommitted code used a localhost-only SSH tunnel. The temporary
database was migrated from empty to 0015 through the normal chain. No application
code was deployed or copied into the production directory.

The role had no superuser, create-database, create-role, replication or RLS-bypass
powers and write privileges on zero production tables. The stress run produced
exactly 25 successful claims, generations, assistants, memory receipts and
activity receipts in its same-key races. Terminal races produced 25 winners;
separate concurrent effect races produced 25 actual revisions and contributions.
Different-digest rejection and actor/conversation/Legacy isolation passed each
iteration. Test database deadlocks and conflicts were both zero.

Read-only production checks matched before, during and after testing/removal:

| Property | Unchanged value |
| --- | --- |
| `legarya` migration revision | `0014_legacy_personality` |
| Deployed backend HEAD | `dd246d658d308e5c558ff320d74e0056727048b5` |
| Service PID | `705190` |
| Service active since | `2026-09-06 00:42:28 UTC` |
| Normalized schema-only SHA-256 | `d28d26f9c0d3cafe0af70ec710ac7f7edd765592f4d0e347a6062aaf0c35c48d` |

Schema normalization matches Phase D: trim surrounding whitespace, remove
PostgreSQL's randomized restrict/unrestrict lines, join remaining lines with LF
without a final LF. Production was only read for these checks.

The stress test removed its random schema in `finally`. Database and role absence
were verified in the PostgreSQL catalogs. Tunnel PID 14728 was stopped after
checking the executable and exact forwarding command. Local temporary scripts,
credentials and the baseline comparison manifest were removed. No credentials
are retained in this report.

Local SQLite remains healthy at 0015: 21 tables readable, five total rows and
`PRAGMA quick_check = ok`. Its existing data was not rebuilt or migrated in E.

Backend HEAD/main/origin/main/L13 checkpoint:
`dd246d658d308e5c558ff320d74e0056727048b5`.

Frontend HEAD/main/origin/main/L13 checkpoint:
`f075f6a569db20bbfe5173d175fe0fa6f149029b`.

No commit, push, L14 tag, production migration, deployment or service restart
occurred. Phase B+C+D+E remain local.

L14 PHASE E COMPLETE — OBSERVABILITY AND USAGE HOOKS READY, L1-L13 PRESERVED
