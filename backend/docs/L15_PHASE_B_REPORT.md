# L15 Phase B implementation and acceptance

Local implementation only. Realtime is disabled by default. No production
deployment, migration, service restart, commit, push or tag creation occurred.
No frontend product files changed.

| Item | Result |
| --- | --- |
| A. Baseline | Backend `5905e75ca0d9baf9ae52063dce29eb2f9d1a8a94`; frontend `f075f6a569db20bbfe5173d175fe0fa6f149029b`. Both were clean at start, on the L14 annotated checkpoint. Baseline: 599 backend passes + 2 environment skips; 80 frontend passes; configured DB/source at 0015. |
| B. Official wire | GA backend WebSocket, `gpt-realtime-2.1`, low reasoning, PCM16 mono 24 kHz, audio plus transcript, `gpt-live-transcribe`, controlled response creation. Official references and exact contract are in L15_PHASE_B_PROTOCOL.md. |
| C. Files | Inventory below. Backend application, tests, migration, isolated developer probes, acceptance documentation and an undeployed Nginx fragment. |
| D. Session model | One `realtime_sessions` metadata table. Actor/Legacy/mode/role, nullable conversation, origin, times, hashed ticket, generation and lease. No audio, transcripts, prompts, memories or provider secrets. |
| E. Migration | Additive `0016_realtime_sessions` after 0015. Fresh SQLite/PostgreSQL, upgrade and downgrade/re-upgrade verified. PostgreSQL preserved all 20 historical tables. Old migrations unchanged. |
| F. Authorization endpoint | Authenticated POST `/api/v1/realtime/sessions`. Existing conversation ID OR selected Legacy/mode. Server resolves scope and role, denies incomplete setup, rejects permission/tool/role injection. |
| G. Tickets | 256 random bits, 30-second maximum, single-use SHA-256 database representation; actor/session/origin/generation binding. No query-string credentials. Database storage supports concurrent workers. |
| H. Browser protocol | First `authenticate`; then only `ping` and `end_call`. Server: connecting/ready/pong/error/ended. No audio/transcript/provider event passthrough in Phase B. |
| I. Provider adapter | Isolated server-side adapter; accepted configuration checked before ready; bounded I/O; typed internal events; close/cancel; no actual L14 tools or persona prompts. |
| J. Voices | Existing preference reused; Marin default, Cedar supported; no substitution. |
| K. State machine | authorized → connecting → connected; reconnecting for transport loss; ended/revoked/failed terminal states. Server transitions only. |
| L. Lease/fencing | Actor transaction lock, generation and owner checks; 15-second renewable lease. Old generations cannot renew, finish or invoke future binding. |
| M. Expiry | 30-minute application maximum, configurable up to 45 minutes, capped by original access-token expiry. |
| N. Reconnect | Authenticated reconnect endpoint, 10-second grace, new ticket/generation, fresh access check, new provider connection. No audio replay or turn rerun. |
| O. Revocation | Logout and both existing membership-revocation paths invalidate live sessions. Current access is re-read during connections and at cleanup. Logout retains a live-only old-token cutoff; general text/API JWT behavior remains unchanged. |
| P. New Chat | Authorization and connection leave `conversation_id=null`; no empty conversation is created. |
| Q. Future binding | Internal lease-fenced `bind_once` obtains actor lock, creates at most one correctly scoped Conversation via a staging factory, and leaves commit to future first-turn admission. No Phase B route invokes it. |
| R. Existing conversations | Immediately bound after actor/mode/Legacy/access validation; no rebinding endpoint. |
| S. Parallel calls | One active realtime session per actor globally, enforced with a database unique slot. Text behavior unchanged; future text/live turn serialization remains Phase C/D. |
| T. Queues | 16-entry application mailbox, bounded provider read/write buffers, fail-and-clean-up on overflow. |
| U. Limits | 8 KiB browser control message; 60 messages/sec; 6 creations/minute/actor; 4,800-byte provider PCM frame; 5-second auth deadline; 30-second idle timeout. Uvicorn transport bounds are documented for release. |
| V. Origin | Explicit exact match against configured approved frontend origins; independently checked for WebSocket authorization. No CORS weakening. |
| W. Errors | Fixed realtime error categories; no raw provider/SQL errors in public responses. |
| X. Observability | L14 sink/correlation reused for session/connect/ready/reconnect/end/revocation/replay/overflow/disconnect, provider-connect duration and measured usage. No content telemetry. |
| Y. Fake harness | Deterministic bounded provider supports input, VAD, transcripts, output audio, cancellation, usage, stale generations and disconnects. Test-only; never a production fallback. |
| Z. GPT-Realtime-2.1 | Real connection/configuration accepted. |
| AA. Marin | Accepted; full semantic-VAD probe passed after correcting continued-silence streaming. |
| AB. Cedar | Accepted; server-VAD probe passed. |
| AC. gpt-live-transcribe | Accepted inside both tested 2.1 configurations; delta/completed events observed. Disposable English input only; multilingual quality is not certified. |
| AD. VAD | Semantic medium and server fallback accepted; neither autonomously created responses with `create_response=false`. |
| AE. Create/cancel | Controlled response creation succeeded; cancel ended via `response.done.status=cancelled`. |
| AF. Audio/transcripts | Input transcription, output audio and assistant transcript event sequences captured without content. |
| AG. Usage | Numeric totals, audio/text/cache details and actual reasoning-token details observed; absent fields unavailable; cancelled usage recorded as interrupted, once per response. |
| AH. Functions | Harmless per-response fake function emitted argument delta/done. No L14 tool exposed or executed. |
| AI. PostgreSQL | All five new concurrent-worker acceptance cases passed; separate PostgreSQL 18 cluster on loopback, separate databases, no production cluster access. |
| AJ. Cleanup/recovery | Normal end, transport failure, provider error, revocation, expiry, queue/rate failure and stale ownership tested. Teardown waits for outstanding DB work and shields cleanup from ASGI cancellation. Expiry sweeper never reruns turns. |
| AK. Routing | Running backend Nginx already supports Upgrade/HTTP1.1 with 120-second read timeout. Recommend direct `wss://89-167-14-211.sslip.io/api/v1/realtime/connect`. Vercel same-origin WebSocket routing is not relied on or certified. Proposed Nginx fragment remains local. |
| AL. Backend tests | **664 passed, zero skipped** across the final 663-case regression (225.06 seconds) and the unchanged L14 PostgreSQL stress case, which passed all 25 iterations / 75 races. Two existing dependency deprecation warnings remain. |
| AM. Frontend tests | 80 passed. |
| AN. Compile/syntax/migrations | 160 Python files compiled in memory; all 46 repository JS/MJS files passed `node --check`; migration tests passed; `git diff --check` clean. |
| AO. New Chat/onboarding | Existing regression suites passed; no frontend or onboarding/daily-question flow change. |
| AP. L12/L13/L14 | Existing voice, personality, tool security, auth and turn regressions passed. No realtime canonical-memory/activity/personality effects. |
| AQ. Security | Scope, origin, ticket replay/expiry/forgery, conflicting connection, stale lease, provider-event injection, frame/rate/queue limits and credential/content privacy tests passed. Changed files and fixtures contain zero configured-secret matches. |
| AR. Remaining blockers | No Phase B foundation blockers. The isolated PostgreSQL cluster and SSH tunnel were stopped and removed. Product audio capture, transcript persistence, barge-in, full L14 integration, mobile and release acceptance remain explicitly later phases. Before enabling realtime against a development DB, explicitly apply 0016 there. |
| AS. Working trees | Backend has intentional local uncommitted changes. Frontend is clean. Both HEADs/main branches remain unchanged. Configured local app DB remains 0015, source head is 0016, realtime flag remains false. |

