# L14 Phase D — server-scoped realtime tool contracts

Phase D adds four internal read-only contracts. Existing HTTP/SSE routes and
providers do not import, register or invoke them. All Phase B/C work remains
local and uncommitted; no frontend, migration, auth, voice or derivation change
is part of Phase D.

Validation status: COMPLETE. All 529 backend cases and 80 frontend cases passed,
with zero skipped. The isolated PostgreSQL stress run passed all 25 iterations;
its temporary database, role, tunnel and local credential/control files were
removed. Production schema, revision, commit and service process are unchanged.

## A. Files added in Phase D

- `app/schemas/conversation_tools.py`: strict model arguments, fixed error codes
  and size limits.
- `app/services/conversation_tools.py`: server context, immutable registry,
  dispatch, fresh authorization, compact bound results.
- `tests/test_conversation_tools_l14.py`: 86 security/regression/performance cases.
- `L14_PHASE_D.md`: this report and the future adapter contract.

No preexisting file was edited in Phase D. The earlier B/C changes remain intact.

## B–C. Execution context and dispatch

`TurnToolContext` is a frozen, server-only dataclass containing the Phase B
`TurnActorContext`, Phase C turn ID, processing claim and request digest. Its
factory requires the processing owner's `db.info` claim, a matching streaming
turn and a durably linked accepted user message. Merely reading another worker's
turn IDs cannot mint a valid context through the factory.

`REGISTRY` is an immutable mapping of exactly four names to strict argument
models. `ConversationTools` receives a session factory and existing memory/web
providers through dependency injection. It has no provider wire format, HTTP
endpoint, dynamic Python execution, transport connection or tool registration.

Future server adapter sequence, after authorized Phase C admission and durable
user linkage:

```python
context = TurnToolContext.from_turn(db, actor, db.info["active_turn_id"])
output = await tools.execute(context, tool_name, model_arguments)
payload = output.for_turn(context)
```

The context is not a model/request schema. The adapter must retain it server-side,
deliver results to that same active turn and keep claim tokens out of provider
payloads. It must not cache personal results for later sessions or promote
returned text to system/developer instructions. Existing text generation still
uses its direct services and transaction boundaries.

## D–G. Tool contracts

| Tool | Model arguments | Existing implementation reused | Result |
| --- | --- | --- | --- |
| `retrieve_legacy_memories` | `query`, optional `max_results` | `LivingMemoryService.retrieve_read_only`, existing query analysis/ranking | Active evidence references, canonical text, category, optional story reference and bounded entity/relationship labels |
| `get_legacy_personality` | None | L13 `select_personality_style` with current evidence, visitor language and assistant history | Availability, at most five phrasing cues, optional exact eligible signature expression; `kind=style_only`, `authorizes_factual_claims=false` |
| `get_visitor_relationship_context` | None | Current viewer profile, `visitor_evidence`, current active relationship evidence and `nickname_cadence_guard` | Claimed relationship, current verification and entity-match status, supported relationship labels, restricted forms of address; `identity_proof=false` |
| `get_current_information` | `query` | Existing FRESH routing, `minimize_search_query`, injected `WebSearchProvider` | Compact public digest and title/domain/URL/date sources; `personal_evidence=false`, `persisted=false` |

Memory uses existing ranking, never builder `retrieve()` embedding maintenance.
It reloads active rows before output, dropping evidence deleted/superseded during
an await. Persona private-address evidence is excluded for unverified visitors
using the existing persona boundary. It returns no embeddings, ORM objects,
source excerpts, whole profile or corpus dump.

Personality is selected by L13, with its stale/unsupported/conflicting evidence,
language and cadence rules. The wrapper does not derive traits. Missing/stale
style returns a successful unavailable style envelope, not invented guidance.
Expressions over the byte limit are omitted intact, never truncated into a
different expression. No profile JSON, evidence manifest, worker generation or
prompt is returned.

Relationship claims alone cannot verify identity or unlock family behavior.
Current supported evidence must agree with the preserved verified status and
claim. Missing/stale/conflicting evidence produces unverified restrictions.
Another viewer's profile is not queried; a matched entity in another Legacy
fails scope validation. Nickname output retains the existing two-assistant-turn
cooldown and allows at most one eligible nickname/use.

Current-information queries are deliberately conservative: the model's minimized
query must equal the minimized accepted user question. Only that server-derived
query goes to the provider. The existing classifier must still identify it as
public FRESH content without personal-memory needs. Known subject/viewer/person
entity names are excluded. Thus tool arguments cannot append a private memory,
identity or transcript to a search. Ambiguous/private queries safely return
unavailable/denied; there is no new classifier or general-purpose DLP claim.

Sources have the same fields as `MessageWebSource` attribution. URLs must use
HTTP(S), have a hostname, contain no credentials/control characters and fit the
limit. Domains are derived from URLs instead of trusting provider labels. At
least one usable source and a nonempty digest are required. A future completed
assistant-message pipeline may persist sources; tool execution never does.

