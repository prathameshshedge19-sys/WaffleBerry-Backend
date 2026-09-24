# Account deletion: storage and retention hardening

2026-09-24. Application changes are uncommitted and **not activated**. The known
host's standalone backup expiry control is now installed and active following
the user's deployment authorization. This is not a full production-closure
certificate. No current live LegaRya customer object/row, app service or feature
flag was changed. The user subsequently authorized permanent retirement of the
predecessor database and historical files; the exact removal evidence is below.
Uniquely scoped synthetic S3 acceptance
uploads were created and removed; every owned test scope was verified empty.

## Enforced policy implementation

Protected backups have a **30-day maximum age from original capture**, not from
last access/copy. The local expiry job makes them eligible at 29 days, giving a
one-day safety margin, and runs hourly. The deadline is not extended by touching
or copying identical files. Named historical capture timestamps and an
independent durable hash catalog preserve the earlier age. New backups must be
atomically published into the restricted managed root, never overwritten.

`app.services.backup_retention` governs all regular files in the explicitly
specified root, including dumps, private environment copies, QA media and
auxiliary records. It refuses symlinks, junctions, hard links, nested catalog
paths, mount boundaries, changed registered files and uncertain deletion. It
records capture/deletion intent before unlinking exact expired files. A failed
run cannot issue a healthy receipt. It does not recursively delete directories.
Dry-run does not write the catalog or remove files.

Reviewed host paths:

- Backup root: `/var/backups/legarya`.
- Independent expiry catalog: `/var/lib/legarya-backup-policy` (root-only).
- Independent erasure journal: `/var/lib/legarya-deletion-journal` (restricted
  service access, must survive restoration independently of the product DB).

The source units in `deploy/account-deletion/` include hourly expiry, a persistent
purge worker, minute-level deletion-age health checks and a critical syslog
failure signal. **Only backup expiry/timer and the critical syslog helper are
installed; account-deletion application/health units are not enabled.** Before full release,
route `authpriv.crit`/failed-unit signals to the operator's monitored incident
destination and test delivery. A local journal message is not proof of paging.
Expiry health older than two hours invalidates restore eligibility; on expiry
failure, stop new backup capture, restore service promptly, and do not use an
expired backup to meet an availability target.

Equivalent module commands (the live standalone service runs the same module
from `/opt/legarya-privacy/backup_retention.py`):

```text
python -m app.services.backup_retention --root /var/backups/legarya --state /var/lib/legarya-backup-policy
python -m app.services.backup_retention --root /var/backups/legarya --state /var/lib/legarya-backup-policy --apply --confirm-root /var/backups/legarya
```

This adapter does **not** control Hetzner server snapshots, unlisted offsite
copies, operator downloads or another bucket. Cloud-account inventory was not
available through the installed tools/available cloud-token environment. Those
systems must be evidenced absent or given equally bounded expiry before public
30-day wording or release acceptance. An outage cannot truthfully be reported
as successful expiry. Existing customer data was not deleted to test this code.
The broader historical inventory below found local predecessor stores/backups
outside this root. Those exact stores were subsequently removed with explicit
user authorization. Cloud/offsite completeness is still unverified.

## Independent deletion journal and restore gate

Production requires `DELETION_JOURNAL_PATH` and a UUID `DELETION_LINEAGE`, matching
both the external journal and the product database's singleton lineage. Explicit
`account_deletion_worker initialize-journal` binds them; requests cannot silently
initialize or rebind a restore. The separate journal contains only former
numeric account ID, opaque request ID and timestamps, not email/content/secrets.

After fresh verification and exact confirmation, a write-ahead intention is
fsynced before SQL deletion changes. If SQL rolls back or the process dies, the
purge worker replays that already-authorized intention through the same service.
An accepted erasure intention is irreversible even when its HTTP response is
lost. Debug tests without a configured journal retain transaction-only behavior.

