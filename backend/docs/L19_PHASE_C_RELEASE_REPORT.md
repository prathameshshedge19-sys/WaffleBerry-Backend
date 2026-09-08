# L19 Phase C — Visual speaking companion

Status: **local implementation verified; production release blocked on protected-backup approval; not released or tagged**.

Frontend implementation commit (local only): `d8387be93f6d9157cab54a95f14317dd509e5867`, `feat(l19): add visual speaking companion`. It is on the isolated `l19-phase-c` branch and has not been pushed or deployed. Backend Phase C operator/deployment/documentation changes are a separate local commit; there is no accepted production release SHA yet.

This continues accepted Phase B `e17ef6744762e933bea9faae035078046c6dd49a`. Phase B architecture, domain lifecycle, migration 0021, native provider, and canonical-memory authority are unchanged. No L20 or voice cloning.

## Implemented scope

Owner Visual Presence settings in the builder, same-Legacy L16 image selection and explicit `visual_reference` upload, physical-square EXIF-oriented quarter-turn crop with pointer/keyboard/zoom controls, versioned likeness declaration, idempotent preparation, bounded polling, private motion preview, and separate revision/digest-bound approval. Current and candidate portraits remain separate. Disable, replace, regenerate and prepared-portrait erasure preserve the original source and canonical records. Collaborators get owner-managed explanatory UI without owner API calls; visitor markup has no settings.

The Legacy-only renderer uses the existing vendored Three.js, a source-derived 2D atlas/mesh, bounded numeric deformation and local procedural timing. No remote inference, generated video, facial identity/emotion inference, microphone-driven mouth, additional audio context, or voice selection is added. Rya retains her particle renderer, ambience, name/perspective and existing Cedar/Marin audio policy.

Assistant-only 20 ms RMS windows observe L15's already-decoded PCM on its scheduled AudioContext/device-latency clock. The observer never acknowledges playback. Mouth hysteresis is .08/.04, attack 30 ms, release 90 ms; silence has a 150 ms hard neutral deadline. Interruption resets synchronously before source stop and transport cancellation. Session/connection/turn/generation/response/playback identity fences retire stale samples. Account/session change and logout retire private visuals before network completion.

Private route/host allowlisting is separate from L16. Manifest and bundle digests, MIME/byte/pixel limits, numeric topology, winding, UVs and deformation envelopes are validated before installation. Assets are memory-only Blob/ImageBitmap resources; no public S3 URLs, query credentials or persistent portrait cache. Fifteen-second authorization leases refresh within five seconds; errors/expiry revoke all prior imagery. Version/revision/digest changes dispose the old bundle before awaiting replacement. Navigation, hidden pages, reconnect, calls ending and logout dispose resources. Authorized static poster is used for reduced motion or WebGL failure, and the Legacy initial is used after revocation.

Deployment additions: hash-locked Linux native dependencies, private visual-worker systemd unit, restricted aggregate purge-health service/timer and sanitized nonzero-exit health CLI. Parent worker retains only required application DB/S3 access; native children retain the accepted minimal environment, Landlock/seccomp denial and pinned model. Production provisioning remains pending at this checkpoint.

## Evidence to date

| Gate | Observed result |
| --- | --- |
| Frontend Node suite | 272 passed, zero skipped; includes 72 new L19 cases |
| Local owner browser flow | 14 checks passed; zero page errors; desktop, 820px tablet and 390px mobile |
| Real native synthetic preparations | Five usable portraits passed; ambiguous group and unusable crop rejected with `visual_needs_recrop` |
| Native resource range | 1.968–2.292 seconds; peak RSS 223112–223464 KiB, under unchanged 768 MiB cap |
| Real browser state matrix | Five native portraits inspected; neutral, low/medium/high mouth, blink, idle/listening/thinking/speaking, exact neutral interruption and static states; recognizable source likeness, no observed severe tearing or seams |
| Renderer main-thread performance | 0.1–0.3 ms p95 on local Chromium software WebGL; 768px maximum canvas, DPR 1.5 |
| Browser resource churn | 100 renderer load/disposal cycles; zero retained canvases/RAF, reported JS heap delta zero (coarse browser metric, not total GPU-memory measurement) |
| Final native Linux tests | 199 passed, zero skipped; 46.01 seconds; provider/reference/confinement and all three real lifecycle cases |
| Real browser audio/failure integration | Actual AudioContext scheduling, internal silence, exactly one drain receipt, retired-output suppression, and WebGL loss preserving running audio/authorized poster; neutral before cancel in 0.2–0.3 ms |
| Final focused L19 backend | 392 passed, 109 skipped; 27.80 seconds |
| Final L15/L16 backend regression | 227 passed, 35 existing opt-in PostgreSQL skips; 113.43 seconds |
| Final full clean backend | 1242 passed, 168 skipped, 2 existing deprecation warnings; 291.67 seconds |
| Added operator checks | 4 passed; success, failed threshold, sanitized runtime and configuration/driver-initialization failures |
| Fresh PostgreSQL 17.11 matrix | 108 passed, 14 skipped; all 74 live PostgreSQL cases passed; 110.22 seconds |
| Disposable PostgreSQL cleanup | Verified zero test sessions/databases; stopped loopback port 55444; removed exact cluster and disposable password; retained binaries/evidence |
| Production read-only preflight | Clean checkout `fbe679af56fcff0751f426d00b0c7eb0ba80fa3e`; remote main contains accepted Phase B; DB exactly `legarya`, revision `0020_legacy_stories`; private S3/SSE-C configured; backend/personality/media services active |
| Dedicated QA account | Normal login and verified identity passed; only labelled synthetic QA Legacies selected for forthcoming acceptance |
| Compileall / diff review | Passed; 20 frontend and 9 backend Phase C files reviewed; no identified secret/binary/QA-artifact inclusion or trailing whitespace |
| Disposable Linux cleanup | Exact `/var/tmp/l19-phase-b-closure-a1f4c8` resolved, confirmed non-symlink with zero native QA processes, removed and absence verified; local evidence retained |

