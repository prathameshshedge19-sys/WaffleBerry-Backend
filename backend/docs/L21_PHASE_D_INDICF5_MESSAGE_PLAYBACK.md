# L21.4 IndicF5 Synthesis Worker and Legacy Message Playback

Status: **ACCEPTED**

The worker, APIs, authorization, storage lifecycle, browser integration, exact
artifact verification, CUDA load, real inference, restart, idempotency, and
revocation fencing are implemented and verified. Human listening of the fixed
owner preview, natural Marathi sample, and Marathi-English sample is complete
and acceptable. L21.4 is accepted with the automated Marathi-English ASR result
retained as a documented limitation rather than treated as an acceptance
failure.

## Baselines and scope

- Backend accepted remote tip: `a8a44d45a87d1e0c988eb81c24fd31da239b0e65`.
- Frontend accepted remote tip: `c44e8e3a2ebe735f4d08a80aec62b2b8bbb51d05`.
- Isolated worktrees: `tmp/l21-phase-b-backend` and
  `tmp/l21-phase-c-frontend`.
- L15 realtime routes, provider response creation, websocket audio messages,
  playback receipts, barge-in, persistence, and reconnect code are unchanged.

## Immutable model evidence

Official Hugging Face metadata inspected on 2026-09-15 reports:

- IndicF5 repository: `ai4bharat/IndicF5`.
- Immutable model revision: `ba85abedf18dc479a447eaa0eccbd76ab78a47d5`.
- Model architecture: `INF5Model`; model card license field: MIT.
- `model.safetensors`: 1,402,789,408 bytes; SHA-256
  `ba7f3671180fb7784e24bd1dafc96e729a38ce02e7f6d3877cdef32525a1865c`.
- `model.py`: 5,774 bytes; SHA-256
  `64ae04ffd86ba942de8342229a2883a915b54d74e21717b0cee2b13782df4c60`.
- `config.json`: 350 bytes; SHA-256
  `9d2bc0d96078b6914e9d77d3b7236c473fa11220a12b7ac0e3fdd148d0533591`.
- `checkpoints/vocab.txt`: 11,283 bytes; SHA-256
  `d3a5ff6aac12ea4fb50628a66a97b20c1e83b9e5ca356e5afae17b511cda96df`.
- Source repository: `https://github.com/AI4Bharat/IndicF5.git`.
- Verified source revision: `13f7c4d627cc10111aea8fe9c0039462cacacdc7`.
- Vocos repository: `charactr/vocos-mel-24khz`.
- Current immutable Vocos revision:
  `0feb3fdd929bcd6649e0e7c5a688cf7dd012ef21`. This supersedes the L21.1
  candidate initial revision for the actual current snapshot.
- Vocos `config.yaml`: 461 bytes; SHA-256
  `da9033922f969a47f0c160010226919e59f27761fd5066f3828d46de6650b0fc`.
- Vocos `pytorch_model.bin`: 54,365,991 bytes; SHA-256
  `97ec976ad1fd67a33ab2682d29c0ac7df85234fae875aefcc5fb215681a91b2a`.

Authenticated access to the exact revision succeeded. All six artifacts were
staged outside Git and verified by byte count and SHA-256. The checked-in
manifest has no null hashes and validates to digest
`7d07242ee1adf5a644df4447bd4dbda59583e4c6b7bb1df1ab48142cb027fb2f`.
The token came only from the ignored main-backend `.env`, was used only by the
staging process, and was never copied into an L21 worktree, printed, passed on a
command line, logged, documented, or required by inference.

`requirements-voice-synthesis.txt` is a separate, exact-version dependency
set. The ordinary FastAPI/CI requirements do not include torch, IndicF5, or
Vocos. Source installation is pinned to the Git commit above. Runtime sets
Hugging Face and Transformers offline modes and has no model download path.
Reviewed, hash-verified local `model.py` is imported without constructing its
networked wrapper. The provider recreates the reviewed DiT architecture from
the exact source revision, maps all 364 `ema_model._orig_mod.*` tensors from the
pinned checkpoint, and fails on any missing, unexpected, or unknown key. The
83 embedded compiled-vocoder keys are recognized but superseded by the
separately pinned Vocos snapshot. Both strict load reports were empty. Mutable
remote-code trust is not used.

## Inference contract

The fixed configuration is:

