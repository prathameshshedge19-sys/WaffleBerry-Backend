# L21 final acceptance — production voice cloning

Status: **PRODUCTION READY — FEATURE GATED**.
This is code/readiness acceptance, not a production deployment/enablement claim.
Latency optimization belongs to **L22**, not L21. Preserved Live remains OFF.

## Published baselines and milestone scope

- Backend l21/voice-cloning: f78fd017f4efa81e906f90e241d28f86d5b3ddc0.
- Frontend l21/voice-cloning: b14b640e5b3873440f108beae7cb0bb3f91a9b23.
- Isolated worktrees: tmp/l21-phase-b-backend and tmp/l21-phase-c-frontend.
- Original dirty source worktrees are untouched. Final hardening is uncommitted.

L21.1 established architecture and independent safety gates. L21.2 delivered the
durable owner-only control plane, consent/profile/version/job/assets and lifecycle.
L21.3 added bounded private enrollment, frozen reference and pinned Whisper
transcription. L21.4 delivered pinned offline IndicF5/Vocos inference, fixed owner
preview and authorized Legacy message playback; human listening was accepted.
L21.5 delivered the same-brain authoritative-answer/speech seam and complete-drain
persistence correctness. Final L21 removes performance experiments and adds
independent overload/recovery/supervision/privacy hardening.

## Complete initial experiment classification

Both initial statuses, diff stats, complete tracked diffs and diff checks were
inspected. All 13 backend and three frontend changed/untracked files were copied
unchanged to tmp/l21-final/experiment-archive before selective edits.
No whole-worktree reset, history rewrite or published L21.5 change was performed.

| Original change | Category and final decision |
|---|---|
| app/api/routes/realtime.py | B: restore published full-answer-first controller; remove chunk ordering/pipeline |
| app/config.py | B: remove voice_live_chunking_enabled; final new timeout is independent A safety |
| app/services/indicf5_provider.py | A/B: retain serialized cancellation/join, tensor-traceback cleanup and OOM recovery; remove optimize_live/cache switch |
| app/services/indicf5_runtime.py | A/B: retain removable cooperative cancellation hook only; remove text embedding/sample monkeypatch cache |
| app/services/realtime_responses.py | A/B: remove multi-job/version-pin chunk fields; retain private numeric final-answer timing |
| app/services/realtime_speech.py | B: remove stream/chunk admission/answer slicing/multi-job cancellation; retain full-answer binding check and safe fallback event |
| app/services/turn_observability.py | A/B: retain numeric worker stages; remove chunk event/index/count |
| app/services/voice_chunks.py | B: removed entirely; no unused production chunker |
| app/services/voice_synthesis_worker.py | A/B: retain fresh lifecycle polling, joined cancellation and private numeric stages; remove cache flag wiring |
| tests/test_indicf5_runtime_l21.py | C: replace cache expectations with unchanged-sampling/cancel/serialization/OOM tests |
| tests/test_voice_latency_l21.py | C: remove chunk-only tests; move worker cancellation/OOM tests to test_voice_recovery_l21.py |
| tests/test_voice_latency_postgresql_l21.py | C: remove chunk tests; published full-answer duplicate/publication races remain; add final admission/recovery races |
| docs/L21_PHASE_F_LIVE_VOICE_LATENCY.md | C: remove obsolete blocked experiment report from final L21; raw archive and L22 handoff preserve measurements |
| frontend tests/live-voice-c2a.test.mjs | C: retain 100 response retirement / 500 delayed frame-callback safety test; rename chunk terminology |
| frontend tests/realtime-playback-l15.test.mjs | C: retain delayed PCM/no-early-drain and interrupted partial-answer tests; no chunk requirement |
| frontend docs/L21_PHASE_F_LIVE_VOICE_LATENCY_UI.md | C: replace obsolete performance gate with L21_FINAL_ACCEPTANCE_UI.md |

Only the listed uncommitted performance portions were removed. Their archive is
recoverable; no model, runtime QA evidence or user source data was deleted.

## Final architecture and product position

Owner consent → bounded private upload → exact selected reference and transcription
→ owner activation. Fixed owner preview and stored-message speech use only the
server-resolved active, consented version.

For staging preserved Live:

```text
existing brain → one completed authoritative answer → freeze once
→ complete preserved OR same-text standard speech
→ existing bounded PCM/WSS → complete playback drain → one persistence
```

No chunks, speculative audio, second brain, alternate model, sample cache or
performance scheduler. No memory/retrieval/personality/canonical write policy
change. Rya and standard Live retain accepted behavior; portraits remain static.
No partial answer is persisted. Failure after playback starts interrupts; no
mid-answer voice switching. Revoke/auth/connection/turn loss fences stale output.

