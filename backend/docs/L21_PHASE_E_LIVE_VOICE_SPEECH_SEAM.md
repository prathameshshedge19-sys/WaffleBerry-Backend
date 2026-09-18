# L21.5 Authoritative Live-Call Answer/Speech Separation

Status: **ACCEPTED — CORRECTNESS**

L21.5 changes only the final answer-to-speech boundary for Legacy Live Call.
The accepted L14/L15 brain remains authoritative, one completed answer is
frozen and digested, and speech renders that exact value through either the
active preserved voice or the existing standard TTS adapter. L21.5 does not
claim latency qualification; the measured full-answer-first delay is explicit
L21.6 work.

## Baseline and scope

- Backend published baseline: `2799284942202e1df16baa554d296a5a1b3a5b1e`.
- Frontend published baseline: `6e36e78bbe22c7e5305ffe34eeb8e9a1db0c739d`.
- Backend worktree: `tmp/l21-phase-b-backend`.
- Frontend worktree: `tmp/l21-phase-c-frontend`.
- Production defaults remain `VOICE_CLONING_ENABLED=false` and
  `VOICE_LIVE_ENABLED=false`.
- No production environment, deployment, migration, tag, commit, or push was
  performed during acceptance.

## Exact old L15 flow

1. The browser sends bounded mono 24 kHz PCM over the existing authenticated
   websocket.
2. OpenAI Realtime transcribes input. `realtime_transcripts.admit` authorizes
   and persists one user message/turn.
3. `prepare_admitted` and the existing L14 brain build the authorized context.
   A silent text planning response selects read-only tools; the server executes
   them and returns their evidence as continuation context.
4. The same configured realtime model (`gpt-realtime-2.1`) creates the final
   audio response and audio transcript together.
5. `realtime_responses.Output` publishes the provider PCM through the existing
   assistant websocket events. The browser's existing `RealtimePlayback`
   supplies bounded progress and exact drain proof.
6. Only a valid final drain proof calls `terminate`, inserts one assistant
   message from the provider transcript, and permits canonical brain effects.
   Interruption or incomplete output leaves the admitted user message but no
   assistant message/effects. Reconnect reconciles user admission receipts; it
   does not replay assistant audio.

## New Legacy flow

The flow through admission, authorization, preparation, retrieval, tools,
reasoning, cancellation generations, playback, receipts, and persistence is
unchanged. When `VOICE_LIVE_ENABLED=true` and the admitted turn is Legacy mode,
only the final response is changed:

```text
same prepared L14/L15 brain and tool continuation
-> same OpenAI Realtime model, text modality
-> completed authoritative answer
-> normalize and freeze once
-> SHA-256 digest
-> fresh server-side speech resolution
   -> active READY preserved voice: durable purpose=live IndicF5 job
   -> otherwise: existing OpenAI TTS over the exact frozen text
-> complete mono 24 kHz signed-16 PCM before publication
-> existing Output assistant_started/audio/audio_end events
-> existing browser playback and drain receipt
-> exact frozen text persisted once
-> existing canonical finalization
```

Rya and all feature-off turns retain the old combined Realtime audio path.
There is no second LLM call after the authoritative answer, and no speech
provider has a brain, retrieval, tool, memory, personality, timeline, story,
relationship, or canonical-effect dependency.

The independent review compares the complete final request payload against
the old audio request: only modality and phase differ. The existing silent
tool planner remains; it is not a second final answer. The review restored
the pronunciation instructions accidentally omitted in text mode and ignores
silent planner text events exactly as the old parser did. Model, reasoning
effort, prepared context, tool continuation, authorization and first-person
instructions are unchanged. The brain/tool/canonical-effect modules have
zero diff from the published baseline.

## Authoritative answer contract