## H. Capability matrix

| Server mode/role | Memory | Personality | Visitor relationship | Current information | Writes |
| --- | --- | --- | --- | --- | --- |
| Rya owner | Read if accepted turn needs memory | Denied | Denied | Denied | None |
| Rya collaborator | Read if accepted turn needs memory | Denied | Denied | Denied | None |
| Legacy viewer | Read if accepted turn needs memory | L13 selector | Current viewer only | Public FRESH only | None |

General-only turns cannot request personal retrieval by supplying a differently
worded tool query. Classification uses the accepted server-bound user message,
not a model-selected routing hint. No builder web/persona capability is added.

## I–J. Binding and revocation

Every invocation reads current turn, conversation, Legacy, user message and
membership/ownership rows in a fresh session. Mode, actor, role, Legacy,
conversation, source content, input mode, turn state, claim and request digest
must agree. It checks authorization again in a separate fresh transaction after
awaited provider work and before building output. Revoked access, terminal turns
and changed claims fail closed; no long-session authorization cache exists.

`ToolOutput` binds its encoded JSON to the immutable server context. Calling
`for_turn` with Turn B on a Turn A result returns `tool_scope_invalid`. Returned
payloads are copies; mutating one cannot alter the stored result. No scope IDs
or processing tokens are accepted in model arguments.

## K–M. Validation, output limits and safe errors

Strict Pydantic models reject extra fields, type coercion, boolean/fractional
result counts, empty queries, invalid controls, oversized strings and malformed
JSON values. Query whitespace is normalized. Personality and relationship accept
an empty object only. Unknown/write names are denied by the immutable registry.

| Bound | Maximum |
| --- | --- |
| Tool name | 64 characters |
| Query before and after normalization | 512 characters |
| Encoded argument object | 4,096 UTF-8 bytes |
| Memory count | 5; caller may request 1–5 |
| Canonical text per memory | 768 UTF-8 bytes, with truncation flag |
| Memory category/story reference | 80 bytes each |
| Entity labels per memory | 2; name 80 bytes, relationship 40 bytes |
| Personality cues | 5 × 200 bytes |
| Original expression | 256 bytes; omit if larger; maximum use 1 |
| Relationship labels | 3 × 40 bytes; claim 80 bytes; preferred name 128 bytes |
| Nickname | 1 exact eligible value, at most 64 bytes; maximum use 1 |
| Web sources | 3 |
| Source URL/title/domain/date | 1,024 / 180 / 255 / 40 bytes |
| Web digest | 1,600 bytes, with truncation flag |
| Complete successful JSON result | 8,192 UTF-8 bytes, including JSON escaping |

The total encoded-byte ceiling is independent of a provider tokenizer and also
bounds character volume. A pathological escaped result exceeding it becomes a
small safe failure instead of an oversized tool response.

Failures return only `{"ok":false,"error":{"code":"..."}}`, using
`tool_not_allowed`, `tool_invalid_arguments`, `tool_scope_invalid`,
`tool_data_unavailable`, `tool_current_info_unavailable` or `tool_internal_error`.
No exception text, SQL, validation input, provider payload, prompt, credential or
provider error kind is serialized. Task cancellation is not swallowed.

## N–S. Security findings and boundaries

- **N:** No save/edit/delete memory, personality update, activity, identity,
  Legacy creation, access-change or arbitrary execution tool exists.
- **O:** The existing collaborator analyzed-DELETE discrepancy remains unchanged.
  It requires an explicit security/policy decision before L15 exposes any
  model-directed write capability. These four tools cannot exercise that path.
- **P:** All successful envelopes carry `data_is_untrusted=true`. Malicious
  memory, web and relationship text remains a bounded JSON value with no policy
  effect. The L13 selector continues suppressing malicious signature expressions.
- **Q:** Cross-Legacy actor/conversation/message/claim combinations fail; identical
  query strings cannot override scope. Cross-Legacy matched entities also fail.
- **R:** Visitor sequences preserve every table, including memories/embeddings,
  revisions, personality profile/generation/jobs, activity/streak/progression,
  visitor profiles, messages/sources, turns and receipts. SQL observation permits
  only reads and selector savepoint controls. Stale embeddings remain unchanged.
- **S:** Contracts use injected domain providers and ordinary Python/JSON values.
  No OpenAI wire format, function-calling integration or realtime transport exists.

## T. Measured local overhead

Windows Python 3.12.10, in-memory SQLite fixture, four active Legacy memories,
existing deterministic providers, five warm-up pairs followed by 100 measured
pairs per tool. Direct calls include their context/evidence reads; wrappers add
validation, fresh authorization, post-await rechecks, compaction and serialization.
No external provider/network latency is included. These are local measurements,
not production PostgreSQL or large-corpus latency estimates.

