# L15 Phase C — live user input

Phase B authorization, origin validation, single-use tickets, actor ownership,
lease/generation fencing, fixed Legacy/mode/role, reconnect deadline and provider
configuration remain authoritative. There is no public transcript-admission API.
`realtime_enabled` remains false in the normal local configuration.

## Browser capture

`realtime-client.mjs` acquires a secure mono microphone with echo cancellation
and noise suppression, using `microphone-ownership.js`. AudioWorklet capture
downmixes channels and uses a streaming 64-tap Blackman-windowed sinc resampler
to emit 24 kHz PCM16 little-endian audio. The input ring has 8192 samples. Output
frames contain 1200 samples / 2400 bytes / 50 ms. The worklet allows four
unacknowledged transferable frames; the main thread caps WebSocket buffering at
64 KiB. Overrun ends capture and asks the user to repeat unfinished speech.
There is no audio replay queue, playback, assistant audio, or barge-in.

The tiny shared acquisition wrapper installs before the unchanged L12
`voice-chat.js` on both chat pages. A same-origin Web Lock covers the permission
request and active microphone tracks, including separate tabs. Pending requests
also exclude one another. L15 fails closed if Web Locks are unavailable; L12
keeps its existing browser support. Different origins/browser profiles are
outside Web Locks' isolation boundary. Camera-only acquisition is untouched.

The developer surface is `realtime-dev.html`, restricted to a local API host.
It is not linked into production navigation. Changing any scope selector closes
and invalidates the old connection, clears its display, and disables reconnect
to that old session. Session binding remains authoritative on the backend.

## Additive browser protocol

After Phase B `authenticate` and server `ready`, the browser can send:

```json
{"type":"audio_frame","sequence":0,"pcm":"base64-PCM16"}
```

Sequence starts at zero for each new connection and must increase by one. The
JSON schema forbids additional fields, arbitrary text, provider-native events,
scope changes, and transcript labels. Existing message size/rate/queue limits
remain in force; decoded audio must be nonempty, even-sized, and at most 4800
bytes. Invalid frames terminate the connection. `ping` and `end_call` retain
their existing contracts.

Provider deltas become `transcript_provisional {item_id, delta}`. They are display
data only. Client provisional text is bounded to 16 items of 8000 characters.

Successful durable admission produces:

```json
{
  "type":"transcript_final",
  "item_id":"provider-item-identity",
  "turn_id":1,
  "message_id":1,
  "conversation_id":1,
  "legacy_id":1,
  "mode":"rya",
  "input_mode":"realtime_voice",
  "content":"My mother loved jasmine flowers.",
  "state":"streaming",
  "replayed":false
}
```

Only normalized, final provider text can produce this event. It is sent after
the database transaction commits. Transcript text is not copied to telemetry.
Reconciliation sends the most recent 256 durable receipts in ascending order,
with `replayed:true` and the current L14 state, without a provider item ID or
captured-audio replay. Client receipt storage has the same cap; older messages
remain accessible through normal chat history.

## Ordering and durable identity

`TranscriptOrder` associates all partial/final/failure events with provider item
IDs. Only `input_audio_buffer.committed` establishes predecessor links. A final
for B cannot overtake committed A, even when B's completion arrives first or B's
commit is observed before A's. A failed/empty A resolves its place without
creating a row. Missing predecessors wait within the bounded buffer and are
discarded at shutdown; they are never guessed. Conflicting predecessor links or
duplicate finals with different text fail closed.

At most 16 unresolved items and 256 total item identities per connection are
retained. Final text is capped at 8000 characters before whitespace normalization;
blank results and control-character corruption are rejected. No Unicode
translation, confidence inference, or provisional-to-final conversion occurs.

L14 `client_turn_id` is `live:<session UUID>:<SHA256(provider item ID)>` (106
characters). Connection generation is separately fenced and deliberately not
part of durable identity. The existing L14 request digest covers actor,
conversation, mode, content, input mode and timezone. A repeated accepted key
returns its existing user receipt; changed text conflicts. No claim is stolen,
no completed response is re-executed, and no new turn table/migration is added.