Restore only with API traffic and all writers stopped. Keep the **current**
independent journal volume, never replace it with the restored snapshot. First
run backup `--restore-backup` eligibility (hash, age, recent expiry health). Then
replay erasures with compatible migrated code and the purge worker. Finally:

```text
python -m app.services.account_deletion_worker restore-gate
```

This fails on wrong lineage, a restored deleted account, or missing positive
completion receipts. Backup eligibility alone explicitly reports
`traffic_release: false`. Neither command restores a database or opens traffic.
Pre-lineage snapshots and recovery without the current object registry need a
separately verified recovery plan; do not initialize a new lineage to bypass the
gate. Retain minimum pseudonymous deletion/cancellation obligations while any
restorable copy or unresolved storage obligation exists; this is not permission
to retain account content. Support-provider case retention is separate.

## Interrupted S3 writes

All real S3 source uploads and visual/voice writes now use durable multipart
initiation, 5 MiB parts and completion through the bounded storage process.
SSE-C remains applied. No conditional PUT support is assumed from Hetzner.
`private_storage_writes` survives product-row removal and records endpoint/
bucket/key-identity scope, exact key, phase, upload ID and writer deadline.

1. Reserve before dispatch (in the source/visual admission transaction).
2. Persist the upload ID **before any private bytes**. Every subsequent RPC gets
   a fresh DB admission. Keys cannot be retried or reused.
3. Purge durably closes admission, drains the isolated caller window, aborts the
   known upload and exact-key listed uploads, and requires typed `NoSuchUpload`
   from `ListParts` plus absence of that ID in the exact-key multipart listing.
   Verified Hetzner `*.your-objectstorage.com` endpoints instead return typed
   `NoSuchKey` for a cancelled ID; only that provider-specific code plus the
   listing check is accepted there. An abort ACK, generic 404, `NoSuchBucket` or
   access denial is not proof.
4. Delete and verify all exact-key object versions/markers. Persist erasure.
   Queue retries survive restart and use due times so one failed key cannot
   monopolize the sweep batch. Tombstones continue hourly sweeps for late empty
   initiations. A late initiator cannot obtain admission to transmit bytes.

Lost initiation/part/completion replies no longer require a matching GET that
may never exist. Cancellation is recorded as cancellation, not as a fabricated
successful PUT. Bucket/endpoint changes cannot certify the old journal's key.
All-version checks and existing account/Legacy/publication fences remain.

The operation window bounds **local execution**, not remote commit latency.
Multipart cancellation supplies the remote handle; it does not retrofit one to
an old single PUT. Historical unjournaled uncertain PUTs still need positive
matching completion or independently verified provider terminal evidence.
No elapsed-time override, fake digest or direct User deletion is provided.

Healthy-storage target: active-data deletion within 24 hours, usually much
sooner. Health checks fail at one hour pending and report critical at 24 hours;
these are product operational thresholds, **not a Google-imposed numeric SLA**.
Storage outages/denied permissions require operator remediation; never report
completion to stop an alert. Communicate actual delays to the requester.

## Actual deployment topology: read-only evidence

Inspected host: `WaffleBerry-server`, backend
`/home/waffleberry/WaffleBerry-Backend/backend`, deployed commit
`8a1420543f3b250463b0a1f1a6f5fd45ecbf0283`, migration
`0023_plan_shadow_usage` (not the local L21 branch).