`AuthoritativeAnswer` contains the normalized completed text, SHA-256 digest,
turn ID, server-generated response claim/generation, provider/model provenance,
and completion status. Text deltas are internal and never start synthesis.
`response.output_text.done` supplies the candidate; only a completed matching
`response.done` freezes it. Audio or function-call output in this phase fails
closed. Duplicate terminal events cannot freeze a second value.

The same value becomes `Output.final`, the speech request input, the standard
fallback input, and the eventual `PlaybackProof.transcript`. It becomes durable
only after the accepted L15 playback drain proof. Speech creates no second
message and supplies no transcript or metadata to canonical text.

If barge-in occurs during text generation, the existing turn claim retires
before provider cancellation and no speech task starts. If interruption occurs
during synthesis, the task is cancelled, the durable Live job is cancelled,
and any written generated object is moved to the exact purge path. A result
already queued for publication is still reauthorized by the controller before
`assistant_started`, every audio frame, and `assistant_audio_end`.
Revalidation continues on the existing one-second controller heartbeat while
queued PCM drains, and immediately before accepting its final drain proof.
Cancellation joins any already-running database admission transaction, retains
its committed job identity, and then cancels/purges that job. It cannot lose a
late commit merely because cancelling `to_thread` does not stop its thread.

## Speech contracts and lifecycle

The server accepts no caller-supplied profile, version, reference, provider,
model, language selector, or object key. It resolves the active READY version
under the existing Legacy lock and binds a priority-90 `purpose=live` job to:

- Legacy, active profile/version, consent and reference binding;
- authoritative text and digest;
- conversation, realtime turn, claim, and actor;
- language, immutable model manifest and exact inference configuration;
- operation generation and purpose.

Migration `0026_voice_live_synthesis` extends the existing synthesis-purpose
constraint and adds the realtime turn/claim binding plus lookup index. Claim,
worker input, result publication, renderer read, and each websocket publication
recheck current turn, Legacy, actor, conversation, profile/version, consent,
asset, operation generation, and claim state.

The parent is `0025_voice_synthesis_jobs`. Downgrade is reversible when no live jobs
exist. With live jobs present it deliberately refuses before any DDL rather
than dropping their binding or silently deleting data. Offline PostgreSQL
rollback SQL contains the same database-side preflight; another offline
dialect is refused. There is no automatic destructive rollback conversion.

The preserved renderer reads only a verified private WAV and accepts exactly
mono 24 kHz signed-16 PCM. The standard adapter calls the existing OpenAI TTS
provider with the exact supplied frozen text and requests raw mono 24 kHz PCM.
No answer regeneration is available in either contract.

A preserved failure before any audio may fall back to standard speech for the
same digest. The complete result is buffered before the first event, so a
provider cannot switch mid-utterance. If authorization changes after playback
begins, the response is interrupted; it never changes voice. If both renderers
fail, the turn is safely interrupted with no fabricated audio or assistant
message and the call remains governed by existing L15 recovery policy.
The controller owns the start-admission handshake: a clone invalidated between
render completion and first publication can still take same-text fallback.
After `assistant_started`, fallback is forbidden. Producer backpressure counts
queued as well as published PCM against the existing 20-second playback window.

The Live clone wait is 90 seconds, leaving bounded room inside the accepted
150-second realtime response lifetime for same-text standard fallback and
terminal handling. This is a correctness bound, not a latency optimization.

## C2A, reconnect, and receipts

The new renderer is owned by the same active `Output` and therefore by the
existing account/session lease, connection generation, turn claim, response
generation, and playback binding. End Call, Stop speaking, local/provider
barge-in, logout, account change, Legacy change, access revocation, disconnect,
and process teardown cancel the owned renderer and preserved job. Late task,
GPU, storage, socket, and playback callbacks cannot attach to a successor.

The existing bounded PCM websocket schema is retained. The only public
addition is `voice_delivery=preserved|standard` plus the authoritative text
digest on `assistant_started`. Private profile/version/reference/model/storage
fields are never serialized. Reconnect does not replay synthesized audio or
restore an old Legacy/account voice. Generation completion alone does not
persist speech: only the exact playback drain proof does.

