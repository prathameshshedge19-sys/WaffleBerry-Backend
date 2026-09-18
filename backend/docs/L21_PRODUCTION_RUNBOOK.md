# L21 production preparation and feature-gated activation runbook

**No production deployment or migration has been performed by this phase.**
This is a later operator procedure, not permission to run production commands.
Preserved Live MUST remain OFF until independently authorized L22 qualification.
The final local evidence and limits are in [L21_FINAL_ACCEPTANCE.md](L21_FINAL_ACCEPTANCE.md).

## Deployment boundary and prerequisites

The API owns authenticated control-plane requests; it never imports/loads models.
Run one dedicated synthesis process per assigned GPU and a separate enrollment
process. Run the **purge-only worker continuously even when all flags are OFF**.
Do not instantiate multiple synthesis services on the same GPU. DB leases fence
publication across processes; the provider lock serializes threads inside one
process. This is conservative resource safety, not multi-worker capacity sizing.

Use PostgreSQL, private encrypted S3-compatible storage and dedicated least-
privilege service accounts. Local storage is a synthetic debug/test adapter,
not encrypted production storage. Set LEGARYA_DEBUG=false and explicitly select
MEDIA_STORAGE_BACKEND=s3 with endpoint/bucket/region/credentials and SSE-C key
and key identifier. Validate TLS, key access and exact-version erase/list/head
permissions in staging. No public bucket, signed public media URL or CDN caching.
Put secrets only in an operator-managed, restricted environment/secret store;
do not place them in source, process arguments, support dumps or client bundles.
The API's JWT secret is not supplied to the model request.

## Immutable artifact staging and environment installation

Stage approved artifacts once, outside the checkout, using a provisioning identity
that is **not** the runtime service identity. Do not fetch models on startup.

- IndicF5: ai4bharat/IndicF5 @ ba85abedf18dc479a447eaa0eccbd76ab78a47d5.
- IndicF5 source: 13f7c4d627cc10111aea8fe9c0039462cacacdc7.
- Vocos: 0feb3fdd929bcd6649e0e7c5a688cf7dd012ef21.
- Manifest: backend/voice-models/indicf5-manifest.json; canonical digest
  7d07242ee1adf5a644df4447bd4dbda59583e4c6b7bb1df1ab48142cb027fb2f.
- All six required artifact sizes/SHA-256 must verify before imports/weight load.
  Missing/mismatched files, incompatible checkpoint keys or missing CUDA fail closed.
- NFE48 / CFG1.65 / sway−1 / speed0.97 / crossfade0.10 / RMS0.1 remain fixed.

Create an isolated Python 3.12 worker environment. Install the API/DB dependencies
from requirements.txt plus the **separate** requirements-voice-synthesis.txt into
that environment, not into the API image. Install the pinned source commit, never
a branch. Build and archive an immutable wheel/image dependency lock and hashes for
the target OS during staging; the checked-in direct pins are not a full transitive
lock or proof that a Windows environment can be copied onto Linux.
Use requirements-voice-worker.txt for the separate enrollment worker (see the
L21.3 document), with its pinned local Whisper snapshot. No moving revisions.
Run pip check and capture the exact environment inventory with the release.

Set HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1. **HF_TOKEN is not a runtime
requirement**; remove hub tokens from worker environments. Restrict model network
egress; DB/private storage access is still required. Models and source are
read-only to service users. Verify hashes through the same load_and_verify_manifest
function that startup uses; compare its canonical digest, not raw JSON whitespace.
Warm-up is a genuine valid-output pass, not merely a process or CUDA presence check.

## Service contract (example for later Linux staging)

From the installed backend working directory:

```text
/opt/legarya/voice-venv/bin/python -m app.services.voice_synthesis_worker --poll-seconds 2
/opt/legarya/reference-venv/bin/python -m app.services.voice_worker --poll-seconds 5
/opt/legarya/api-venv/bin/python -m app.services.voice_worker --purge-only --poll-seconds 5
```