## Atomicity and processing policy

The Phase B actor lock and `bind_once` contract wrap an internal atomic option
on L14 `accept_turn`. The same transaction creates the first conversation when
needed, binds the live session, stages the L14 pending row, performs its existing
CAS processing claim, persists an ordinary user Message and links it to the
turn. Any failure rolls the entire transaction back. Existing conversations
never invoke the creation factory. Mode-specific title derivation is reused;
daily prompts and onboarding are not synthesized by live input.

**Phase C allows one active accepted turn per conversation.** It retains that
L14 streaming claim for the future response consumer. Further finalized
utterances receive `utterance_failed` with `realtime_turn_busy` and explicit
repeat guidance. They are not saved or silently queued indefinitely. In the
out-of-order A/B test, A is accepted first and B is rejected while A remains
active. The ordering buffer independently proves A-before-B delivery when both
are eligible. Normal text admission also respects an active realtime claim.

`prepare_admitted` is the server-only L14 handoff. Acceptance invokes the actual
`prepare_turn` builder/persona policy with the durable user Message. Parity tests
compare the full ordered prompts/grounding against text preparation for owner,
collaborator and viewer. The WebSocket itself does not run an assistant or a
background reasoning response. Preparation is rolled back before return so
even an existing builder retrieval's stale embedding refresh is not committed.

Call end, connection loss, revocation and expired-lease recovery terminate the
unanswered claim using L14's existing user-only `interrupted/cancelled` outcome.
The user Message survives. This is lifecycle cleanup, not Phase D assistant
interruption persistence: there are no assistant partials, played-audio estimates
or response cancellation UX. Reconnect reconciles receipts, never resumes an
old token. This also prevents a crashed call from keeping normal text busy.

No Phase C code calls `complete_turn`, canonical writes, personality updates,
progression/activity, write tools, or collaborator DELETE. Builder actor/source
provenance remains on the ordinary conversation, turn and message. Viewer
messages remain in the viewer's private conversation.

## Failure and shutdown

ASR failure/empty finals create no Message or ConversationTurn and emit
`utterance_failed`. Prior accepted messages remain accessible. Provider protocol
failure is not replaced by guessed text. If `end_call` arrives with unfinished
speech, the controller permits at most one second of provider reconciliation,
accepting only a safely ordered final. It stops forwarding new browser audio.
Anything unfinished is discarded and emits `realtime_unfinished_speech` with
repeat guidance. Disconnect/navigation discards provisional state; the client
also explains that unfinished speech may need repetition.

## Local acceptance commands

- Backend regression: `.venv/Scripts/python.exe -B -m pytest -q -o addopts= -p no:cacheprovider`.
- Frontend regression: `node --test tests/*.test.mjs` (expand the glob in PowerShell).
- Real provider samples: `.venv/Scripts/python.exe -B scripts/l15_transcript_acceptance.py`.
  Explicitly uses disposable synthesized speech and a separate SQLite database;
  this is not human microphone acceptance.
- Manual loopback backend: `.venv/Scripts/python.exe -B scripts/l15_local_acceptance_server.py`.
  Creates/migrates `backups/l15-phase-c/manual-microphone.sqlite3`, never the
  configured application database. Start the frontend HTTP server on port 5500.
  Open `http://127.0.0.1:5500/realtime-dev.html`, use the disposable local account
  `l15-mic@example.com` / `LocalMic-Jasmine-2026!`, Legacy 1, blank conversation ID.
  Speak the jasmine sentence, wait for Saved speech, end, then refresh messages.
- PostgreSQL tests require explicit loopback `L15_TEST_POSTGRES_URL` pointing at
  a disposable `l15_test*` database migrated to 0016. The unchanged L14 stress
  test uses a separate `l14_test*` database at 0015. Never point either at the
  configured application or production database.

Official ordering reference: [Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription).
The approved realtime session configuration remains GPT-Realtime-2.1 with
gpt-live-transcribe, semantic VAD medium, and autonomous responses disabled.