## Existing real GPU component evidence and measurement limits

The pre-review isolated campaign used the accepted synthetic/consented Marathi QA
reference, pinned offline IndicF5/Vocos artifacts, and an NVIDIA GeForce GTX
1650 (4,096 MiB). Manifest digest was
`7d07242ee1adf5a644df4447bd4dbda59583e4c6b7bb1df1ab48142cb027fb2f`;
inference-config digest was
`541ba644b1536a2b656b85e6fc78a6445809eb09ba28d58e40661bea9089718d`.
Peak allocated GPU memory was 1,495,299,072 bytes. The model load took 19.656
seconds in the final run.

Inspection of its script and persisted artifacts establishes the following
scope; this audit did not rerun GPU inference or change the quality settings.

1. A durable `purpose=live` worker job over fixed authoritative Marathi text
   succeeded: 2.539 seconds of audio, 32.578 seconds to synthesis readiness.
2. The equivalent Marathi-English worker job succeeded: 2.197 seconds of audio,
   30.578 seconds to synthesis readiness.
3. One real standard TTS call returned matching-text mono 24 kHz PCM in 2.406
   seconds. The script reused that result for both no-profile and failed-clone
   labels; these were not two actual fallback-controller executions.
4. Retirement/Legacy-switch/logout simulations mutated durable state during
   fresh inference (28.500/30.938/34.125 seconds). No generated asset was
   published. These bypassed the real socket; the three saved jobs remain
   `running` awaiting lease reconciliation, not proof of controller cleanup.
5. The revoke case published no generated asset after 33.000 seconds, but its
   persisted error is `voice_provider_output_invalid`; that run alone cannot
   isolate revocation as the cause. Deterministic lifecycle tests prove it.
6. A direct `Output` exercise retired PCM and rejected late drain without a
   browser or physical speaker. Actual socket/receipt coverage is separate.
7. Worker close/recreation around the same warm provider succeeded in 30.750
   seconds. This was not a GPU model reload or full process restart.

These are worker-component timings, not measured websocket first-PCM or
microphone-to-speaker latency. The script assigned its first-PCM timing from
synthesis time and hard-coded its canonical-effect counters. It did not call
the realtime brain. Independent tests below instead measure zero canonical
effects and exercise the actual controller/renderer/worker/receipt chain with
deterministic providers. Read-only inspection of saved successful WAVs verified
their stored SHA-256 and byte counts; every saved live job's text digest matches.
Both QA WAVs are mono 24 kHz signed-16 PCM. A real end-to-end latency campaign is
explicitly L21.6 scope. The observed ~31–33 second preserved delay is therefore
an L21.6 latency blocker/optimization requirement, not an L21.5 correctness
failure.

The accepted L21.4 listening verdict and exact quality settings remain
authoritative. L21.5 does not claim universal speaker or language quality.
Settings remain `nfe_step=48`, `cfg_strength=1.65`, `sway_sampling_coef=-1.0`,
`speed=0.97`, `cross_fade_duration=0.10`, `target_rms=0.1`; the accepted provider
and manifest/config modules are unchanged.

## Verification evidence

- Independent audit additions: 35 backend cases and 3 opt-in PostgreSQL cases.
  The 91-test focused backend run (new audit, existing seam, L15 provider/Output,
  voice synthesis/models) passed. It covers actual renderer/worker/storage,
  exact digest, one final answer, no-profile/disabled/failed/read-error fallback,
  30 controller synthesis cancellations (including a renderer ignoring cancel),
  logout/Legacy switch/reconnect, delayed admission, long PCM backpressure,
  post-audio-end revoke, one exact persisted answer and duplicate drain rejection.