| Read-only check | Observation |
| --- | --- |
| API/media/personality/visual units | Active; user/group `waffleberry`; same backend directory and environment file; each has `PrivateTmp=yes`. |
| L21 voice workers / voice tables / NVIDIA devices | None / zero / zero. No deployed shared GPU worker topology exists to certify. |
| `/tmp`, `/var/tmp`, `/run`, `/home/waffleberry`, `/opt`, `/root` | Each scanned without following symlinks: zero `prepare-*`, `synthesis-*`, `legarya-synthesis-*`, known voice runtime directories, or unattributed WAVs; zero scan errors. Dependency/Git directories excluded. |
| Each of the four live units' `/proc/<MainPID>/root/tmp` and `/proc/<MainPID>/root/var/tmp` | All eight private-namespace paths inspected explicitly: zero voice-named directories/WAVs and zero scan errors. Host `/tmp` alone was not assumed to expose each unit's view. |
| `/var/backups/legarya` | Protected pre-L13–L19, Legacy-deletion and plan-schema dumps/private env copies plus release evidence. Two historical synthetic QA WAVs are under this backup root, not runtime scratch. Nothing removed. |
| Backup expiry timer (initial inventory) | Initially absent; subsequently installed and verified active as recorded below. |
| Source S3 artifacts missing SHA | Zero (read-only aggregate). Repeat at a drained cutover, not a permanent assumption. |
| Historical visual `dispatching` writes | **One**, writer deadline `2026-09-08 21:31:33.364682+00:00`. Exact configured bucket/key identity matches; exact S3 version count 0, marker count 0, untruncated listing, matching committed payload count 0. Still unresolved. |
| Existing visual health | Service failed. Do not dismiss the historical pending-write signal as resolved. |

Metadata-only host scanning did not inspect private file contents. The exact
object check attempted only read-only listing/verification for the registered
uncertain key and returned no object body. No customer key, bucket name, token or private
content is included here. This is evidence for this known deployment host, not
a claim of a complete cloud-account inventory.

## Future voice scratch topology

The prior separate per-service scratch examples are superseded for account
deletion. All reference/synthesis/purge processes must share the same dedicated
persistent `/var/lib/legarya-voice-scratch`, outside per-service `PrivateTmp` and
runtime directories. Use one restricted service UID on this single host and
bind `VOICE_RUNTIME_HOST_ID` to its `/etc/machine-id` and
`VOICE_RUNTIME_ROOT_ID` to the shared root's numeric `st_dev:st_ino`. Production
writers and cleanup fail closed for a missing/private-path mismatch, different
underlying directory in a mount namespace, or different host.
Keep model/cache paths separate and read-only. Both preparation/synthesis locks
must be visible across services. Before activation prove namespace device/inode
equality and cross-service lock contention. The on-disk lock protects live
writers; do not delete another process's work to force completion.

There is no supported second GPU host in this contract. Adding one requires a
reviewed shared-lock-capable filesystem and equivalent verified erasure, or a
durable per-host acknowledgement protocol. It must not silently use local disk.
Local Windows consented QA/model directories are outside Git, not production
service storage, and were not deleted under this production task.

## Known-host backup control activation

After explicit deployment authorization, validated exact non-symlink backup root
`/var/backups/legarya` (root:root 0700) and absent target policy/unit paths. Installed
the standalone stdlib-only policy in `/opt/legarya-privacy` and independent root-only
catalog directory `/var/lib/legarya-backup-policy`. Source/installed SHA-256:
`1d880019fe0ac8af1ead3fb407e869277eb145e78f2778d86eca572f39e01352`.

Dry-run: 39 files, 0 due, 0 removed, no catalog writes. Initial enforced service
run: 39 files registered, 0 due, 0 removed, Result=success, ExecMainStatus=0.
`legarya-backup-expiry.timer` is enabled/active, hourly/persistent. Unit verification
passed (systemd reported only unrelated existing XFS CPUAccounting warnings).
Current plan-schema backup passed hash/age/health eligibility and explicitly
returned `traffic_release: false`, awaiting independent deletion replay.
The installed code also passed a confined Linux synthetic test: expired-file
removal, fresh-file preservation, dry-run without catalog writes, symlink
rejection and unhealthy restore status after failed inventory. Its temporary
test tree was removed; real backups were not changed by the test.
The application checkout remains clean at its original deployed SHA; no app
restart, live schema migration or feature activation occurred. No backup was
deleted by this initial expiry activation; later authorized retirement is below.
This activation supersedes the earlier inventory table's "no timer" observation.

## Broader historical-store inventory and authorized retirement

