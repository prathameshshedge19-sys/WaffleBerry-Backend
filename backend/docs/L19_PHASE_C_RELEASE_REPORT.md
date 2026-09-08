# L19 Phase C — Visual speaking companion

Status: **ACCEPTED — mandatory automated, production, security, integrity and cleanup gates passed**. The exact final backend release/report commit is the commit resolved by annotated tag `legarya-l19-visual-speaking-companion`; application code is unchanged from `95d531e7cb1dd2169225df0271c854566234667f`. Frontend release is `c1a0e3df127877b37d82edd634cd77bbb354ac72`. No L20 or voice-cloning work.

## Resumed production checkpoint — 2026-09-08

The user directly authorized the full protected production rollback backup, including customer data, and subsequently authorized read-only counts/fingerprints restricted to the two verified synthetic QA Legacies. The earlier backup block below is historical, not the current release blocker.

Backup record (no contents exported or inspected):

| Field | Result |
| --- | --- |
| Path | `/var/backups/legarya/l19-20260908T181437Z/legarya-before-0021.dump` |
| Size | 444765 bytes |
| Timestamp | 2026-09-08 18:14:37 UTC |
| Verification | Configured and connected DB exactly `legarya`; nonzero custom dump; root-owned 0600 file inside existing root-owned 0700 protected backup location; `pg_restore --list` succeeded |

Backend `95d531e7cb1dd2169225df0271c854566234667f` was pushed normally and deployed by fast-forward from the clean production checkout. Only the new Pillow 12.3.0 dependency was added to the API environment; `pip check` passed. Migration `0020_legacy_stories` → `0021_visual_companions` succeeded with bounded lock/statement timeouts and both visual flags off. Backend, personality and media workers restarted healthy. No downgrade or customer-data manipulation occurred.

Private worker `/opt/legarya-visual/venv` uses CPython 3.14.4, the hash-locked native closure and privately extracted accepted OS libraries. Model `/opt/legarya-visual/face_landmarker.task` matches the pinned SHA-256, is root:waffleberry 0640, and its parent is root:waffleberry 0750. Full native LICENSE/NOTICE and model provenance are retained privately. Package inventory and install hashes are on-server. The bounded worker and aggregate health timer are installed; no native dependency was added to the API closure. Final observed worker peak was **330407936 bytes** with zero restarts, under the unchanged 805306368-byte limit, swap zero, CPU 50%, TasksMax 64. The native child retains the unchanged Landlock/seccomp boundary.

Frontend `7e863c94fc195a9e7d12acf5231af728af31685f` (implementation `d8387be` plus static-artifact exclusion) was pushed to the existing Vercel-connected repository. Live L19 markup/modules return 200; the QA browser harness path returns 404. Both feature flags were then enabled for authorized production QA.

Production exposed a narrow frontend timing defect: normal L16 upload returns accepted/pending before its asynchronous visual-reference safety validation finishes. The initial UI attempted an original read immediately, receiving the correct safety denial. After validation, the same source opened normally. Frontend fix `c1a0e3df127877b37d82edd634cd77bbb354ac72` adds bounded, cancellable, Legacy/account-fenced clean-source polling; it changes no backend validation or authorization. **279 frontend tests pass, including 79 L19 cases**, plus the 14-check owner browser suite and all five native portrait/AudioContext cases. Publication was initially rejected; the exact repository, remote baseline, clean four-file scope and the original Phase C sections 92/98 authorization were reverified. The approval layer then approved the ordinary exact-SHA non-force push. Vercel serves the fix, and a fresh fictional upload successfully waited for safety validation and opened its crop preview. No security check was relaxed.

Final authenticated production evidence, using only the permanent dedicated QA account and its labelled synthetic Legacies:

- Normal login and account/ownership verification; existing LEG persona access kept separate from ownership.
- Fictional visual-reference upload, deterministic safety validation, real native preparation, private rig preview and separate exact-digest activation. No automatic activation.
- Anonymous application asset reads denied; private responses are no-store. Exact synthetic S3 objects denied anonymous, missing-SSE-C and wrong-SSE-C HEAD requests; authenticated SSE-C metadata access succeeded. Exact object/version registry is root-only on-server; no broad bucket listing or raw key export.
- Cross-Legacy version, manifest, generated-asset and source requests denied. Full non-owner/collaborator authority remains covered by automated tests; no separate production identity was manufactured.
- Real production Live Voice: silent call creates zero Conversation; approved portrait idle; mute/unmute; real synthetic microphone speech through unchanged L15/WSS/provider; mouth follows assistant audio; Stop speaking neutralizes synchronously **0.2 ms**, before transport interrupt; stale output stays neutral; next response animates and drains; natural silence neutral; static toggle; End Call cleanup. Rya retains particles with zero L19 visual fetches. Zero browser errors.
- Production replacement preview/activation, old-version read denial, disable/re-enable, companion deletion with original retained, 390px/tablet no-overflow checks passed. One harness expected a .5 crop offset after eight .05 keyboard steps; actual .4 was correct. The real manually selected crop was previewed and approved; this was not a product crop defect.
- All **21** scoped canonical/provenance/personality/relationship/activity/conversation table-group fingerprints remained unchanged across visual setup/preparation and, separately, across replacement/deletion. Real voice added exactly one conversation and two turns; other audited groups remained unchanged. No rows or credentials were displayed.
- On-server log parsing exports only fixed allowlisted counts. Worker cycle and purge-pending records match the source-defined schemas; no raw production journal was exported.

The final full backend rerun initially reported **1 failed, 1241 passed, 168 skipped**. `test_logout_ends_call_and_denies_old_token` incorrectly minted a new token in its final assertion; crossing a second boundary made that fresh token legitimately postdate logout. The isolated test now reuses the actual pre-logout header throughout and still requires 403 for old-token realtime admission. Production authentication code is unchanged. The corrected test passed, followed by the authoritative full rerun: **1242 passed, 168 skipped, 2 existing deprecation warnings, 277.64 seconds**. Its XML includes **392 passed/109 skipped L19** and **227 passed/35 skipped L15/L16** cases. Compileall and diff checks passed. Failure and final XML are retained outside Git. All five unrelated original files still match their pre-task SHA-256 hashes.

## Final acceptance and cleanup

Three actual native production versions were prepared, privately previewed and explicitly activated in sequence. Original preservation on companion deletion and immediate viewer denial on source deletion passed. A further preparation after the fresh-upload timing retest hit the existing preparation quota; no fourth version was created, no limit was raised and no authorization or safety guard was bypassed. The fresh upload itself had passed the deployed safety-wait/crop-opening retest. The redundant preparation was not needed to establish the already-passing native lifecycle.

Both labelled fictional source originals and all **12 registered generated asset records** are purged. Exact-key S3 enumeration/HEAD verified **14 tracked keys, zero current objects, zero versions and zero delete markers**. This includes two reserved writes with no surviving object. All three purge jobs succeeded; one needed 14 ordinary retries across writer completion and the two-absence proof/backoff. No direct database mutation, broad bucket listing or manual S3 deletion was used. Temporary root-only exact-object registry was removed only after this proof. Source tombstone/purged timestamp and active-manifest denial were independently rechecked through the normal authenticated API. Cleanup harness serialization/selector mistakes did not weaken assertions; final check passed.

Final scoped audit against the pre-visual baseline: **19 non-conversation table groups unchanged**, including all canonical Memory/Timeline/Story, provenance, personality, identity/relationship and activity groups. Conversations changed 7→10 and turns 14→18 only during the explicit real voice/text smoke flows. Separate before/after visual-only checkpoints had all 21 groups unchanged. No visual-reference evidence or candidate was created. Existing labelled QA Legacies/account, pre-existing synthetic canonical data and ordinary general-knowledge QA conversation records remain available; no customer records were accessed or mutated for QA.

Final production product smoke: **12 checks passed, zero page errors** — homepage, memories, personality, media, timeline, stories, voice settings, realtime capability, owner dashboard controls, Rya text, Legacy text and real L12 transcription/standard-voice preview. Preview returned 46464 audio bytes. Real L15 production visual/call suite separately passed 11 checks with zero page errors.

