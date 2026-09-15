# L21.3 Secure Voice Enrollment and Exact Reference Preparation

Status: L21.3 ACCEPTED. Automated backend/frontend/Android checks, a fresh
disposable PostgreSQL run, rendered browser acceptance, and API 36 emulator
acceptance pass. All production flags remain false.

Scope ends at a private READY reference. There is no IndicF5/Vocos adapter,
cloned preview, cloned message playback, Live Call clone, L15 answer/speech-seam
change, production migration, deployment, commit, or push.

## Baseline and isolated worktrees

- Backend: `l21/voice-cloning`, `tmp/l21-phase-b-backend`, starting and fetched
  `origin/l21/voice-cloning` SHA `e248bade2259c56f15dd30e80666273fae992fa2`.
- Frontend: `l21/voice-cloning`, `tmp/l21-phase-c-frontend`, based on fetched
  `origin/l20/android-foundation` SHA `94b560c91bacc30009901a42f785af6cd654532f`.
- The unrelated dirty backend/frontend main worktrees were not reset, stashed,
  cleaned, checked out, or edited.

## Consent and authorization

Exact server-provided copy:

> I confirm that I have the authority to provide and preserve this voice for this Legacy, and I consent to LegaRya processing this recording to create synthetic speech.

- Copy version: `l21-voice-consent-v1`.
- Policy version: `l21-voice-policy-v1`.
- UTF-8 SHA-256: `25be21b909bf2c2e7a161c67344bc7d5bc23c3f90c01624c891dd87f9e4e0161`.
- The backend requires `consented=true`, the exact versions and digest, a
  declared authority basis, and a fresh receipt for every replacement.
- Every intent, upload, activation, revoke, delete, and status call freshly
  scopes through the authenticated Legacy owner. Collaborators, visitors,
  outsiders, and cross-Legacy IDs cannot manage preserved voice.
- This is an explicit user assertion and processing consent, not legal advice or
  proof that the speaker is the named person.

## Upload contract and limits

`POST /api/v1/legacies/{legacy_id}/voice-profile/enrollments` creates the
consent-bound intent. `PUT
/api/v1/legacies/{legacy_id}/voice-profile/enrollments/{version_id}/content`
streams a bounded authenticated body into a private original reservation and
returns `202` only after storage verification and durable PREPARE enqueue.

Reviewed MIME declarations are `audio/wav`, `audio/mpeg`, `audio/mp4`,
`audio/x-m4a`, `audio/webm`, `audio/ogg`, `video/mp4`, and `video/webm`.
The declaration and browser `accept` are hints only; ffprobe establishes the
actual container, codec, streams, channel count, sample rate, and duration and
rejects mismatches or ambiguity. No URL ingestion exists.

Scoped defaults are 20 MiB input, 600 seconds, two total streams, one audio and
at most one video stream, two channels, 96 kHz source sample rate, 30 MiB decoded
output, 45-second tool wall time, 256 KiB captured tool output, four temp files,
5-second minimum reference, 7-second preferred reference, and 15-second hard
reference maximum. Original retention is at most 24 hours. These do not broaden
L16 media limits.

## Hostile-media boundary

The worker writes supplied bytes only to a generated private `prepare-*`
directory and uses fixed filenames. ffprobe/ffmpeg receive argument arrays with
`shell=False`, disconnected stdin, a `file,pipe` protocol allowlist, an explicit
network/nested-input blacklist, a wall timeout, bounded stdout/stderr, and a
bounded decoded file check. POSIX workers additionally set CPU, 2 GiB address
space, file-size, and process-count rlimits. Production service-level memory and
task limits remain a deployment gate; Windows local acceptance relied on the
same wall/output/file bounds because POSIX rlimits are unavailable there.

Symlinks, excess files, unreadable/encrypted/unsupported media, absent audio,
excess streams/channels/sample rate/duration, MIME spoofing, tool failure, and
oversized decode output fail with safe codes. The temp directory is removed in
the provider `finally` path; worker startup also sweeps orphan `prepare-*`
directories. Operational logs contain only event/outcome/duration fields.

## Normalization, selection, and exact binding

ffmpeg extracts only `0:a:0`, discards video/subtitle/data streams, applies
bounded EBU loudness normalization, and emits mono 24 kHz signed-16 PCM WAV.
No denoiser or voice-changing filter is used.

The deterministic selector measures 20 ms frames. It derives a conservative
signal/noise threshold, bridges at most 500 ms of a natural pause without
splicing time, finds continuous voiced runs, and scores a preferred 7-second
window using voiced ratio, silence, clipping, SNR, zero-crossing rate, amplitude
variation, and duration. It rejects insufficient speech, severe clipping,
noise/non-speech, and detectable disjoint speaker-like runs. It performs no
speaker identification or identity verification; overlapping speakers beyond
these signal checks remain a known limitation and the UI asks for one clear
continuous speaker.