The synthesis environment explicitly supplies VOICE_SYNTHESIS_PROVIDER=indicf5,
VOICE_SYNTHESIS_DEVICE=cuda, an absolute read-only artifact path, manifest path
and the digest above. Startup requires cloning and either message or staging-Live
enablement. It does not enable the API's flags itself.
Purge-only requires neither flags nor Whisper/IndicF5 dependencies.
Never co-locate reference/purge processes in a shared temporary directory.

Example synthesis unit; substitute reviewed paths/identities, validate with
systemd-analyze verify in staging, **do not deploy this example automatically**:

```ini
[Unit]
Description=LegaRya private preserved speech worker
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=notify
NotifyAccess=main
User=legarya-voice
Group=legarya-voice
WorkingDirectory=/opt/legarya/backend
EnvironmentFile=/etc/legarya/voice-worker.env
Environment=HF_HUB_OFFLINE=1
Environment=TRANSFORMERS_OFFLINE=1
Environment=TMPDIR=/run/legarya-voice-synthesis
Environment=MPLCONFIGDIR=/run/legarya-voice-synthesis/mpl
RuntimeDirectory=legarya-voice-synthesis
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=no
UMask=0077
ExecStart=/opt/legarya/voice-venv/bin/python -m app.services.voice_synthesis_worker --poll-seconds 2
Restart=on-failure
RestartSec=10
TimeoutStartSec=900
TimeoutStopSec=45
WatchdogSec=650
TimeoutAbortSec=10
KillMode=control-group
LimitCORE=0
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/run/legarya-voice-synthesis
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
```

Do not enable PrivateDevices for a CUDA worker. Confirm required NVIDIA device
permissions and driver compatibility in staging. Add host memory/PID/tmp-volume
quotas from host capacity validation; GPU throughput qualification is L22.
Run enrollment and purge with distinct runtime directories/accounts. Enrollment
VOICE_TEMP_PATH belongs to its own private runtime directory. A purge service
must not sweep another live process's preparation directory.

READY=1 is emitted only after verified artifacts, successful CUDA acoustic/Vocos
load and validated warm output. WATCHDOG=1 is refreshed during the service loop
and active synthesis checks; SIGTERM/SIGINT stop admission and request cooperative
join. A native CUDA hang cannot be forcibly interrupted in Python: stop/watchdog
timeouts kill the entire cgroup; leases and registered assets recover on restart.
RuntimeDirectory cleanup removes crash remnants, including pinned preprocessing
tempfiles that could survive an exception before returning their filename.
Normal returned preprocessing files are removed in finally. Keep runtime storage
private/quota-bounded; alert on unexpected file accumulation and restart safely.

The notify/watchdog/stop and runtime-directory semantics follow the upstream
[systemd service contract](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml)
and [execution environment contract](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml).
The Linux unit itself was not run on this Windows QA host.

## Readiness and health operations

Check service state plus the current process's voice_worker_ready event, not an
old journal line or existence of a PID. Check watchdog/restart count, last
successful cycle and oldest queued/running lease against configured bounds.
Run a synthetic preview/message smoke to validate DB, storage and authorization:
model warm-up alone does not prove remote storage availability.
API 202 means queued, **not a claim of healthy or completed inference**.
Unavailable workers remain bounded by admission and caller timeouts; messages
offer same-text standard fallback; staging Live falls back before first preserved
audio. Never regenerate the brain answer to hide a synthesis failure.

Structured voice events allow only fixed event/reason values: startup, ready,
not-ready, manifest failure, admitted/rejected, completed/failed/cancelled,
stale blocked, lease recovered, purge queued/completed and fallback.
Numeric stage telemetry contains no text/key/audio/token. Enable legarya.voice
INFO logging. An admission/purge event emitted inside a transaction describes an
attempt; DB committed state is authoritative after a rollback. Avoid high-cardinality
private identifiers and payload dumps. Alert on oldest purge backlog, not just count.

## Resource bounds and overload policy