Enrollment, reference, owner preview, message playback and standard Live are the
feature-gated release scope. Cloned Live correctness is implemented/tested, **not
production-enabled**. The four VOICE_* enablement defaults remain false.

## Final hardening beyond the experiment

- Bounded synthesis admission (64 global/four per Legacy pending), historical
  ceilings, atomic PostgreSQL cross-Legacy gate, idempotent reuse at capacity,
  preview/enrollment safe rejection and exact-text message/Live fallback.
- Expired partial reference/generated writes reconciled and purged, original
  retention enforced even with feature flags off; active profile remains valid.
- Maximum three ordinary attempts even after repeated process death; durable
  purge retry remains unlimited with bounded arithmetic/backoff.
- Genuine synthesis READY after validated warm-up; clears readiness before
  re-warm/close. Safe CLI startup failure, service notifications, watchdog,
  signal stop, cooperative inference join and no new claims while stopping.
- Synthesis deadline and configured output-duration enforcement, in addition
  to existing text/PCM/WAV bounds.
- Purge-only CLI mode independent of product flags/models. Voice deletion clears
  duplicate private synthesis text while retaining lifecycle/digest evidence.
- Allowlisted privacy-safe operational events and explicit model-binary ignores.

## Production-readiness audit

| Area | Contract / evidence boundary |
|---|---|
| Model artifacts | Six required immutable hashes; IndicF5/source/Vocos revisions unchanged; hash mismatch and CUDA absence fail closed |
| Worker deployment | Separate pinned dependency set and deployable service contract; Linux installation/service smoke remains a later staging action |
| Configuration | Explicit manifest digest/path/provider/device, PostgreSQL and encrypted private storage; no automatic model download |
| Feature flags | All false by default; cloned Live false until L22 authorization |
| Secrets | Staging token not needed at runtime; exact-value and APK checks required; secrets never model inputs |
| Storage | Exact registered keys/versions, private responses, bounded storage adapter, no public URL |
| Database/migration | 0023 → 0024 → 0025 → 0026; no new migration; disposable PostgreSQL roundtrip/constraints |
| Authorization | Owner manage; collaborators/visitors denied management; permitted Legacy visitor output; outsider/cross-Legacy non-enumeration |
| Consent | Fresh versioned authority assertion; immutable except one-way revoke; not speaker-ID verification |
| Revocation | Immediate admission/selection fence, in-flight stale publication rejection |
| Deletion | Private assets purged with proof before metadata/Legacy erasure |
| Account hook | One transactional request_account_voice_purge entry point; future parent owns account fence, commit, completion and metadata erasure |
| Failure recovery | Malformed output, OOM, storage failure, DB rollback and lifecycle loss do not publish false success |
| Process restart | New worker reacquires only expired leases; old token cannot complete |
| Machine restart | Durable DB/storage and clock assumptions documented; no actual production reboot claim |
| Queue recovery | Three-attempt bound, stale claims, bounded pending/history admission |
| Purge | Flag-independent service; unlimited durable retries; no success on uncertain absence |
| Backups/restore | Quarantined restore, flags off, replay revoke/delete records; offline backups governed separately |
| Observability | Fixed events/reasons and numeric values, no text/audio/key/token/private IDs |
| Security | Scope FKs, private auth-checked delivery, validated media/model/output and no caller-selected model/reference |
| Abuse | Consent assertion and AI disclosure; no biometric verification/resurrection/personhood claim |
| Runbooks | Concrete startup, staging, fault, retention, account hook and kill-switch procedures |
| Resource bounds | Upload/decode/text/output/duration/attempt/queue/history/concurrency bounded; host runtime/storage quotas required |
| Staging activation | All off → internal enrollment → fixed preview → message; synthetic QA and revoke/delete/fallback checks |
| Kill switch | All flags off and supervised process restart; preserve additive DB and mandatory purge, no destructive downgrade |

## Human quality and immutable inference

Accepted L21.4 human listening remains authoritative for owner-preview.wav,
natural-marathi.wav and marathi-english.wav: speaker/timbre similarity, Marathi
accent, tone/intonation, naturalness and Marathi/code-switch intelligibility were
acceptable; no material robotic/metallic artifacts reported.
Whisper code-switch similarity **0.4348** remains an automated-ASR limitation,
not a human acceptance failure. No universal speaker/language certification.
The removed chunk path does not inherit this verdict; any L22 chunking needs
independent join/prosody listening.