A subsequent metadata/schema-only scan expanded beyond the protected backup
root. No customer rows, credentials, backup bodies or voice content were printed.
Seven host roots were checked without following links: `/home/waffleberry`,
`/root`, `/opt`, `/srv`, `/tmp`, `/var/tmp`, `/var/backups`; no scan errors were
reported. This found **14 SQL/PostgreSQL dump files outside the managed root**,
some older than 30 days. Their exact metadata was inspected before considering
any mutation. Five backup/cutover directories also contain a SQLite backup,
an environment-file backup and TOC/checksum companions: 19 historical files in
that inspected set. No recognized pg_dump/restic/borg/rclone/duplicity/rsnapshot
command was found in the inspected cron files; this is not proof against manual
copies, service-driven capture or offsite snapshots.

| Historical location | Read-only finding | Current coverage |
| --- | --- | --- |
| `/home/waffleberry` and its `backups/` | Nine SQL/dump files plus a SQLite backup; some predate the current LegaRya cutover. | Outside installed expiry root. |
| `/root/legarya-cutover-20260905-225852`, `/root/legarya-backups`, `/root/waffleberry-backups` | Three dumps and environment/TOC companions. | Outside installed expiry root. |
| `/var/backups/waffleberry` | Two dumps and their checksum companions. | Outside installed expiry root. |
| Predecessor PostgreSQL database `waffleberry` | 14 accounts, 10 Legacies; zero voice profiles/samples; zero other connections at inspection. | Not the current `legarya` account-deletion database; inactivity alone does not authorize dropping it. |
| Three SQLite files under the service home | Each has four accounts/four Legacies and empty voice tables. One is in `backups/`; two are `backend/waffle_berry.db` and `backend/waffle_berry_backup_2026-08-01.db`. | Not covered by current SQL deletion or backup expiry. |

The table records the initial discovery, before the explicit retirement below.

Expanded audio-extension scanning (WAV/MP3/M4A/FLAC/OGG/AAC/OPUS/WebM) found zero
files outside the managed root in these seven scanned roots. That supplements,
but does not replace, the four live services' private-temp namespace inspection.

The operator was asked whether these stores were retired or still served another
product and explicitly replied **"you can delete them"**. Before acting, verified
all four live services use `legarya`, no connection used `waffleberry`, and the
old database's aggregate counts still matched inspection. Each exact file was
validated against its observed size/mtime, canonical path, regular-file/single-link
identity, open-file handles and SQLite sidecars. The two files under the application
checkout were confirmed untracked. Dry-run passed for 21 files / 7,404,271 bytes.

Then recorded a restricted durable retirement intention in
`/var/lib/legarya-backup-policy/predecessor-retirement-20260924.json`, disabled new
connections to the exact old database and dropped **only `waffleberry`**, without
force or terminating connections. Unlinked only the 21 explicit validated files:
14 SQL/dump backups, three SQLite files, one environment backup, one TOC and two
checksum companions. No directory tree or live environment file was removed.
No new recovery copy was made; these database/file removals have no direct undo.
This does not assert absence of unknown cloud/offsite snapshots.

Post-action evidence: old database absent; every one of the 21 paths absent;
only `postgres` and live `legarya` remain among non-template databases. Repeated
seven-root metadata scan found zero unmanaged dump/SQL/archive candidates,
zero SQLite files and zero outside-managed audio files, with zero scan errors.
All four app services and the expiry timer remain active. Live schema remains
`0023_plan_shadow_usage`, application Git status remains clean, and managed-root
dry-run still reports **39 files, zero due, zero removed**. The independent
retirement receipt records completion. This closes the newly discovered local
predecessor-store gap; full release still awaits the other gates below.

## Real S3/SSE-C acceptance