The selected WAV bytes are frozen first. Their SHA-256 is computed before ASR,
and exactly those selected bytes are resampled over the same interval to 16 kHz
for Whisper. Nothing transcribes the full source and crops afterward. Binding
schema `l21-reference-binding-v1` hashes canonical JSON containing selected
audio digest, normalized and raw transcript digests, reference language,
Whisper model/revision, and recipe `l21-reference-preparation-v1`.

Transcript normalization is NFC plus control-character/whitespace folding and
a 4,096-character bound. It does not guess names or rewrite semantics. The
normalized text is stored for later synthesis; raw text is not retained, but
its digest is. APIs and logs expose neither transcript.

## Whisper and language handling

- Model: `openai/whisper-large-v3-turbo`.
- Immutable revision: `41f01f3fe87f28c78e2fbf8b568835947dd65ed9`.
- Local accepted `model.safetensors` SHA-256:
  `542566a422ae4f3fd23f1ba11add198fca01bbf82e66e6a2857b3f608b1eb9d1`.
- Accepted runtime: CPU-only `torch 2.8.0`, `transformers 4.56.2`,
  `safetensors 0.6.2`; model loading uses safetensors, no remote code, and
  local-files-only by default.
- The separate worker loads one model at startup and reuses it across jobs.
  Request handlers never load or call Whisper and no job downloads weights.
- Generation passes an explicit attention mask and requests
  `language=marathi`, `task=transcribe`; it does not request translation.

Whisper language detection labeled both synthetic Marathi fixtures as `hi` and
the English fixture as `en`. The provider therefore accepts `mr` directly and
accepts ambiguous `hi` only with deterministic Marathi lexical/script evidence;
it persists the raw detected token. Hindi-only text under `hi` and all other
tokens fail `voice_language_mismatch`. This is a conservative heuristic, not a
general language classifier.

Observed synthetic ASR is not production-quality evidence. The name `सई` was
retained, but the normal Marathi fixture included errors such as `माझे` ->
`माजे`, `आणि` -> `आने`, and `कुटुंबाच्या` -> `कुटुमबा चा`. English words in the
code-switched fixture were rendered phonetically in Devanagari (`online
meeting` -> `ओनलाइन मेटिन`, `family dinner` -> `फामली दिननर`). The WebM codec
also changed the first/last token relative to MP3. Broader human, accent, name,
device, and code-switch evaluation is required before a production quality
claim.

## Storage, publication, and purge

Original and reference objects use L21.2 `voice_assets` registrations and
private storage. Object keys, bucket/key identifiers, paths, encryption
metadata, and public URLs never enter API responses or frontend state.

The worker rechecks lease token, operation generation, Legacy deletion, profile
state, desired version, version state, consent, asset scope, writer deadline,
and exact hash/size/sample metadata. It reserves the reference key before I/O,
verifies the stored exact bytes, and only then transactionally publishes READY.
Late/stale reference writes are queued for exact-object purge. An expired
web-process original reservation is reconciled by the worker into safe failure
plus purge, preventing a permanent `dispatching` orphan.

On success the original becomes `purge_pending`; the durable purge job deletes
the exact registered object/version, positively checks absence, increments the
absence proof, and only then marks it `purged`. Delete uncertainty remains
retryable and is never reported as deletion. Terminal preparation failures
queue original purge at the bounded expiry time.

## Frontend and Android

The selected Legacy's owner receives a small `Preserved Voice` settings dialog
with Record/Upload, exact server consent, authority selection, progress,
replacement, and deletion. No clone or reference playback is offered. A new
candidate does not replace the current voice until READY and explicit
digest/revision-bound activation.

Status and mutations are fenced by session epoch, selected Legacy, navigation
generation, and abort signal. Logout, Legacy switch, disposal, deletion, or
replacement retires old work. Replacement polling continues while the old
current voice remains active.

Browser recording is user-initiated, uses the shared L12/L15 Web Lock with owner
`enrollment`, disables browser noise suppression, stops tracks on cancellation,
background, lock/session/navigation events, and caps capture at 120 seconds.
The browser requires at least five seconds before upload.

Android reuses `RECORD_AUDIO`; no permission was added. WebView recording uses
the same shared owner and native foreground audio grant. The SAF file chooser
allows only the exact reviewed MIME set from `https://localhost/chat.html`, in
the foreground with window focus and capture disabled. It grants only the
chosen `content://` item. No audio/video/storage/camera broad permission or
background recording exists, and mic capture does not auto-restart.

## Real local acceptance evidence

Only generic synthetic TTS QA audio was used. Artifacts and a CPU virtual
environment stayed under ignored/out-of-repository
`tmp/l21-phase-c-runtime`; no token was used. Portable FFmpeg 9.0.1 archive
SHA-256 was `fec81ae03971d9dd4be3ebe02e263bd2ec1d789483f931bdba5f5715e65da2e9`.

- Real DB/worker clear Marathi MP3: selected 7,000 ms at 160 ms; READY;
  reference available; original purge completed with one positive absence
  check; zero Memory and Message rows. Audio SHA-256
  `47038d0ea5e34aa58b3dd4b68dc23b755d5a14e98cfd7be59a9839ff7a241da6`;
  transcript SHA-256
  `4af278363b30b1aaf508dfe27a787b6527af8b74497dde1079623a35ead48192`;
  binding SHA-256
  `bca94dad718980a4a00c3de0fefb07e7d9c76b4c5ff646c9b22fc6248990f225`.
