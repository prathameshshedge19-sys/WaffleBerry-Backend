# L19 Phase B — Visual Companion Engine & Backend Foundation

Status: **PHASE B ACCEPTED — all mandatory implementation/QA gates passed**. Updated 2026-09-08. This report is included in the single scoped commit `feat(l19): add visual companion engine`; its exact SHA is available from that commit and the closure handoff.

This is Phase B implementation acceptance, not a release. The approved [Phase A](L19_PHASE_A_ARCHITECTURE.md) remains authoritative, with the owner's square-crop decision incorporated. [Model provenance and provisioning](L19_MODEL_MANIFEST.md) are recorded separately. Phase C has not started.

Baseline: backend `fbe679af56fcff0751f426d00b0c7eb0ba80fa3e`; frontend `ef920331edbc0990945aca2a78bd96d24e5546fc`; migration `0020_legacy_stories`. All dirty/untracked files and prior acceptance evidence were preserved in `backups/l19-phase-b-before-closure/`. Unrelated realtime/microphone changes and the L16 architecture draft are excluded.

## 1. Architecture and domain boundaries

The implementation remains precomputed 2D portrait deformation, recipe `portrait_2d_v1`. No talking-head video, face replacement, 3D avatar, voice cloning, identity matching or external inference was introduced.

Four additive tables represent one owner-managed VisualCompanion per Legacy, immutable confirmed versions, three-role registered assets, and separate preparation/purge jobs. Composite FKs enforce Legacy/profile/source/artifact-generation/version scope. Current and desired pointers are separate; ready content is private until a second owner command approves the exact bundle digest and revision. No worker may activate content.

Version admission binds owner, confirmation copy `l19-likeness-v1`, source/hash/artifact/lifecycle generations, crop/rotation, recipe/model and request digests. Same-key replay returns its historical receipt without changing current pointers or enabled state; changed payload conflicts. Quotas remain three admissions per Legacy/day and ten per owner/day. Owner quota locking retains `FOR NO KEY UPDATE`, followed by Legacy/source/profile/version locks.

All setup, mutation and candidate preview operations are owner-only. Collaborators receive only generic owner-managed capability. Active persona viewer access is checked independently of ownership/collaboration and exposes only the enabled, approved, current bundle. Private responses are no-store/nosniff, never storage redirects; content is bounded and authorization/storage identity are rechecked after I/O. Viewer manifests have 15-second leases.

Source deletion fences dependent pointers, increments revision, cancels preparation and durably queues derivative purge within the L16 transaction. A valid current A/candidate B side unrelated to the deleted source is preserved. Companion deletion tombstones presentation state, not L16 originals or canonical memory. Historical receipts cannot undelete; owner erasure remains available when feature flags are disabled or a Legacy is archived.

### L16 zero-factual-effects integration

`processing_purpose` is immutable: ordinary `source_review` remains the default; `visual_reference` is owner-only and image-only. Historical normal-upload request digests remain byte-compatible. Visual references take a full-decoding validation branch before any intelligence provider is constructed. Direct extraction and factual-support admission reject visual references, including ORM and PostgreSQL Core/raw-SQL guards. Ordinary source-review extraction and Personality invalidation retain their behavior.

Real-provider lifecycle tests snapshot values across all non-media/non-visual tables, including existing synthetic canonical memory: no unexpected Memory, MemoryRevision, LifeEvent, SourceEvidence, candidate, Story, Personality, relationship, Conversation/Message, progression/activity, User or Legacy effects. Normal prepare/preview/activate/viewer/toggle/source-delete/purge and two stale-native cases passed.

## 2. Square crop and decoder

Owner approval is bound to the exact physical 1:1 square after EXIF orientation and explicit clockwise rotation. Normalized fractions remain finite and in bounds. Physical width and height must agree within absolute `1e-6` source pixels, with no relative tolerance; fractions on a rectangular source need not be equal. Crop/rotation changes require fresh confirmation.