Using the actual configured Hetzner endpoint with bounded SDK calls and opaque
`legarya/deletion-acceptance/<random>/` scopes, verified: active empty MPU listing,
empty cancellation, partial private-SSE-C upload cancellation, completion and
SSE-C readback, deliberately discarded completion receipt, terminal cancellation
response and exact-version deletion. All owned synthetic object versions,
markers and multipart uploads were absent after cleanup. No SQL row, bucket
policy, customer key or feature flag was changed. This is a real provider API
test, not a deployed new-worker end-to-end certificate.

The check exposed two issues not covered by the initial fakes: explicit base64
SSE-C key/MD5 headers are needed by `ListParts`, and Hetzner returns `NoSuchKey`
instead of AWS's `NoSuchUpload` for cancelled handles. Both now have focused
tests, including actual botocore parameter validation, non-Hetzner rejection and
multipart-list confirmation. Initial failed probes cleaned their owned empty
uploads; final three-scenario acceptance passed.

## Final verification of this follow-up

- Full backend regression, with account-deletion and L21 disposable PostgreSQL
  enabled: **1,572 passed, 206 skipped, 0 failures**, two dependency deprecation
  warnings; 1,585.66 seconds. Optional external/runtime/other PostgreSQL gates
  are not represented as passing merely because they skipped.
- Additional visual-worker/visual-concurrency/Legacy-deletion run, including the
  new crash-after-dispatch multipart test on PostgreSQL: **129 passed, 14 skipped,
  0 failures**, two dependency warnings. The 14 skips are SQLite variants of
  tests requiring PostgreSQL. This run used the existing harness's supported
  psycopg2 connection; an earlier psycopg3 invocation failed its driver-specific
  `get_backend_pid()` inspection and was stopped, not counted as acceptance.
- New S3 crash-stage/deletion-race PostgreSQL cases passed (five cases);
  post-provider-fix storage/SSE/retention tests passed (27 cases). The earlier
  full run exposed an obsolete direct-PUT test fake; it was updated for the new
  multipart contract before the final green full run.
- Real synthetic Hetzner/SSE-C and confined Linux expiry checks passed as
  detailed above. These are not a deployed application-worker certificate.
- Python compileall, 131 frontend JavaScript syntax checks, both Git diff checks,
  scoped secret-pattern scanning and model/runtime/binary exclusion checks passed.
  Neither worktree has staged files; changes remain uncommitted (57 backend,
  21 frontend files including prior implementation).
- This follow-up changed frontend documentation only. The original frontend
  453-pass and Android unit/lint/debug-build evidence remains historical; it
  was not rerun or represented as a new device/production validation.

## Operator acceptance and independent-volume/alert provisioning

The operator explicitly accepted items 1 (cloud/offsite backup inventory) and 2
(historical ambiguous S3 write) as release decisions. This is **operator risk
acceptance, not new independent technical evidence**. The historical row must
not be falsely marked completed, and unknown cloud copies are not certified
absent by this record.

The operator confirmed `waffleberry.app@gmail.com` is monitored and created/
attached new 10 GB Hetzner Volume **106945930**, `legarya-deletion-journal`.
Server-side inspection confirmed the exact serial/size/model, no children,
no filesystem signatures and no mounts; the root disk was separately identified
and excluded before formatting. The new volume alone was formatted ext4 and
mounted at `/var/lib/legarya-deletion-journal` with UUID
`3cdddb94-d6d1-4eb0-ac1c-b1de8c61f91c`, `nosuid,nodev,noexec`, owner/group
`waffleberry`, mode 0700. An enabled native systemd mount unit restores it on boot.
A synthetic fsynced file survived stop/start of that mount and was then removed.
The underlying unmounted directory is mode 0000, preventing root-disk fallback.
No application service was restarted and no live database was migrated.

Source journal admission now requires a production `DELETION_JOURNAL_VOLUME_UUID`
and checks both the mounted root and exact block-device identity before opening
SQLite. The purge unit requires the mount; its health check deliberately can
run without it to report failure. Lineage/configuration is staged in protected
`/etc/legarya-privacy-monitor.env`. **The real deletion journal's SQL binding and
account-worker activation still require the application rollout/migration.**
Do not represent an empty provisioned volume as an initialized deletion service.