The final full regression used the explicit real `.venv/Scripts/python.exe -m pytest` from the isolated backend worktree, with no exclusions or temporary xfails. L19 skips are the platform/opt-in PostgreSQL variants already exercised by the separate Linux and live PostgreSQL runs; the remaining full-suite skips are existing non-L19 opt-in PostgreSQL cases. No test was weakened or hidden to obtain a passing result.

## Reproduced issues and corrections

Four existing frontend assertions assumed LF line endings, failing on Windows CRLF: two worklet import-stripping patterns and two robots assertions. Only optional CR matching was added; their semantics remain unchanged. Original dirty worklet and playback-test edits are excluded.

New test-fixture mistakes (missing renderer diagnostics stub, missing synthetic bearer type) were corrected. Browser reduced-motion assertion now waits for the browser's asynchronous media-query event. The contact-sheet-only static snapshot must copy synthetic poster pixels before its source Blob URL is revoked; product disposal remains unchanged.

The fresh PostgreSQL launcher inherited daemon pipe handles on Windows; its startup now routes daemon output to the owned server log. The first matrix used `l19_test_phase_c`, which the frozen tests deliberately reject. The disposable database was renamed to the existing strict allowlisted `l19_test_phase_b` on the same fresh loopback cluster. No acceptance guard or race test was relaxed; the rerun passed all 74 live cases.

Final review added immediate account/logout invalidation, late response-ID presentation binding updates, a hard silence deadline independent of RAF, and explicit neutral listening state. All remain presentation-only.

## Remaining release gates

Provision and verify the isolated production worker only after a fresh protected custom `pg_dump` passes `pg_restore --list`. Deploy disabled backend and migration 0021, then worker and frontend, then authenticated synthetic production preparation/preview/activation/call/revocation/cross-Legacy/zero-factual-effects checks. Verify private S3 erasure, purge-health timer, existing services and sanitized logs. Record backup path/size, exact backend/frontend SHAs and only then create `legarya-l19-visual-speaking-companion` at the accepted backend SHA. Re-run relevant gates if subsequent implementation changes are required.

Two attempts to request the protected production backup were rejected by the tool approval layer before execution. The second supplied the user's attached Phase C sections 54 and 56 (explicit production release and fresh custom `legarya` backup instructions). The approval layer still requires a direct chat authorization covering the full database/customer data in the root-only on-server rollback backup. A concise authorization question was sent to the user. **No workaround, backup, migration or deployment was attempted after that rejection.**

No production code, configuration, schema or service changes, frontend deployment, push or release tag occurred. The only authenticated account action was normal QA login/read-only owned-Legacy discovery; no synthetic memory/source/portrait/Story mutations or S3 objects were created. Unrelated original worktree edits remain untouched, unstaged and uncommitted, verified against their pre-task SHA-256 hashes. This report is not a production release acceptance claim.

## Resumption checkpoint

Worktrees are `backups/l19-phase-c-frontend` and `backups/l19-phase-c-backend`, each on local branch `l19-phase-c`; original main worktrees retain the five unrelated changes. Remote main remains frontend `ef920331edbc0990945aca2a78bd96d24e5546fc` / backend `e17ef6744762e933bea9faae035078046c6dd49a`. Production remains backend `fbe679af56fcff0751f426d00b0c7eb0ba80fa3e`, migration `0020_legacy_stories`.

After direct backup approval, recheck live preflight and follow `deploy/L19_VISUAL_PRESENCE.md`. The credential-free backup helper is retained locally outside Git and has never run. Do not infer a verified backup from its existence. The QA runtime was removed; recreate only as necessary. Native model/wheel provenance, synthetic bundles/contact sheets, the 199-test log, PostgreSQL XML (including initial harness failures), focused/full regression XML and local browser scripts remain reproducible outside Git or in the committed test harnesses. Never reuse old session tokens or another account; obtain a fresh normal login to the authorized permanent QA account.

## Known scope limits

Restrained source-pixel mouth/eyelid deformation is not phoneme-accurate lip sync or photorealistic blink closure. No actual voice cloning, full-body/semantic gestures, recording/video export or camera capture. Difficult photos require recropping. Automated Chromium/software-WebGL checks do not establish physical mobile-device performance or human microphone/listening acceptance. Delivered bytes cannot be remotely retracted; in-page imagery is revoked through short authorization leases and local lifecycle fences.
