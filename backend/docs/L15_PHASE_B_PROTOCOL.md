# L15 Phase B: local connection foundation

Status: implementation is local and disabled by default. No live chat UI, audio
browser endpoint, chat messages, ConversationTurns, memory effects, personality
effects or L14 provider tools are added by Phase B. No production deployment.

## Verified protocol references (2026-09-06)

- https://developers.openai.com/api/docs/models/gpt-realtime-2.1
- https://developers.openai.com/api/docs/guides/realtime-websocket
- https://developers.openai.com/api/docs/guides/realtime-conversations
- https://developers.openai.com/api/docs/guides/realtime-transcription
- https://developers.openai.com/api/docs/guides/realtime-vad
- https://developers.openai.com/api/docs/guides/realtime-costs

The installed Python OpenAI SDK is 2.54.0. The adapter uses the documented GA
WebSocket wire contract through websockets (tested with 17.1); no beta header.
Endpoint: `wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1`.
Only the backend sends `Authorization: Bearer <provider key>`. There are no
provider credentials in an HTTP response, browser WebSocket event or session row.

`session.update` specifies model `gpt-realtime-2.1`, reasoning effort `low`,
`output_modalities: ["audio"]`, PCM16 mono 24 kHz input/output, and the existing
user's `marin`/`cedar` preference (existing default: `marin`). Transcription model
is `gpt-live-transcribe`. Semantic VAD uses medium eagerness. Both VAD modes set
`create_response: false` and `interrupt_response: false`. Server VAD fallback
uses threshold 0.5, prefix 300 ms and silence 600 ms. There are no production
tools, persona instructions or prompt IDs. Tracing is disabled. A rejected voice
or configuration fails explicitly; there is no model/voice fallback.

The provider connection is ready only after `session.updated` confirms the
model, voice, format, reasoning, transcription model and response-control fields.
The provider maximum is documented as 60 minutes. Application maximum is 30
minutes, configurable only within 1–45 minutes and capped by the original access
token expiry. Reconnect never extends either boundary.

## Real-provider evidence

`L15_PROVIDER_ACCEPTANCE.json` records the original probes, including the first
Marin probe's incomplete finalization. `L15_PROVIDER_MARIN_RECHECK.json` records
the successful corrected Marin probe. The correction streams silence while
reading events concurrently, rather than stopping the input stream after two
seconds of silence. Provider configuration was not silently changed.

Both voices accepted the exact requested model/transcription combination. Both
VAD modes produced speech-start, speech-stop, input commit, transcript deltas and
completed transcript events without autonomously creating responses. Controlled
responses produced audio/transcript events, cancellation produced
`response.done` with status `cancelled`, and a disposable function schema
produced argument delta/done events. The fake tool was never executed and was
registered only on an explicit CLI probe response.

Only event names/order, numerical usage and non-private configuration are saved.
The spoken input is a disposable English test sentence. Multilingual quality,
language switching, transcription accuracy and browser playback are not certified
by this foundation probe. Language configuration remains automatic (no forced
language); Phase C/E must evaluate the requested languages separately.

Actual responses include `output_token_details.reasoning_tokens`, even though
the installed SDK's Realtime usage type omits that property. The adapter records
it only when supplied. Reasoning is a detail of reported output usage: counters
must not be added together as independent billable totals. Missing fields remain
unavailable. Cancelled response usage is recorded as interrupted, once per
response ID. Input transcription billing is separate; no fabricated audio-input
usage is inferred from microphone duration. No billing is implemented.

## HTTP and browser WebSocket protocol

All routes are gated by `REALTIME_ENABLED=false` by default. Existing auth/CORS
rules remain; a separate exact Origin check uses the configured explicit CORS
origin list. Wildcard/missing/foreign origins are denied for live requests.

`POST /api/v1/realtime/sessions`, authenticated:

- Existing chat: `{ "conversation_id": 123 }` only.
- Unsaved chat: `{ "legacy_id": 123, "mode": "rya" }` or mode `legacy`.
- Unknown fields (including role, permissions, tools) are rejected.
- Server resolves actor, conversation, Legacy, mode, role, active access and
  complete setup. Only active Legacy setup permits live connections.
- Response: session ID, opaque ticket, ticket expiry, session expiry, connection
  generation, nullable conversation ID and server state. No provider identifiers
  or credentials are returned. Do not log or persist the response ticket.

Connect to `/api/v1/realtime/connect` with the normal browser Origin. No query
string or credential subprotocol. First frame, within five seconds:
`{"type":"authenticate","ticket":"<opaque 43-character ticket>"}`.
Subsequent client messages are ONLY `{"type":"ping"}` and
`{"type":"end_call"}`. All extra properties, binary messages, audio frames,
provider-native events, transcript submissions and generic event envelopes fail.

Server events: `connecting`, `ready`, `pong`, `error`, `ended`. Ready includes the
session ID and server generation. There is no provider event passthrough.
The developer CLI exercises media directly through the backend adapter instead
of exposing an additional browser/media route in Phase B.

`POST /api/v1/realtime/sessions/{id}/reconnect` requires fresh application auth,
the same allowed Origin, actor ownership, retained access and a live reconnect
grace. It issues a replacement single-use ticket and increments generation.
It never reruns a turn, replays audio or resumes a provider session implicitly.

## Persistence, authorization and fencing

Migration `0016_realtime_sessions` follows 0015. It adds one metadata table; no
historical table or old migration changes. The configured application database
is not upgraded by tests or the CLI. Enable the flag only after explicitly
migrating the intended development/staging database.