The standalone SMTP outbox/monitor is installed in `/opt/legarya-privacy` and
uses the existing SMTP credentials without copying them into source. Its
five-minute timer is enabled/active. It checks the mounted UUID, backup expiry
health (maximum two hours), and the expiry timer. Account-worker/deadline-unit
checks are deliberately staged off until those units are deployed, then must be
enabled via `DELETION_MONITOR_ACCOUNT_UNITS=true`. Individual unit checks avoid
systemctl's multi-argument "any active" behavior.

Failures are durably queued on the root disk independently of the journal volume,
retried after SMTP/network outages, and rate-limited. No account identifiers,
private content, object keys, DSNs or exception strings enter emails or logs.
The installed/source alert-module SHA-256 is
`2f5a8d1c00d7747ef8ff86ba7a0d660bcbb034bced32dd8adcb03a16c20fd28a`.
Unit verification, real monitor invocation and the explicit setup-email unit
succeeded. SMTP accepted the test addressed to the monitored inbox, subject
`LegaRya privacy monitoring - setup test`; inbox receipt itself is not observed
from this environment. The old logger-only alert unit was retained as a root-only
configuration rollback copy, not a customer-data backup.

Focused journal/alert/account/runtime/retention tests: **43 passed**, two existing
dependency warnings, after these changes. Compileall and both diff checks passed;
the refreshed scoped secret/runtime scan was clear (62 backend files, 21 frontend
files; nothing staged), with 131 frontend syntax checks passing. The
earlier full-regression counts above precede this narrow follow-up. No
frontend application code, feature flag, production account row, commit, push,
tag or public-page deployment was changed by volume/alert provisioning.

## Remaining rollout work (operator exceptions recorded above)

- Known-host expiry, independent-volume mount and SMTP alert monitor are active.
  Initialize/bind the real journal and enable account-unit monitoring during rollout.
- Historical predecessor database/21 files: explicitly authorized retirement
  completed and verified. Keep future backups confined to the managed policy root.
- Preserve the historical unjournaled PUT as unresolved unless real terminal
  evidence arrives. Its release exception is not a successful erasure receipt.
- Real protocol acceptance passed; verify the deployed durable worker end-to-end
  during the separately gated rollout.
- The user authorized public-page/in-app/backend deployment and subsequently
  accepted the two evidence exceptions above. Recheck drained old writers,
  historical unknown-write counts, shared root and cross-service locks at cutover.

## Application rollout preflight

Public HTTP verification on 2026-09-24 followed the canonical-host redirect and
returned **404** at `https://www.waffleberry.app/legarya/delete-account`. The page
is not live, and neither the in-app flow nor the new backend has been deployed.
Standalone backup expiry and SMTP monitoring are deployed; the independent
journal volume is provisioned. Application activation is still pending.

The frontend's current published `origin/main` is
`4f9f1c1a59f606d361bbb5ab1a995565dba8ce78`, with later saved-chat/voice UI fixes and
an explicit static-web build allowlist. Do not publish the older account-deletion
worktree wholesale: preserve those fixes, add the deletion pages/assets to the
static manifest, and retain the live Vercel build/output settings and routes.
An Android bundle also needs distribution; a web deployment does not update
already-installed native bundles.

Production backend remains clean at the deployed SHA recorded above. Its
refresh-cookie rotation and Android origin patches must also be preserved during
rollout. No application restart, migration, feature activation, commit, push,
tag or Play Console submission was performed in this follow-up.

References: [Google account deletion guidance](https://support.google.com/googleplay/android-developer/answer/13327111?hl=en),
[S3 multipart abort semantics](https://docs.aws.amazon.com/AmazonS3/latest/API/API_AbortMultipartUpload.html),
[Hetzner supported actions](https://docs.hetzner.com/storage/object-storage/supported-actions/).