- PostgreSQL: **13 passed**, using isolated schemas on a disposable loopback-only server.
  Added cases cover duplicate live admission, 10 real overlapping publication
  versus cancellation races on distinct connections, and populated rollback
  refusal. Existing voice migration/concurrency and L15 terminal races passed.
  The audit-only server was stopped and its exact temporary data/log removed;
  test result XML is retained outside Git. No production database was involved.
- Full backend regression after the audit fixes: **1,475 passed, 214 skipped,
  zero failures** in 582.11 seconds; two existing dependency deprecation warnings.
  The required opt-in PostgreSQL cases were verified separately as above.
  Initial focused runs hit Windows MAX_PATH with a long audit temporary root;
  shorter disposable paths/fixture storage names resolved that environment
  limitation without changing production storage behavior.
- Full frontend regression after all code and test fixes: **436 passed**. New Legacy-mode
  stress explicitly covers 100 retirements/600 late callbacks and 50 preserved
  reconnect cycles; the old Rya-only C2A loops are not passed off as new-path proof.
- Android deterministic bundle: 131 files. Final `testDebugUnitTest`, `lintDebug`,
  and `assembleDebug` passed (370 tasks). APK scan: 656 entries; no voice model,
  QA audio or database. `DebugProbesKt.bin` is Kotlin debug metadata, not a model.
  Permissions remain INTERNET, RECORD_AUDIO and the existing private receiver.
  API-24 device smoke remains inconclusive due to prior emulator infrastructure;
  no successful physical/emulator playback assertion is claimed.
- Static portrait tests, L12/L15 microphone exclusion, C2A start/end/reconnect,
  logout/account/Legacy fencing, malformed/duplicate events, playback drain,
  and Rya separation remain green.
- Python compile, JavaScript syntax, diff checks, secret/model/runtime/APK
  hygiene passed. Credential-shaped values and new runtime/model/audio/database
  Git candidates: zero. Existing Rya MP3 assets are unchanged. Both indices are
  empty and HEADs still equal the published L21.4 baselines.

## Security and privacy

- Profile resolution is server-only and current-state authorization is checked
  at admission, worker input, result publication, private read, and PCM emit.
- Cross-Legacy, old-account, revoked, deleted, replaced, expired, stale-claim,
  and lease-takeover output cannot publish.
- Object keys, reference transcript/audio, model paths, model names, consent
  details, and internal profile/version/reference IDs do not enter websocket
  events, browser state, canonical messages, or telemetry.
- `HF_TOKEN` is unnecessary at runtime and is not present in tracked changes.
- QA WAVs, databases, model artifacts, logs and smoke scripts are outside Git.
  The local APK/build outputs inside the frontend worktree are ignored.
- Speech has zero canonical effects; only the pre-existing brain finalizer may
  create authorized effects after a completed playback receipt.

## Independent audit fixes

1. Restore final text-request instruction parity; tolerate silent planner text.
2. Join cancelled database admission and preserve its job identity for cleanup.
3. Revalidate answer/turn/job/profile/asset bindings, streaming state and retirement.
4. Handshake first publication for same-text fallback; prohibit duplicate starts
   and provider changes after start; fence cancellation-ignoring renderers.
5. Include enqueued PCM in playback-window backpressure.
6. Revalidate while audio awaits drain and before terminal persistence.
7. Refuse populated migration downgrade before destructive DDL.
8. Fence client disclosure by playback identity, reject conflicting duplicates
   and private-field aliases, and clear delivery on retirement/completion/switch.
9. Strengthen actual-path tests and correct the GPU/stress evidence descriptions.

No brain rewrite, provider fork, model setting change, latency optimization or
unrelated refactor was made. Synthetic fixtures only; no customer data used.

## L21.6 remaining scope

L21.6 owns full end-to-end latency qualification, sentence/semantic chunking
only if justified, safe synthesis pipelining, cancellation efficiency, GPU
throughput/OOM/restart stress, concurrent capacity, and production hardware
sizing. None is claimed by L21.5.