The session row stores server UUID, actor/Legacy/conversation scope, role, state,
origin, timestamps, original auth lifetime, generation, lease owner/expiry,
reconnect deadline, safe end reason and ticket hash/expiry/use time. No audio,
transcript, prompt, memory, key or plaintext ticket is stored.

Tickets have 256 random bits, expire after 30 seconds, and are stored only as
SHA-256 digests. Database storage was chosen so consumption works across worker
processes. Actor-row serialization plus ticket state makes consumption atomic;
wrong-origin attempts cannot consume a valid ticket. Reissuing a ticket replaces
the hash and fences the older ticket. Consumed tickets cannot authorize a second
socket. Tickets are bearer capabilities during their short lifetime, so the
frontend must retain them only in memory.

V1 permits ONE active live session per actor globally. A nullable unique
`active_actor_id` slot enforces this across workers, covering different tabs,
conversations and unbound chats. Conflicts are explicit HTTP 409s. Text routes
remain unchanged; Phase C/D must serialize live/text turn admission before
enabling actual live turns.

States: authorized -> connecting -> connected. Normal end -> ended; provider/
protocol failures -> failed; access/logout -> revoked. Browser/provider transport
loss releases ownership and enters reconnecting for ten seconds. Expired leases
are fenced into reconnecting only until their original lease-expiry-plus-grace
deadline, then ended. Old workers cannot renew, finish or bind a replacement
generation. Connection lease: 15 seconds; active controller checks/renews at
most one second apart while idle, and checks before each approved control/event.
An application lifespan task runs bounded expiry cleanup every five seconds.
Cleanup never creates, steals, claims or reruns a ConversationTurn.

Transactions use actor then session lock order. No database transaction is held
across provider/browser awaits. Cancellation waits for an already-started DB
operation before reusing its session; teardown shields resource cleanup against
ASGI cancellation. Provider I/O has explicit connect/send/close timeouts.

Authorization is re-read on creation, connection, reconnect, heartbeat/control,
future sensitive execution and finalization. Retained ORM membership snapshots
are expired before checks. Existing collaborator and access-panel revocation
transactions also invalidate matching live rows. Logout identifies an actor
using a validated access token or existing refresh cookie, invalidates live
rows/tickets and retains a live-only old-token cutoff. Text JWT behavior is
unchanged. Active workers observe committed revocation within their next check;
already delivered content cannot be recalled. Logout cutoff metadata must be
retained at least until affected access credentials expire; any future retention
job must preserve that invariant. Timestamp precision may conservatively require
a freshly issued token from a later second for new live admission after logout.

## New Chat binding contract

Authorization/connect never creates a Conversation. Existing chats are bound at
authorization and scope cannot be changed through a route. An unbound call keeps
its selected Legacy/mode and nullable conversation ID.

The internal `bind_once` contract requires the actor and current connected
lease/generation, obtains the actor write lock, reauthorizes, and invokes a
staging factory only if the session is still unbound. It validates the resulting
conversation scope and flushes the binding. Caller commits this transaction with
Phase C's first-turn admission. Competing/retried factories cannot create two
conversations. This helper is not exposed to the browser; no Phase B route calls
it. PostgreSQL tests exercise it solely with disposable conversation rows.

## Limits, observability and safe failures

Defaults: five-second first-frame deadline; 30-second client idle timeout;
8 KiB control messages; 60 messages/second; six new sessions/minute/actor;
16-entry application mailbox; 16-frame provider socket queue; 1 MiB maximum
provider message; 32 KiB provider transport write high-water mark. Provider PCM
append calls accept at most 4,800 bytes (100 ms at 24 kHz mono PCM16). The browser
cannot send PCM in Phase B. Overflow fails and cleans up; no unbounded queues.
For deployment also configure Uvicorn `--ws-max-size 8192 --ws-max-queue 16` so
oversized browser messages are bounded before ASGI application decoding.

L14 telemetry gets fixed session/connect/ready/reconnect/end/revocation/replay/
queue/provider-disconnect events and provider-connect duration. Session UUID is
the correlation ID; no fabricated turn ID. Only fixed error/end categories and
numeric usage are exported. Raw exceptions, tickets, audio and transcripts are
excluded. An unexpected autonomous provider response fails the foundation socket.

Safe error codes include realtime_not_available, realtime_not_authorized,
realtime_setup_incomplete, realtime_ticket_invalid/expired/used,
realtime_session_expired, realtime_conflict, realtime_access_changed,
realtime_protocol_error, realtime_rate_limit, realtime_queue_overrun,
realtime_idle_timeout and realtime_provider_connection/failed.

## Deployment routing audit — read-only

On 2026-09-06 the running Nginx TLS virtual host for
`89-167-14-211.sslip.io` already forwarded Upgrade and Connection headers, used
HTTP/1.1, disabled buffering and had a 120-second proxy-read timeout. The checked-in
older template did not include Upgrade. A separate proposed location fragment
is supplied in `deploy/nginx-l15-websocket-location.conf`; it was NOT deployed.

Recommended eventual URL:
`wss://89-167-14-211.sslip.io/api/v1/realtime/connect`.
Use the backend TLS host directly. The frontend's current Vercel rewrite proves
HTTP routing, not this WebSocket path. No frontend/Vercel changes were made and
no production Realtime endpoint was called. A final deployed handshake test is
still required at the authorized release phase. Provider and browser pings keep
the existing 120-second read timeout from killing a healthy idle connection.

## Later-phase boundaries

Phase C: browser capture/resampling, finalized speech, L14 admission and atomic
New Chat binding. Phase D: response generation/playback, transcript persistence,
interruption and reconciliation. Phase E: existing L14 tools/prompts/memory/
personality/relationship/current information and collaborator DELETE policy.
None of those product behaviors are implemented in Phase B.