Uniform square resampling yields 512x512. No rectangular stretching, synthesized padding, background invention or automatic subject selection occurs. JPEG/PNG/WebP are fully decoded, single-frame only; original <=20 MiB, <=24 MP, <=8192-pixel edges, minimum 128-pixel crop. Metadata is stripped. Linux decoder confinement is independently exercised; unsupported confinement fails closed.

## 3. Real provider and offline execution

`LocalPortraitRigProvider` is implemented and exercised with MediaPipe 1.0.1 Face Landmarker, CPU-only, local `face_landmarker/float16/1` model. It receives approved crop pixels and opaque immutable request identity, never names, factual records, auth/S3 credentials or storage destinations. Only poster, texture_atlas and numeric rig leave the child.

The pinned 3,758,596-byte model has SHA-256 `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`. Package/model Apache-2.0 evidence and redistribution conditions are in the manifest. No model, wheel or portrait output is committed.

Linux x86-64 CPython 3.14.4 was actually verified. The supervisor uses a minimal environment, private working directory, anonymous/unlinked input/output handles, process-group teardown, strict bounded output parsing and identity checks. Before native import, Landlock ABI >=3 restricts filesystem access and libseccomp denies all socket families/DNS transport and process-control/exec escapes. Parent-death SIGKILL and parent identity checks prevent surviving native workers.

Native limits remain 120-second parent deadline, 110 CPU seconds, 768 MiB address space, 4 MiB file size, no core dump. QA also used a transient 768 MiB/no-swap/50%-CPU/64-task systemd cgroup, DynamicUser, private network/temp and read-only runtime. No persistent service was installed. Initial allocator SIGSEGV/SIGABRT under the address-space ceiling was fixed with `MALLOC_ARENA_MAX=2`, not a raised limit.

The wheel discloses metrics. No supported telemetry-off switch or claim of zero internal telemetry events is made. OS confinement prevents transport before initialization, and actual initialization/inference succeeds without networking, API keys or request-time downloads. Windows/ARM/musl/GPU and other Python versions are not claimed tested.

### Failure and orphan handling

The real Linux suite covers normal success, deadline, cancellation, child crash, invalid output, memory limit, network/private-file denial, temporary cleanup and parent death. A private owned `l19-native-spool-<uid>` contains only registered `l19-native-*` workspaces with a fixed marker and exclusive flock lease inherited by the native child. Startup reaps only unlocked, owned, nonsymlink registered children; live and unrelated directories are preserved.

An actual MediaPipe-mapped child was observed before its parent was killed; the child exited and subsequent startup removed the orphan. Lease-loss and source-deletion tests commit independent domain changes while a real native child runs: results are stale, no assets publish, the child/workspace disappear and factual snapshots remain unchanged. Failures return fixed safe codes; no fake/remote/static fallback. Fake provider remains explicit test-only. All production feature defaults remain false.

## 4. Portrait quality and resources

The imagegen skill produced a fictional four-adult contact sheet without customer/reference portraits. Generation was online; all inference was local and network-denied. Prompt and fixtures remain outside Git in `backups/l19-closure/`; fixture SHA-256 `d47cd38af39c3a61d113bd4c110b7318bac5f1f88748bb0cd9209b710fbe74d2`.

| Case | Result | Seconds | Peak RSS KiB | Poster bytes | Atlas bytes | Rig bytes | Total bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Clear frontal | Pass | 2.413 | 205540 | 95796 | 385882 | 55461 | 537139 |
| Faded/grayscale | Pass | 2.377 | 205524 | 98501 | 360127 | 55451 | 514079 |
| Glasses | Pass | 2.288 | 205740 | 97260 | 395542 | 55460 | 548262 |
| Valid low-resolution, 192px | Pass | 2.067 | 205256 | 93303 | 244495 | 55458 | 393256 |
| Difficult unusable crop | needs_recrop | 1.662 | Not reported | — | — | — | — |
| Manually selected group subject | Pass | 2.297 | 205460 | 101574 | 301786 | 55603 | 458963 |
| Ambiguous uncropped group | needs_recrop | 1.773 | Not reported | — | — | — | — |