```text
nfe_step=48
cfg_strength=1.65
sway_sampling_coef=-1.0
speed=0.97
cross_fade_duration=0.10
target_rms=0.1
mono 24 kHz
```

`ClonedSpeechRequest` contains only Legacy/version identity, authoritative text
and digest, exact reference audio/transcript and digests, binding digest,
language, manifest digest, purpose, and operation generation. It contains no
memory, timeline, story, personality, relationship, tools, prompts, auth token,
or caller-supplied object key. Outputs must be non-empty mono 24 kHz signed-16
PCM, bounded to 120 seconds and 2 MiB after WAV wrapping. Invalid, odd-sized,
oversized, non-finite, wrong-rate, wrong-channel, or digest-mismatched output is
never published.

## Warm worker architecture

Run the isolated process with:

```text
python -m app.services.voice_synthesis_worker
```

Startup validates the complete manifest and every staged byte before importing
heavy dependencies, requires CUDA, loads IndicF5 and Vocos once, performs one
bounded synthetic warm-up, validates output, and only then emits a READY event.
The worker processes one synthesis job at a time, heartbeats its durable lease,
and never loads or downloads a model per request. OOM is terminal for that job;
no partial output is published. Storage uncertainty remains safe/retryable.

For every job it rechecks user existence, owner/viewer authorization, Legacy
state, conversation mode and ownership, assistant message identity and exact
text, active profile/version, consent, reference registration/digest/binding,
operation generation, manifest, inference configuration, and lease. Publication
rechecks the current generation and selection after inference. Revocation,
profile/Legacy deletion, access removal, replacement, or lease takeover fences
late output; any already-written stale object is queued for exact purge.

## Fixed owner preview

- Text: `नमस्कार. हा माझ्या जतन केलेल्या आवाजाचा नमुना आहे.`
- Version: `l21-owner-preview-mr-v1`.
- UTF-8 SHA-256: `f2c8219956e76c47ddcbcdf84eb9fd0b844e22649b214b4821faf827eae9cc12`.
- Endpoint: `POST /api/v1/legacies/{legacy_id}/voice-profile/preview`.

The body accepts no text or profile/version identifier. Fresh Legacy ownership
and the server-selected active READY version are required. Collaborators,
visitors, outsiders, and cross-Legacy callers get non-enumerating 404 behavior.
Preview creates no conversation, message, memory, timeline, story,
personality, or progression effect.

## Persisted Legacy message playback

Endpoint:

```text
POST /api/v1/legacy-conversations/{conversation_id}/messages/{message_id}/speech
```

The server loads the assistant message, derives its Legacy from the caller's
Legacy-mode conversation, reruns persona access authorization, and admits a
job over the exact persisted `Message.content`. It accepts no profile, version,
reference, provider, voice, or object key. Rya-mode conversations fail closed
and the existing Rya playback path remains unchanged.

No active preserved profile returns an immediate standard AI audio fallback
for the exact same persisted text. A terminal cloned failure exposes only a
safe fallback capability; the next explicit request may select standard
fallback. Playback never switches voices after audio starts and never invokes
the answer provider again.

## Idempotency and private storage

The synthesis digest binds Legacy, active profile/version, reference binding,
authoritative text, message/preview scope, language, the full model manifest,
exact inference config, and purpose. Preview and message keys are distinct;
version identity prevents replacement collisions. Terminal jobs without a
live generated asset are archived and may be explicitly retried, while an
unexpired exact-digest result is reused. There is no cross-Legacy/profile/global
text cache.

Generated mono WAV assets use opaque server keys under the registered Legacy,
profile, and version. They are private, use the existing encrypted S3 boundary
in production, expire within 24 hours, and are delivered only through an
authenticated backend proxy with `private, no-store`, `nosniff`, and fixed
`audio/wav`. Expiry queues exact deletion and requires positive absence proof.
No object key, model path, transcript, internal profile ID, or presigned URL is
serialized.

## Browser and Android

The READY owner state shows **Hear cloned preview**. It admits a job, polls at
most 40 times, fetches private audio, and revokes the Blob URL on completion or
disposal. Logout, Legacy switch, navigation, background, and session expiry
retire polling and playback.