- Original upload default 20 MiB; effective storage adapter ceiling 20 MiB.
  Default duration 600 s (configuration max 900), at most two streams/channels
  by default; 96 kHz default ceiling. Decode default 30 MiB (max 50 MiB);
  media-tool timeout 45 s (max 120), selected mono 24 kHz reference 5–15 s.
- Synthesis text <=4096 characters; validated mono PCM16/24 kHz <=120 s,
  configured duration enforced; WAV <=2 MiB (about 43.7 s, thus usually tighter).
  Worker inference deadline default 300 s, max 600. Live waits 90 s by default,
  inside the existing turn lifetime. Cancellation joins before successor work.
- One inference at a time per dedicated GPU service. No speed scheduler/cache.
- At most 64 pending synthesis jobs globally, four per Legacy. Idempotent
  duplicates return the existing job even at capacity. PostgreSQL admission
  uses a non-blocking transaction advisory lock to prevent cross-Legacy races.
  Busy/full admission returns preview 503, enrollment 429, or same-text standard
  message/Live fallback; it does not enqueue an overflow job.
- Historical ceilings: 1,000 synthesis records per Legacy / 10,000 globally;
  32 enrollment versions per Legacy / 10,000 globally. These safety ceilings
  include terminal history. They intentionally fail closed; there is no automatic
  deletion of consent or purge evidence to make room. Monitor capacity well before
  exhaustion; archival/retention changes require review, never ad-hoc SQL deletion.
- Preparation can only follow a bounded enrollment version; ordinary attempts
  are at most three. Purge is **not** admission-limited and retries durably,
  with bounded backoff, until positive absence. Bound storage volume separately.

## Storage/retention/erasure matrix

All objects: server-generated exact keys in one Legacy/profile/version namespace;
private encrypted S3 boundary in production; no raw key or public URL in client
state. Model files are separate immutable non-user artifacts.

| Asset | Retention/expiry | Purge trigger and completion |
|---|---|---|
| Original | Until successful reference preparation; default/max 24 h | Success, expired upload/write, failed preparation retention, revoke/delete; exact-key all-version absence |
| Canonical reference | While authorized version is active/candidate | Replacement, revoke, profile/Legacy/account deletion, interrupted write; same absence proof |
| Owner preview | Default/max 24 h, inaccessible after expiry | Expiry, revoke/delete/replacement; same proof |
| Message speech | Default/max 24 h, bound to authorized stored message | Expiry/lifecycle loss; same proof |
| Staging Live speech | Default/max 24 h; turn/claim-bound | Cancellation/lifecycle loss, expiry; same proof |
| Cancelled/stale/partial | Never playable | Durable reservation retained; writer deadline respected; reconcile then erase/verify |

Cleanup scans original expiry and interrupted original/reference/generated
reservations even when product flags are OFF. Never mark deleted on timeout,
403, uncertain version listing, partial delete acknowledgement or generic 404.
The storage facade proves exact-key version/marker absence plus current absence,
then commits purged/absence-check metadata. Failure preserves purge_pending and
retry state across process restart. S3 operations are bounded child processes;
the 120-second registered writer window exceeds their 32-second client bound.
Use a storage backend whose consistency/PUT completion behavior meets that
contract; absence at one instant is not a promise about an arbitrarily late remote PUT.

Voice-version purge clears reference transcript/manifests and duplicate synthesis
answer text (a non-sensitive tombstone remains to satisfy the existing schema).
Digests, lifecycle and immutable consent evidence remain until authorized Legacy
erasure. Existing conversation/brain content has its own deletion policy and is
not silently removed by voice-only deletion.

## Consent, access and account cleanup integration

Fresh enrollment/replacement requires versioned consent/authority assertion.
Consent is immutable except one-way revocation. This is **not biometric identity
verification** or proof that a recording is the asserted speaker. Preserve
synthetic AI-voice disclosure, no resurrection/personhood claims.

Owner alone manages voice. Collaborators/visitors cannot manage it. Authorized
Legacy visitors may hear their authorized Legacy output. Outsider/cross-Legacy
requests receive non-enumerating denial. Caller cannot choose profile/version/
reference/model/key. Recheck actor, consent, active version, claim and lifecycle
during inference and before publication/delivery. Revoke is immediate fencing,
not a promise that a remote object has already been erased.