Each successful bundle has a 256x256 poster, 512x512 atlas, 484 vertices and 882 triangles. All 27 neutral/intermediate/blink/mouth combinations pass schema, finite-coordinate, topology, winding, index, patch and size validation. RSS is approximately 200.45–200.92 MiB, below the 512 MiB target and unchanged 768 MiB ceiling. Bundle totals are below 2 MiB, rig below 128 KiB and poster below 100 KiB.

This is a real-pixel, landmark-positioned rig, not a fake-provider grid acceptance claim. Motion is deliberately restrained: eyelid deformation is not photorealistic full blink closure. Automated topology/resource results do not establish subjective likeness or finished animation; owner preview and device/render quality remain Phase C.

## 5. Storage, uncertain writes and purge

`VisualStorage.read_original()` separately bounds original reads to 20 MiB, checks content length, registered version and SSE-C, uses bounded chunks, and terminates the S3 subprocess after a 30-second whole-operation deadline plus at most two seconds shutdown. Generated reads retain the 2 MiB ceiling. No unbounded original reader or SDK retry was substituted.

Before object I/O, asset rows durably register opaque per-attempt exact keys, bucket, backend, key identity, size/hash/version, and reserved/dispatching/confirmed state. `begin_put` fences one no-retry dispatch. Dispatching cannot be replayed. Publication validates all three roles, physical bytes and immutable identity, then rechecks current lease/source/owner/profile/desired version in one transaction. Storage I/O and native work occur outside SQL locks.

Positive exact checksum/size/version reconciliation may prove a lost PUT receipt. That proof commits before exact registered versions/markers are erased. Absence, timeout and killing the local client never prove remote completion. Unknown dispatching writes remain pending indefinitely until positive evidence; they cannot falsely finalize purge. Never-dispatched reservations or positively confirmed writes need two absent sweeps at least 60 seconds apart. No elapsed-time window converts an unknown write to erased.

Storage scope is enforced on delivery, dispatch, publication and purge. A changed backend, bucket or SSE-C key identity produces a safe failure before wrong-adapter I/O and cannot falsely mark objects purged. Originals remain L16-owned.

Purge has priority, runs with feature flags disabled, retries beyond the two-attempt preparation cap, and only reconciles registered keys. Operator `python -m app.services.visual_worker --health` returns restricted counts, ages and fixed codes/times. Structured ERROR logs contain only opaque job IDs, attempts and fixed codes. Thresholds: oldest purge >=3600 seconds, repeated attempts >=5, per-Legacy admission stop at two outstanding cleanup bundles. No existing external paging platform was found; Phase C must monitor the restricted CLI JSON and worker logs and connect operational alerts. No public health endpoint or portrait metadata was added.

## 6. Live S3/SSE-C and cleanup

The user explicitly authorized transfer of the credential-free source/test archive and disposable execution solely at `/var/tmp/l19-phase-b-closure-a1f4c8/`. The earlier transfer rejection was resolved by that authorization, not bypassed. Dependencies and OS libraries were installed/extracted privately. Only necessary S3 configuration was read in memory; no credentials entered the snapshot, reports or Git. No production database was opened.

Two synthetic runs passed:

- `qa/l19-phase-b/28d29851-dd5b-4b27-b7c0-59a31b77cb97/`
- Final sequence D: `qa/l19-phase-b/f5238820-5381-441c-801c-0ada1799c1e3/`

Each registry recorded exactly six object keys before mutation. Checks passed for three-role PUT/read/hash/size, correct SSE-C, missing/wrong-key denial, actual 20 MiB original, unchanged generated-reader bound, initial absence, interrupted result channel, late committed object discovery, erasure and repeated reconciliation. Prefix-restricted object/version listings ended at zero.

The actual bucket has versioning disabled. Real enabled-version delete-marker creation is therefore **not claimed**; version/marker semantics are covered by fake-S3 tests, and actual null-version deletion passed. No bucket settings were changed or arbitrary customer prefix inspected.