- 17.12-second padded WebM: selected 7,000 ms at 3,260 ms; READY; distinct
  audio/transcript/binding digests.
- MP4 with video plus AAC audio: selected 7,000 ms at 160 ms; READY.
- Marathi-English code switch: selected 7,000 ms; READY, with the phonetic
  English errors noted above.
- English-only: `voice_language_mismatch`.
- Eight seconds silence: `voice_speech_insufficient`.
- strong synthetic white noise: `voice_speech_insufficient`.
- corrupt bytes: `voice_media_invalid`.

## Regression and security evidence

Focused tests cover bounded upload/reservation, exact consent, MIME spoof,
corrupt media, duration/tool timeout, deterministic selection, exact-segment
hashing, normalization, attention-mask/Marathi transcription contract, empty
transcript, Marathi/Hindi ambiguity, language rejection, disjoint-speaker
uncertainty, storage-before-READY, positive original purge, interrupted-upload
reconciliation, privacy/no-key response, and zero canonical effects. Existing
L21.2 tests retain revoke/delete/replacement/stale publication, consent
revocation, IDOR, migration constraints, and PostgreSQL race coverage.

Completion results:

- Backend full regression: exit 0 at 100%; 1,636 collected items, comprising
  1,431 passed and 205 established environment-gated skips; two existing
  dependency deprecation warnings.
- Final focused L21.3 enrollment/API rerun: 16 passed. The wider focused L21
  service, deletion, and control-plane set also passed. The final six PostgreSQL tests
  passed against a newly initialized PostgreSQL 17.11 database named
  `l21_test_phase_b`; the database, local credentials, logs, and data directory
  were removed after the run.
- Frontend full regression: `422 passed, 0 failed` after the final UI and
  Android picker fixes.
- Android final: deterministic 131-file sync passed, then
  `testDebugUnitTest lintDebug assembleDebug assembleDebugAndroidTest` passed.
  The final post-fix focused run was `BUILD SUCCESSFUL` with 354 actionable
  tasks (16 executed, 338 up-to-date).
- The retained L15 `staleCallsAndFocusLossAreFenced` instrumentation method was
  also attempted, but its existing main-thread handoff timed out before an L21
  assertion ran. It was not treated as L21 evidence; the direct installed-APK
  WebView/ADB run below supplied the interactive L21 runtime evidence.
- `compileall`, JavaScript syntax, final Android bundle scan, secret-assignment
  scan, model/binary/runtime-artifact exclusion scan, and both repositories'
  `git diff --check`: passed.

Rendered Chrome 152 acceptance used only synthetic local QA identities and a
local backend/frontend. The owner saw the exact consent, required consent and
authority, uploaded and recorded valid samples, observed progress through READY,
activated only the matching candidate/revision/binding, retained the old active
voice during replacement, deleted, cancelled, and reopened coherently. A short
recording failed safely; a bounded recording uploaded. Legacy switch, logout,
and navigation fenced polling and stopped tracks. Collaborator and visitor UI
remained absent and management requests returned 404. No unexpected console
error, credential, transcript, private object key, or response leak appeared.

Android acceptance used the current debug APK on the isolated Google APIs API
36 `l20_api36_phone` AVD (`sdk_gphone64_x86_64`) and the real
`https://localhost` WebView. Android DocumentsUI opened through
`ACTION_OPEN_DOCUMENT`; reviewed MP3 and MP4 fixtures returned scoped
`content://` results, and cancellation was safe. Capture-mode and untrusted-page
requests failed closed. No broad storage/media or CAMERA permission was present.
The real `RECORD_AUDIO` prompt/permission path passed; because `-no-audio` made
the virtual input unavailable, a deterministic in-WebView media source was used
only to exercise the real shared ownership and lifecycle code. L12 and L15 both
received `NotReadableError` while enrollment owned the mic, and observed stopped
track counts advanced for cancel, Home/background, simulated lock, session end,
and Legacy switch. Resume did not restart capture. There was no crash,
unexpected external navigation, sensitive Logcat output, or L21 runtime
exception. This is emulator lifecycle evidence, not physical-device audio
quality evidence.

Two narrow acceptance defects were fixed and regression-covered: reopening after
a Legacy switch now resets the dialog to a loading state before status arrives,
and the Android voice picker now uses the exact mixed audio/video SAF contract
with foreground trusted-origin, scoped-content, and returned-MIME validation.

No voice operation creates or changes Memory, MemoryRevision, Personality,
LifeEvent, Story, relationship, Conversation, Message, or builder activity.
The enrollment transcript never enters memory extraction.

## Remaining L21.4 scope

L21.4 must separately audit and pin IndicF5/Vocos artifacts, implement a warm
synthesis worker, create a fixed-text cloned preview, and add cloned message
playback. Live cloned speech and any L15 answer/speech integration remain later
gates. None is installed or used here.
