# L14 Phase F release verification

This records local release gates against the L13 checkpoint. The earlier Phase
C/D/E reports describe their historical, uncommitted phases. Production backup,
deployment and acceptance evidence is reported separately after deployment.

## Release scope and frozen behavior

The L13 backend checkpoint is `dd246d658d308e5c558ff320d74e0056727048b5`;
the unchanged frontend is `f075f6a569db20bbfe5173d175fe0fa6f149029b`.
Of 141 backend files tracked at L13, 126 are byte-equivalent after newline
normalization. The 15 modified files are classified below. Historical migrations,
auth, cookies, CORS, setup logic, authorization, visitor identity, personality
derivation/selection and frontend files are unchanged. AST comparison confirms
the existing conversation creation, listing, rename/delete, daily prompt start,
visitor profile and source persistence functions are unchanged.

The only Phase F implementation fix supplies a message-only INFO logging fallback
for `app.conversation.telemetry` when Uvicorn has configured no application
handler or explicit logger level. It runs in the existing buffered consumer,
after startup has had the opportunity to configure logging. It does not configure
the root logger, change chat/provider behavior, or enable DEBUG stage/delta logs.
Two fresh-process tests exercise actual Uvicorn defaults and an existing host
configuration. Existing privacy and failure-isolation tests remain intact.

## File classification

All paths below are relative to `backend/`.

| Modified file | Intentional purpose |
| --- | --- |
| `app/api/routes/conversations.py` | Builder core, lifecycle, timing and usage integration |
| `app/api/routes/legacy_conversations.py` | Read-only persona core, lifecycle, timing and usage integration |
| `app/api/routes/legacies.py` | Replace content-bearing bootstrap exception logging with a fixed safe category |
| `app/api/routes/voice.py` | Existing STT/TTS/cache boundaries instrumented in Phase E |
| `app/models/__init__.py` | Register turn models |
| `app/schemas/chat.py` | Optional bounded client turn key |
| `app/services/legacy_persona.py` | Capture existing provider usage metadata |
| `app/services/memory.py` | Optional transaction participation and existing provider usage metadata |
| `app/services/personality_invalidation.py` | Time the existing invalidation function |
| `app/services/progression.py` | Optional transaction participation for atomic effect receipts |
| `app/services/rya.py` | Capture existing provider usage metadata |
| `app/services/voice.py` | Capture existing provider usage metadata |
| `app/services/web_search.py` | Capture existing provider usage metadata |
| `tests/test_personality_cadence_l13.py` | Follow the extracted selector import; retain assertions |
| `tests/test_personality_migration_l13.py` | Expect the new single migration head; retain preservation checks |

| New files | Classification |
| --- | --- |
| `alembic/versions/0015_conversation_turns.py` | Additive migration, no historical backfill |
| `app/models/turn.py` | Durable turn and effect receipt tables |
| `app/schemas/conversation_tools.py`, `app/schemas/observability.py` | Strict bounded contracts |
| `app/services/conversation_turns.py`, `builder_turns.py`, `persona_turns.py` | Shared core with separate builder/persona policies |
| `app/services/turn_lifecycle.py`, `turn_effects.py` | Claim ownership, guarded transitions, effect deduplication |
| `app/services/conversation_tools.py` | Exactly four internal read-only tools |
| `app/services/turn_observability.py`, `usage_accounting.py` | Passive timing, safe logging and usage hooks |
| `tests/test_conversation_turns_l14.py`, `test_turn_lifecycle_l14.py`, `test_turn_migration_l14.py` | Core, lifecycle and migration regression |
| `tests/test_turn_postgresql_acceptance_l14.py` | Real PostgreSQL concurrent workers and effects |
| `tests/test_conversation_tools_l14.py`, `test_turn_observability_l14.py` | Security, privacy, provider parity, failure isolation and benchmarks |
| `tests/test_telemetry_logging_release_l14.py` | Production logging fallback regression |
| `L14_PHASE_C.md`, `L14_POSTGRESQL_ACCEPTANCE.md`, `L14_PHASE_D.md`, `L14_PHASE_E.md`, `L14_PHASE_F.md` | Design and verification history |

No requirements change, frontend commit, credential, DB, runtime token, audio,
tunnel, control script or temporary acceptance file belongs in the release.
Temporary control scripts are kept outside both repositories and removed after use.

## Local verification

