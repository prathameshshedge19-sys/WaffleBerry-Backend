# L15 Phase D live assistant output contract

Phase D is local and behind the existing disabled-by-default realtime flag. The
source and configured local migration head remain `0016_realtime_sessions`.
No new migration or durable generation table is required: the L14 turn claim is
the active generation identity, and the live session lease fences the worker.

## Response ownership and context

A newly admitted, non-replayed provider final starts one generation. Preparation
uses `realtime_transcripts.prepare_admitted` and the existing L14 builder/persona
preparation in a separate SQLAlchemy session. Preparation rolls back its read
transaction and reauthorizes; no canonical memory, personality, activity, or
TurnEffect writes are committed. Phase E tools and post-turn effects are absent.

The provider request is out-of-band (`conversation: none`) with explicit prepared
L14 messages. Consequently, unadmitted input and interrupted assistant output in
the provider's default conversation cannot silently enter the next response.
Builder instructions reuse `RYA_SYSTEM_PROMPT`. The adapter rejects a second
request with the same generation identity. Session configuration remains
`gpt-realtime-2.1`, low reasoning, mono 24 kHz PCM, and the user's Marin/Cedar
preference. Both autonomous VAD response creation and interruption stay disabled.

Every output event binds `session_id`, connection `generation`, `turn_id`,
`active_generation_id` (the L14 claim UUID), and `response_id`. `assistant_thinking`
may have a null response ID; the matching provider `response.created` metadata
binds it once. Unexpected new provider generations fail closed. Retired output,
transcripts, and function events cannot attach to another turn. No functions are
exposed or executed in this phase.

## Audio and receipts

Server events:

| Event | Additional fields and meaning |
| --- | --- |
| `assistant_thinking` | Admitted turn is preparing; response ID may be null. |
| `assistant_started` | Matching provider response ID is now bound. |
| `assistant_audio` | Zero-based contiguous `sequence`, base64 little-endian PCM16 `pcm`; mono 24 kHz. |
| `assistant_audio_end` | Final `sequence`, total `samples`, fresh 43-character `seal`. Sent only after successful provider completion, audio completion, and a nonempty final transcript. |
| `assistant_completed` | Durable assistant `message_id` and conversation/turn binding. |
| `assistant_interrupted` | Terminal turn with no assistant message. |

Browser controls include the full binding above:

| Control | Additional fields |
| --- | --- |
| `playback_started` | `sequence: -1`, `samples: 0`; emitted at the first scheduled playback start on the AudioContext clock. |
| `playback_progress` | Fully played cumulative sequence and sample count, checked against the server's outstanding frame ledger. |
| `playback_drained` | Exact final sequence, sample count, and seal. |
| `interrupt` | Current binding; may precede provider response ID assignment. |

The browser schedules AudioBufferSource nodes in order on the AudioContext clock.
Each source must end and its output-latency allowance must elapse before progress
is acknowledged. The final acknowledgement also waits for every queued source.
Suspended audio fails conservatively. Late callbacks are fenced before sources
are stopped. A bare finished boolean, another generation's seal, wrong totals,
or an acknowledgement before the server's terminal seal cannot complete a turn.

These receipts authenticate the application's playback protocol; no browser
receipt can cryptographically prove that a person heard their hardware output.
Physical hearing and immediate barge-in therefore have a separate human gate.
No elapsed-time estimate of an assistant text prefix is ever stored.

## Durable completion and interruption

Only the socket controller constructs an internal PlaybackProof, using the
provider final transcript held for that generation. The browser cannot submit
assistant content or choose a different turn for completion.

`realtime_responses.terminate` takes the actor lock, reloads the session,
reauthorizes, validates the lease owner/connection generation/expiry, locks the
bound conversation, and reloads the L14 claim and state. Assistant insertion,
linking, and the existing L14 terminal compare-and-set commit together. A repeated
terminal operation is a no-op; insertion failure rolls back the whole completion.
Revocation, disconnect, and interruption serialize with that same actor lock.

Local microphone onset detection stops and clears output before any network
cancellation. Provider speech-start events are a secondary safeguard. Stop speaking
uses the same bound interruption and keeps the call open. Server retirement
precedes cancellation I/O. A response that is created after retirement is
cancelled by its matching ID. Cancel submission is deduplicated; the adapter
ignores only its own correlated benign cancel/terminal race error.

Interrupting queued audio after provider completion still yields an interrupted
turn with no assistant Message. Accepted user messages remain. The next committed
provider input receives a new ordered L14 turn. End Call immediately stops local
playback and capture, interrupts unfinished output, and closes resources.
Disconnect recovery and the existing lease sweeper release unanswered claims.
Reconnect reconciles receipts and never automatically regenerates an old reply.

## Bounds and telemetry

- Browser and server pending playback: at most 480,000 samples / 20 seconds /
  960,000 PCM bytes, plus the browser's bounded Float32 representation.
- Individual provider frame: at most 48,000 PCM bytes; at most 512 outstanding
  frames/sources. Whole response: at most 120 seconds of audio.
- Final/accumulated transcript: at most 8,000 characters.
- Session input identities and retired generation tracking: 256.
- Controller mailbox: existing configured depth 16. Valid microphone/provider
  bursts wait for bounded space with an I/O deadline; sustained stalls fail closed.
- Existing microphone worklet credits, frame-size/rate checks, 64 KiB client
  WebSocket backpressure, heartbeat, ticket, origin, and scope checks remain.
- Authorization heartbeat is at most one second; protected terminal writes
  reauthorize inside their transaction. Output generation has a 150-second bound.

The shared audio-output Web Lock is exclusive for live calls and shared for L12
playback. L12 only registers its existing Audio element with the ownership guard;
its synthesis, recording, review, replay, and voice selection flows stay intact.
Unsupported live audio ownership fails closed. Internal thinking, speaking, and
listening events reflect preparation and actual scheduled playback.

L14 telemetry allowlists first audio, browser first playback, queue depth/overflow,
playback acknowledgement, durable completion, interruption, cancel dispatch,
cancel-to-terminal latency, stale discard, and session end reason. No content,
audio, ticket, or playback seal is logged. Provider usage is deduplicated by
terminal response ID and correlated to the actual turn/generation. Bounded
teardown drains supplied cancelled terminal usage without forwarding late output.
No billing is performed.

## Acceptance evidence

`L15_PHASE_D_PROVIDER_ACCEPTANCE.json` records real provider + application socket
acceptance with synthesized disposable input and simulated playback receipts.
It covers normal Marin/Cedar replies, cancellation with a second real ASR utterance,
post-generation interruption, Stop speaking, and End Call. It is explicitly not a
claim of physical browser playback. PostgreSQL and regression evidence is recorded
in `L15_PHASE_D_TEST_ACCEPTANCE.json` and the phase report.