Both exact prefixes were independently rechecked at final cleanup: zero objects, versions or markers. After confirming no active QA processes, the validated exact server QA directory was removed, including venv, models, extracted libraries, code and synthetic files. No persistent QA service remains. Credential-free logs, registries, package inventory and metrics are retained locally in `backups/l19-closure/`. Deleted disposable resources are reproducible from retained source/provenance; customer data was never used.

## 7. PostgreSQL migration and concurrency

Fresh retained PostgreSQL 17.11 runtime, loopback `127.0.0.1:55443`, disposable `l19_test_phase_b`, random disposable credentials. Production `legarya` was never targeted.

Final matrix: **74 genuinely live PostgreSQL passes**: 29 domain, 43 worker, 2 migration/raw-SQL guard cases. Combined invocation: **108 passed, 14 skipped**, 122 collected, 106.51 seconds. Other passes are SQLite/DDL; skips are SQLite variants of PostgreSQL-only races, not unrun required PG cases.

All original 68 live cases are preserved. Added storage/relational closure coverage includes durable no-replay dispatch, confirmed-write stable absence, wrong-storage fencing, dispatch-versus-delete in both observed row-lock orders, and duplicate dispatch with one committed winner. Independent sessions/PIDs and `pg_blocking_pids` prove lock contention, not sleep timing.

Existing activation/stale completion, regenerate/delete, source delete/publication/activation, duplicate idempotency, expired leases/two workers, cross-Legacy FK isolation, rollback and late-PUT races remain green. Quota `FOR NO KEY UPDATE` is unchanged.

Unreleased migration `0021_visual_companions` includes remote reconciliation metadata and constraints. Populated upgrade → downgrade to 0020 → re-upgrade passed, with schema/model parity and purpose/factual guards. Migrations <=0020 are untouched.

Final cleanup verified no active test sessions, dropped the exact disposable database and confirmed zero matching databases, stopped the loopback server, and removed the validated cluster/password file. Trusted PostgreSQL binaries and XML/log evidence remain; no production database/service changes occurred.

## 8. Reproduced failures and narrow corrections

- Earlier accepted checkpoint: quota `FOR UPDATE` conflicted with approval FK `KEY SHARE`, reproducing one deadlock in two historical-replay/activation orderings. `FOR NO KEY UPDATE` fixed it without retries or weakening assertions; the final matrix preserves the fix.
- Native virtual-arena allocation failed under 768 MiB; limiting glibc arenas to two resolved it under the same ceiling.
- Final review reproduced two storage-scope failures: wrong configured bucket still delivered HTTP 200, and wrong-adapter dispatch did not reject. Evidence `storage-scope-before.xml`: 2 failed, 1 passed, 1 skipped. Registered scope checks fixed both; unchanged cases then gave 3 passed, 1 opt-in PG skipped (`storage-scope-after.xml`), with the PG variant passing in the final matrix.
- Initial new storage tests matched generic exception text instead of structured `.code`; the harness was corrected without relaxing behavior. An earlier startup-timeout assertion conflated process startup with reaching the test target; the hard deadline was retained and an independent handshake test verifies termination after startup.
- First new disposable PG matrix had 29 setup errors because the fresh public schema was not migrated (77 passed, 14 skipped). The QA helper then supplied a disposable required JWT setting and migrated only this database. All 29 domain cases and the full final matrix passed. This was fixture setup, not a production schema fix or test exclusion.
- Orphan startup cleanup was completed with owned marker/flock registration and actual native parent-death coverage; neither arbitrary temporary directories nor active children are removed.

## 9. Authoritative final sequence

Executed in the requested A–J order after the last runtime-code fix. The clean detached worktree is baseline `fbe679a` plus only intended L19 changes, excluding unrelated realtime edits. Windows commands use the real backend `.venv/Scripts/python.exe -m pytest -o addopts='' -q --tb=short`, not a copied pytest launcher.