| Final gate | Result |
| --- | --- |
| Frontend / L19 frontend | **279 passed, zero skipped / 79 passed** |
| Focused L15 frontend realtime | **33 passed, zero skipped** |
| Full backend | **1242 passed, 168 skipped**, 277.64 s, no exclusions or xfails |
| L19 backend / L15–L16 backend within final XML | **392 passed, 109 skipped / 227 passed, 35 skipped** |
| Fresh PostgreSQL 17.11 matrix | **108 passed, 14 skipped**, including all **74 live** cases |
| Native Linux provider/lifecycle/confinement | **199 passed, zero skipped** |
| Browser | 14-check owner suite; five native portrait state matrices; real AudioContext/failure cases; 11 production live-call and 12 product smoke checks |
| Performance | Native 1.968–2.292 s, 223112–223464 KiB RSS; renderer p95 0.1–0.3 ms; 100 dispose/load cycles with zero canvases/RAF retained |
| Production services | Backend, personality, media and visual workers active; visual-health timer active; health oneshot Result=success / ExecMainStatus=0 |
| Purge health | status=ok; pending=0; repeated failures=0; remote reconcile pending=0; no current cleanup error |
| Backend health / schema | HTTP 200, status=ok; `0021_visual_companions` |
| Vercel | Exact frontend remote main `c1a0e3d…`; live `l19c2` settings and safety-wait module 200; QA test harness path 404 |
| Privacy logs | On-server schema/allowlist audit: zero unexpected worker journal lines; no raw journal, row contents, S3 keys/versions or credentials exported |
| Compile / build / whitespace | Python compileall and JS syntax checks passed; static frontend requires no separate bundler build; git diff --check passed |
| Disposable cleanup | Linux QA runtime absent; PostgreSQL test DB/sessions/cluster/password removed and loopback stopped; both production synthetic sources and all generated objects erased; exact-object registry removed |

The protected rollback backup and required private production model/runtime/inventory/notices remain on-server. Credential-free local evidence remains outside Git. All five pre-existing dirty-file hashes were preserved. The final backend commit adds only this report/model-manifest acceptance update and the deterministic pre-logout-token regression-test correction; production app/schema/worker content is identical to the already-tested `95d531e7…` application commit. Publish the final commit and annotated backend tag by ordinary fast-forward/non-force workflow; record the frontend SHA separately.

## Historical pre-deployment checkpoint — superseded by the final acceptance above

The rest of the checkpoint narrative records the earlier stopped state, not current production or publication status. Historical counts below are superseded by the final matrix above where reruns occurred.

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

## Historical remaining release gates (completed above)

Provision and verify the isolated production worker only after a fresh protected custom `pg_dump` passes `pg_restore --list`. Deploy disabled backend and migration 0021, then worker and frontend, then authenticated synthetic production preparation/preview/activation/call/revocation/cross-Legacy/zero-factual-effects checks. Verify private S3 erasure, purge-health timer, existing services and sanitized logs. Record backup path/size, exact backend/frontend SHAs and only then create `legarya-l19-visual-speaking-companion` at the accepted backend SHA. Re-run relevant gates if subsequent implementation changes are required.

Two attempts to request the protected production backup were rejected by the tool approval layer before execution. The second supplied the user's attached Phase C sections 54 and 56 (explicit production release and fresh custom `legarya` backup instructions). The approval layer still requires a direct chat authorization covering the full database/customer data in the root-only on-server rollback backup. A concise authorization question was sent to the user. **No workaround, backup, migration or deployment was attempted after that rejection.**

No production code, configuration, schema or service changes, frontend deployment, push or release tag occurred. The only authenticated account action was normal QA login/read-only owned-Legacy discovery; no synthetic memory/source/portrait/Story mutations or S3 objects were created. Unrelated original worktree edits remain untouched, unstaged and uncommitted, verified against their pre-task SHA-256 hashes. This report is not a production release acceptance claim.

## Historical resumption checkpoint (superseded)

Worktrees are `backups/l19-phase-c-frontend` and `backups/l19-phase-c-backend`, each on local branch `l19-phase-c`; original main worktrees retain the five unrelated changes. Remote main remains frontend `ef920331edbc0990945aca2a78bd96d24e5546fc` / backend `e17ef6744762e933bea9faae035078046c6dd49a`. Production remains backend `fbe679af56fcff0751f426d00b0c7eb0ba80fa3e`, migration `0020_legacy_stories`.

After direct backup approval, recheck live preflight and follow `deploy/L19_VISUAL_PRESENCE.md`. The credential-free backup helper is retained locally outside Git and has never run. Do not infer a verified backup from its existence. The QA runtime was removed; recreate only as necessary. Native model/wheel provenance, synthetic bundles/contact sheets, the 199-test log, PostgreSQL XML (including initial harness failures), focused/full regression XML and local browser scripts remain reproducible outside Git or in the committed test harnesses. Never reuse old session tokens or another account; obtain a fresh normal login to the authorized permanent QA account.

## Known scope limits

Restrained source-pixel mouth/eyelid deformation is not phoneme-accurate lip sync or photorealistic blink closure. No actual voice cloning, full-body/semantic gestures, recording/video export or camera capture. Difficult photos require recropping. Automated Chromium/software-WebGL checks do not establish physical mobile-device performance or human microphone/listening acceptance. Delivered bytes cannot be remotely retracted; in-page imagery is revoked through short authorization leases and local lifecycle fences.