Legacy assistant buttons use the Legacy-authorized endpoint and disclose
`Preserved AI voice` or `Standard AI voice`. The existing Rya route is selected
outside Legacy mode. No visitor profile selector or IndicF5 identifier is added.
Generated playback Blob URLs are intentionally not cached: every explicit play
reauthorizes, and the transient URL is revoked on stop, completion, error, or
lifecycle disposal.
The existing Android bundle asset graph already includes `voice-chat.js` and
`preserved-voice-settings.mjs`; no permission or native code change is needed.

## GPU and real smoke

`nvidia-smi` reports `NVIDIA GeForce GTX 1650`, 4,096 MiB VRAM, driver 529.04.
The isolated runtime uses PyTorch `2.4.1+cu121`; CUDA is available. A token-free
offline load completed in 20.422 seconds, allocated 1,411,641,856 GPU bytes,
and reported zero missing/unexpected acoustic or vocoder keys. The full real
control-plane smoke peaked at 1,499,640,320 allocated bytes.

Using the existing synthetic L21.3 Marathi QA reference, exact-quality outputs
were preserved outside Git under `tmp/l21-phase-c-runtime/indicf5-synthesis/qa-outputs`:

- fixed owner preview: 4.480 seconds, 215,084 bytes, 50.531-second synthesis;
- natural Marathi: 2.539 seconds, 121,900 bytes, 32.609-second synthesis;
- Marathi-English: 2.197 seconds, 105,516 bytes, 30.625-second synthesis.

The worker also proved an exact duplicate reused its durable job, a fresh
offline worker reverified/loaded/warmed/synthesized, and revocation after real
generation returned `stale` with no published result. Initial load/warm-up were
22.468/26.235 seconds; restart load/warm-up were 15.750/8.328 seconds.

Signal QA found zero clipped samples, bounded peaks, and no truncation signal.
Pinned local Whisper normalized similarity was 0.8611 for the preview and
0.9362 for the natural Marathi sample, but only 0.4348 for Marathi-English;
the English words were not recovered by that automated transcription. Human
listening found speaker similarity and voice/timbre match acceptable, Marathi
accent acceptable, tone and intonation acceptable, naturalness acceptable, and
no materially present robotic or metallic artifacts. Marathi and
Marathi-English code-switch intelligibility were acceptable by direct human
listening. The 0.4348 score remains an automated-ASR observation and limitation,
not a product-quality blocker for these accepted samples.

This is a scoped acceptance of the tested owner voice, samples, pinned model
artifacts, exact inference settings, and verified runtime path. It is not a
universal certification of speaker or language quality.

## Test and acceptance evidence

Focused fake-provider acceptance covers manifest rejection, exact input/text
binding, malformed output, warm lifecycle, durable claim, idempotent reuse,
private WAV storage, expiry/purge, owner preview denial matrix, exact Legacy
message authorization, no-profile same-text fallback, Rya exclusion, and safe
serialization. Existing L21 revoke/delete/replacement/lease/PostgreSQL tests
retain the generation race coverage. Frontend tests cover bounded polling,
private fetch, Blob revocation, lifecycle fences, fallback, disclosure, Rya
separation, and absence of internal IDs.

The software, real GPU, and human-listening gates are complete. The automated
Marathi-English result remains documented as a limitation without overriding
the acceptable direct-listening verdict. The refined parameters remain fixed
and must not be changed merely to make this one automated sample score better.

Verification on the final worktree:

- Focused L21.4 backend suite: 24 passed.
- Disposable PostgreSQL concurrency/migration suite: 6 passed; the loopback
  server was stopped and its temporary data removed afterward.
- Full backend regression: 1,642 tests collected, exit 0 (expected environment
  skips retained); only two existing dependency deprecation warnings.
- Full frontend regression: 425 passed.
- Focused frontend playback assertions: 8 passed.
- Android deterministic sync: 131 files; `testDebugUnitTest`, `lintDebug`, and
  `assembleDebug` passed (370 tasks).
- Python compileall, JavaScript syntax, diff checks, secret scan, model-binary
  exclusion, runtime-artifact exclusion, and APK module/security scan passed.
- API 24 emulator: debug APK launched; the real 215,084-byte preview WAV opened
  as `audio/wav`, and Android NuPlayer reached audio end-of-stream without a
  codec error. This is a baseline decode/playback smoke, not a signed-in
  end-to-end authorization test.

## L21.5 remains separate

L21.5 may split authoritative L15 answer text from provider-neutral PCM speech
and integrate cloned Live Call while preserving C2A fences. None of that work is
included here.