| Gate | Result |
| --- | --- |
| A. Linux provider/reference/native, excluding the three B cases | 196 passed, 3 deselected, 0 skipped; 39.92s |
| B. Real native lifecycle, lease loss, source delete | 3 passed, 29 deselected; 6.68s |
| C. Storage unit/fake S3 | 77 passed; 7.43s |
| D. Final live S3/SSE-C | Accepted; six registered keys, zero objects/versions after cleanup |
| E. Focused L19, all nine modules | 388 passed, 109 skipped, 497 collected; 28.04s |
| F. PostgreSQL matrix | 108 passed, 14 skipped; 74 genuinely live PG passes |
| G. L15/L16 regression | 227 passed, 0 skipped; 159.20s |
| H. Full clean backend | 1238 passed, 168 skipped, 0 failed, 0 errors; 1406 collected; 260.80s |
| I. Compileall | Passed for app, alembic, tests and scripts |
| J. Diff/whitespace/scope review | Passed; all 40 intended files checked, including untracked files |

The A/B selectors partition the Linux native module; no mandatory case is excluded. Focused skips are 74 live PostgreSQL cases (F), 14 SQLite-only variants of PG races, and 21 Linux/platform/fixture cases (A/B). Full-suite skips comprise these 109 L19 cases plus 59 existing non-L19 disposable-PostgreSQL opt-in cases. No tests were excluded from the full regression. Starlette/httpx/AnyIO deprecation warnings remain. Linux Python 3.14 also reports future-removal warnings for existing asyncio introspection and the Windows reserved-path check (50 warnings in A, 80 in B); these do not affect the verified pinned runtime and require review before a future Python upgrade.

Final evidence: `native-tests.log`, `native-lifecycle.log`, `portrait-results.json`, `storage-final.xml`, `final-s3-result.json`, `final-s3-registry.json`, `focused-final.xml`, `postgres-final/matrix.xml`, `l15-l16-final.xml`, `clean-full-final.xml`, `runtime-inventory.json` and `pip-check.log` under workspace `backups/l19-closure/`. Earlier checkpoint evidence remains preserved separately and is superseded by this final snapshot.

## 10. Final file scope and handoff

Intended scope is 40 files: migration 0021; visual models/schema/routes/domain, provider, local adapter, native sandbox, reference decoder, storage, health and worker; narrow L16 media purpose/routes/service/intelligence/worker guards; configuration/model imports/router registration/Pillow requirement; nine L19 test modules and helper; four migration-head test updates; S3 acceptance script; three L19 documents.

Excluded unchanged work: backend `realtime_provider.py`, `test_realtime_l15.py`, untracked `L16_PHASE_A_ARCHITECTURE.md`; frontend `realtime-worklet.js` and `realtime-playback-l15.test.mjs`. No frontend code is included.

Final SHA-256 comparisons verified that all 37 intended code/test files match the clean regression snapshot and all five excluded files match their preserved pre-task hashes. The three L19 documents were refreshed into the snapshot separately. No migration <=0020 changed. Final review covered the complete migration, visual model/service/routes/provider/storage/worker paths, narrow L16 integration, tests and documentation. No unresolved Phase B canonical-integrity or timeline issue was found; no conversation/retrieval or timeline service changes are part of this commit.

Production application checkout, services, configuration and database remain unchanged. Authorized disposable QA activity on the same host and synthetic S3 mutations are explicitly disclosed above; this is not a claim of no infrastructure access. No deploy, final L19 tag or Phase C work.

Remaining Phase C: owner Visual Presence/settings and square crop pan/zoom/subject confirmation UI; exact preview/approval UX; private authenticated bundle fetch/allowlist; portrait renderer and safe fallback; local 20 ms output-energy observer; playback-identity/reset fencing, speaking/listening/thinking states and barge-in neutral reset; mobile/reduced-motion/device/likeness quality; resource disposal and five-second refresh of 15-second viewer leases; isolated worker/model provisioning and operational health alert hookup; separately authorized production release. Already delivered bytes cannot be remotely retracted. Preserve Rya pronunciation/renderer/voice, L15 audio ownership/receipts and existing per-caller Cedar/Marin policy.

All A–J gates and disposable cleanup are complete. The single scoped Phase B commit is `feat(l19): add visual companion engine`. The documented backend deployment is a separate server pull/migration/restart procedure and is not executed by this closure. Stop after Phase B; no release tag.