| Tool | Direct median / p95 ms | Wrapper median / p95 ms | Paired overhead median / p95 ms |
| --- | --- | --- | --- |
| Memory | 2.031 / 3.492 | 5.688 / 7.278 | 3.546 / 5.226 |
| Personality | 4.772 / 6.774 | 9.840 / 11.713 | 5.147 / 7.256 |
| Relationship | 2.168 / 3.134 | 5.442 / 6.671 | 3.074 / 4.580 |

Reproduce with `python -B -m pytest tests/test_conversation_tools_l14.py -s -k
wrapper_overhead`. The test checks result parity and prints timings without a
fragile wall-clock assertion.

## U–AD. Validation and repository state

| Gate | Result |
| --- | --- |
| U. New Phase D cases | 86 passed; final targeted run 12.31 seconds |
| V. Backend suite | 529 passed, zero skipped across complementary invocations: 528 cases in 90.30 seconds plus the separate live stress case |
| W. Frontend suite | 80 passed, zero skipped |
| X. Phase C PostgreSQL stress | 25/25 iterations, 75 concurrent races passed, two distinct database connections; zero deadlocks/conflicts; isolated database/role/tunnel removed |
| Y. Compilation / syntax / migration | 139 Python files and 48 JavaScript files pass; full-suite migration tests pass; SQLite current/head and temporary PostgreSQL current/head are `0015_conversation_turns` |
| Z. New Chat / onboarding | Automated New Chat, first/second send, history persistence, onboarding, daily-question, collaborator and visitor regressions pass; no manual browser session was run |
| AA. L12 / L13 | Voice, personality derivation/style/cadence and frozen L13 JSON/SSE provider-input contracts pass |
| AB. Frozen files | SHA-256 comparison: all 204 preexisting backend/frontend source/test/migration files unchanged from the start of Phase D |
| AC. Concerns | No Phase D blocker. Existing collaborator DELETE policy decision remains before any future write-tool exposure; two existing dependency deprecation warnings remain |
| AD. Trees | Frontend clean; backend intentionally contains unstaged, uncommitted B/C work plus the four new Phase D files; index empty and branches unchanged |

The complete backend suite was rerun after the final claim-binding change.
The dedicated PostgreSQL stress test is excluded from that invocation solely to
avoid running its long loop twice; the existing live PostgreSQL lifecycle test
is enabled and passes in the full-suite invocation.

### Live PostgreSQL regression and cleanup

PostgreSQL 18.6 was reached through the already-authorized Hetzner SSH connection.
Actual local uncommitted code ran through a localhost-only tunnel against
`l14_test_phase_d_20260906_100018`, owned by the disposable role
`l14_test_role_d_20260906_100018`. No application code was deployed or copied to
the production directory. The database was migrated normally from empty through
`0015_conversation_turns`; no stamping or production migration occurred.

All 25 iterations passed their same-key admission, different-digest rejection,
terminal CAS, stale-state concurrent effect finalization and cross-scope cases.
Same-key totals were 25 claims, generations, assistants, memory receipts and
activity receipts. Terminal races had 25 winners. Separate effect races produced
25 actual revisions and contributions. Two distinct PostgreSQL backend
connections were observed; database deadlocks/conflicts were both zero.

The role had no superuser, create-database, create-role, replication or RLS-bypass
powers, and write privileges on zero production tables. The following read-only
production checks matched before, during and after the test and after removal:

| Production property | Unchanged value |
| --- | --- |
| `legarya` revision | `0014_legacy_personality` |
| Deployed backend HEAD | `dd246d658d308e5c558ff320d74e0056727048b5` |
| Service PID | `705190` |
| Service active since | `2026-09-06 00:42:28 UTC` |
| Schema-only SHA-256 | `d28d26f9c0d3cafe0af70ec710ac7f7edd765592f4d0e347a6062aaf0c35c48d` |

For this run the schema dump was stripped of surrounding whitespace, then its
random PostgreSQL `restrict`/`unrestrict` lines were removed and remaining lines
joined with LF, without a final LF. Comparisons use the same normalization.

Cleanup verified database and role absence in PostgreSQL catalogs. The test's
random schema was cleaned by its `finally` block. Tunnel PID 22180 was stopped
only after its executable and exact localhost forwarding command were checked.
Temporary local scripts, credentials and the frozen-file comparison manifest
were removed after the final checks. No test credentials are retained here.

Stable backend HEAD/main/origin/main/L13 checkpoint:
`dd246d658d308e5c558ff320d74e0056727048b5`.

Stable frontend HEAD/main/origin/main/L13 checkpoint:
`f075f6a569db20bbfe5173d175fe0fa6f149029b`.

No commit, push, L14 tag, production migration, deployment or service restart is
part of this phase. Existing local SQLite remains at `0015_conversation_turns`;
21 tables remain readable and `PRAGMA quick_check` returns `ok`.

L14 PHASE D COMPLETE — REALTIME TOOL CONTRACTS READY, L1-L13 PRESERVED