Future Play account deletion must call **request_account_voice_purge(db, user_id)**
inside its own account admission-fence transaction and commit. The hook covers
all owned Legacy voice profiles/versions/consents/assets and queued/running work,
owns no commit, and does not delete the account itself. Retry is idempotent.
Keep the purge service running. Wait for every version purged, every registered
asset positively absent and relevant profile deleted; do not equate queued with
complete. Then the existing authorized Legacy erasure finalizer removes voice
metadata/consent/jobs in FK-safe order. Do not delete the user first: consent
owner FKs and retained audit evidence require the parent erasure workflow.
No unrelated account-deletion system is implemented here.

## Restart, failure and backup/restore

- Backend restart does not erase durable voice jobs/profiles. Realtime turn/
  connection loss fences late Live audio; no automatic assistant audio replay.
- Worker death before reservation: stale lease can be reclaimed, new lease token
  rejects old completion; after three ordinary attempts fail safely.
- Death after private reservation/write: expired reservation becomes purge_pending,
  job failed; retry owns a new key. Active profile and canonical brain remain safe.
- Storage/DB failure: no fabricated success; leave durable lease/reservation for
  recovery. Malformed/nonfinite/oversized output never publishes. Recoverable OOM
  fails current job, clears traceback/unused allocator before next serial request;
  driver corruption requires service restart, not altered quality settings.
- Host restart assumes durable PostgreSQL and private object storage survive,
  correct clock and working supervision. Start cleanup first; reverify/warm models.
  Host power-loss behavior is simulated by independent DB leases/process state,
  not claimed as an actual production machine reboot.
- Backups are encrypted/restricted and include revocation/deletion tombstones and
  registered key/version evidence. Do not restore an old active profile over later
  consent revocation. Restore quarantined with ALL flags OFF; replay authoritative
  deletion/revocation records, resume purge and verify absence before service.
  Apply the operator's reviewed backup retention and deletion propagation policy.
  No assertion is made that object erasure instantly deletes offline backups.

## Migration and controlled future activation

1. Back up staging; verify prior revision 0023. Apply reviewed chain
   0024_voice_profiles → 0025_voice_synthesis_jobs → 0026_voice_live_synthesis.
   Check alembic current/head and DB constraints. No new migration is needed.
2. Deploy API/control plane with ALL flags false. Standard behavior must pass.
3. Stage/verify artifacts and isolated worker environment; start mandatory cleanup.
4. In internal staging only, enable cloning/enrollment and enroll a synthetic
   consented QA account. Verify bounded upload, reference transcript/binding,
   owner-only activation and private bytes. No customer voice for infrastructure QA.
5. Start synthesis, require READY and valid smoke; enable message playback in
   staging for owner preview, then exact stored-message speech. Check outsider/
   collaborator denial and permitted visitor playback.
6. Stop the synthesis worker and verify same-text standard fallback. Restart;
   test stale lease/result rejection and no permanently running jobs.
7. Revoke while a job runs; no new admission/late publication. Replace with new
   consent; old reference cannot be selected. Delete voice and prove every asset
   absent through the durable cleanup path, including a temporary storage failure.
8. Review logs/APK/secrets/retention and target-platform smoke; only then seek the
   separate environment activation authorization. Preserved Live stays OFF.

Final shipped defaults:

```text
VOICE_CLONING_ENABLED=false
VOICE_ENROLLMENT_ENABLED=false
VOICE_MESSAGE_PLAYBACK_ENABLED=false
VOICE_LIVE_ENABLED=false
```

Kill switch: set all four false and restart/reload the API through the approved
release procedure (settings are process-cached). Stop synthesis/enrollment,
leave purge running. Standard voice remains available. Keep the additive DB
schema; never require destructive downgrade to disable a feature. 0026 downgrade
refuses while any Live job exists. Earlier downgrades remove voice data/columns;
they are disposable-test capabilities, **not a no-data-loss production rollback**.