Fixed settings remain nfe_step=48, cfg_strength=1.65, sway_sampling_coef=-1.0,
speed=0.97, cross_fade_duration=0.10, target_rms=0.1.
Manifest digest 7d07242ee1adf5a644df4447bd4dbda59583e4c6b7bb1df1ab48142cb027fb2f;
config digest 541ba644b1536a2b656b85e6fc78a6445809eb09ba28d58e40661bea9089718d.

Earlier real GTX 1650 retirement/revoke/replacement checks published zero available
stale audio and recovered successfully with stable short-run live CUDA allocation
(1,428,681,216 bytes). This is bounded safety evidence, not long-duration leak
certification. An earlier unseeded waveform validation failure safely published
nothing; stochastic provider failure is handled by validation/fallback, not hidden
or “fixed” by lowering quality. Accepted preview/message outputs are unchanged.

## Final verification results

Final stabilized-code results below are from this closure campaign. Prior
experiment counts are not substituted. Runtime reports live in tmp/l21-final
outside both repositories.

- Initial focused enrollment/synthesis/realtime safety suites: passed.
- Final focused closure suite: **23 passed**, including the additional API-level
  full-capacity preview rejection/message fallback check added after the full run.
- Fresh disposable PostgreSQL: **17 passed**, including global admission races,
  rollback recovery, published lifecycle/publication races, migration roundtrip
  and populated 0026 downgrade refusal. No production DB accessed.
- Full backend regression across all 99 test files in three disjoint processes:
  **1,497 passed, 216 skipped, zero failures/errors**. Groups passed/skipped:
  318/81, 507/62 and 672/73; durations 407.7, 447.1 and 654.4 seconds.
  Opt-in environment skips are not represented as passes.
- Full frontend: **439 passed**, zero failures/skips.
- Android deterministic sync, unit, lintDebug and assembleDebug: **passed**.
  Debug build completed in 2m18s. INTERNET, RECORD_AUDIO and existing private
  dynamic-receiver permission only; no new permission.
- One bounded hidden read-only API 33 emulator attempt: **not executed** beyond
  startup, because the host failed the emulator disk-space prerequisite.
  Zero instrumented tests; no emulator/physical-device pass is claimed.
  No unrelated files were deleted to make room. Later target-platform smoke
  remains part of deployment validation, not a fabricated L21 result.
- Real GTX 1650 worker: manifest/all artifacts verified; missing/corrupt artifact
  and synthetic CUDA-unavailable checks fail closed; offline/token-free valid
  warm-up, actual in-flight cooperative cancellation/join, successful recovery,
  worker reload/warm-up and shutdown readiness clearing **passed**.
  Fixed-seed whole B PCM was bit-identical to the earlier accepted whole-answer
  baseline before and after cancellation. No quality setting was changed.
- Separate CLI processes: purge-only/all flags off, two fake-worker process
  startups/restarts and missing-model startup rejection without raw traceback
  **passed**. Real GPU reload was in-process; lease/process-death tests simulate
  queued/in-flight crashes against surviving DB/storage. No host reboot claim.
- Compileall, changed-JS syntax and both diff checks: **passed**.
- Exact secret-value/generic credential scan of 643 Git-addressable files:
  **zero leaks/candidates**. Raw and decompressed APK: zero token/credential hits
  and zero model module entries. Literal HF_TOKEN documentation references are
  counted separately and are not credentials.
- Model-binary ignore probe passed; only the identity manifest is tracked in
  voice-models. QA WAVs, models, test DBs/logs and runtime reports remain outside
  both repositories. APK/build output is ignored. Both indexes remain empty.

0026 refuses downgrade while Live jobs exist; empty compatible downgrade and
re-upgrade are tested. 0024/0025 destructive test downgrades are not the production
kill switch. Schema remains forward-compatible with flags off.
No physical Android evidence is claimed; unavailable physical hardware alone is
not an L21 code-closure blocker. Physical acoustic/end-to-end latency is L22.

## Handoff and non-actions

[Production runbook](L21_PRODUCTION_RUNBOOK.md) contains the later activation
procedure. [L22 handoff](L22_VOICE_PERFORMANCE_HANDOFF.md) preserves performance
and hardware work only: latency, L4-first/L40S-fallback benchmarking, throughput/
concurrency, caching/compile experiments, chunking/pipelining and physical latency.
None is retained as an L21 acceptance blocker.

No deployment, Hetzner/Vercel access, production migration/flags, GPU procurement,
commit, push, merge or tag. Leave all final changes uncommitted for review.