- Final full backend excluding the separately run PostgreSQL stress: **600 passed**,
  zero skipped, 120.79 seconds. The original 599-case baseline gains two release
  logging tests when the separate stress case is included: **601 total cases**.
- Frontend: **80 passed**. **144 Python** files compile and **48 JavaScript**
  files pass syntax checks.
- Final PostgreSQL stress: **25/25 iterations, 75 races passed**, two distinct
  backend connections, 723.88 seconds. Same-key totals: 25 claims, generations,
  assistants, memory receipts and activity receipts. Each terminal race has one
  winner; each effect race has one actual revision and contribution. Zero
  deadlocks/conflicts. Temporary database/role removed and tunnel stopped;
  the test role had write privileges on zero production tables.
- Owner/collaborator preparation, provider context/count parity, provenance,
  activity and memory effects pass. Visitor personal/general/mixed/fresh paths,
  style/relationship/source handling and zero canonical effects pass.
- Pending/streaming/completed/failed/interrupted transitions, replay/digest/scope
  conflicts, claimed processing and receipt races pass.
- Tool security covers forged arguments/scope, fresh access rechecks, malicious
  data, bounded outputs, active-only retrieval and style-only personality.
  There is no HTTP/provider tool exposure and no write tool.
- Content-free logs and low-cardinality dimensions pass; logger, sink and clock
  failures preserve chat. Usage remains best effort, measured only where supplied
  by the provider, with unavailable fields explicit; no durable billing ledger.
- Automated New Chat first/second send, persistence, unsent repeated New Chat,
  onboarding/bootstrap, daily-question browse/start, roles, L12 voice, L13 and auth
  regressions pass. No test was weakened or removed.
- Fresh SQLite and 0014 upgrades preserve historical rows. Additional SQLite
  0014 -> 0015 -> 0014 -> 0015 round trip retains a sentinel and integrity checks.
- The isolated PostgreSQL database migrates fresh to head, downgrades to 0014
  and re-upgrades to 0015 while retaining its historical sentinel. One head.
- Four bounded real-provider L13 builder smoke cases complete using fictional
  supplied examples and no DB writes. These remain qualitative smoke checks.

## Local latency comparison

100 paired samples per scenario, SQLite and fake providers, concurrent local
regression load. These are not production latency. Exact provider/SQL parity
checks provide stronger evidence than small timing differences.

| Measurement (ms) | Phase E median | Phase F median |
| --- | ---: | ---: |
| Owner preparation | 4.066 | 4.566 |
| Collaborator preparation | 4.200 | 4.702 |
| Visitor preparation | 6.610 | 6.640 |
| Owner instrumentation overhead | 0.650 | 0.698 |
| Collaborator instrumentation overhead | 0.621 | 0.706 |
| Visitor instrumentation overhead | 0.273 | 0.460 |
| Fresh-information instrumentation overhead | 0.248 | 0.334 |
| Memory wrapper versus direct service | 3.546 (Phase D) | 4.724 |
| Personality wrapper versus direct service | 5.147 (Phase D) | 5.369 |
| Relationship wrapper versus direct service | 3.074 (Phase D) | 3.872 |

Phase F preparation p95: owner 5.751, collaborator 5.803, visitor 9.010 ms.
Wrapper overhead p95: memory 5.869, personality 6.768, relationship 7.208 ms.
All benchmark gates pass. Memory wrapper overhead increased about 1.18 ms versus
Phase D; this includes Phase E usage instrumentation and is not an ordinary-chat
provider-call increase. The internal tools are not exposed to existing routes.
No material release-blocking latency regression was observed.

## Explicit security decision and acceptance boundary

L14 preserves the preexisting L13 analyzed conversational DELETE behavior for
collaborators. Dashboard DELETE remains owner-only. L14 does not expand it and
none of the four tools exposes writes. Before L15 exposes any write tool, make
an explicit owner-versus-collaborator deletion-policy decision and test it; do
not infer write authorization from the current read-only registry.

Other deferred work: recovery leases, resumable SSE, frontend creation-key UI,
durable usage accounting and L15 realtime/session/transport features.

Browser discovery returned no connected browser. Desktop/mobile UI, microphone,
editable transcription, auto-speak/Stop/replay, voice choices and silence-stop
acceptance require user confirmation. Production API checks cannot substitute
for these. **Do not create either final L14 tag before manual acceptance.**