## Evidence

- [Wire contract and operational boundaries](L15_PHASE_B_PROTOCOL.md)
- [Original provider probes](L15_PROVIDER_ACCEPTANCE.json)
- [Successful Marin semantic-VAD recheck](L15_PROVIDER_MARIN_RECHECK.json)
- [PostgreSQL migration preservation](L15_POSTGRESQL_MIGRATION.json)
- [Final verification summary](L15_TEST_ACCEPTANCE.json)

The original provider fixture deliberately retains the first timeout result;
the recheck supplies the successful result. Generated audio and provider
transcripts are not embedded in these event fixtures.

## Changed-file inventory

Existing integration points:

- `app/api/routes/{auth,access_management,collaborations}.py`
- `app/{config,main}.py`
- `app/models/__init__.py`
- `app/schemas/observability.py`
- `app/services/{turn_observability,usage_accounting}.py`
- `requirements.txt`
- `tests/{test_personality_migration_l13,test_turn_migration_l14}.py`

New foundation:

- `alembic/versions/0016_realtime_sessions.py`
- `app/api/routes/realtime.py`
- `app/models/realtime_session.py`
- `app/schemas/realtime.py`
- `app/services/{realtime_sessions,realtime_provider,realtime_runtime}.py`
- `tests/fake_realtime.py`
- `tests/{test_realtime_l15,test_realtime_provider_l15,test_realtime_migration_l15,test_realtime_postgresql_l15}.py`
- `scripts/{l15_realtime_probe,l15_postgresql_migration_probe}.py`
- `docs/L15_PHASE_B_PROTOCOL.md`, this report and the four evidence JSON files
- `../deploy/nginx-l15-websocket-location.conf`

No changes to `chat.js`, `legacy-chat.js`, `voice-chat.js`, frontend settings or
the production deployment were required.

L15 PHASE B COMPLETE — REALTIME SESSION FOUNDATION READY, L1-L14 PRESERVED
