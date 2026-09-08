# L19 staged production release / rollback

This runbook does not assert that release has happened. See the Phase C report
for the accepted SHAs, actual backup verification and remaining gates.

## Required preflight

Verify the authoritative host, clean production checkout and exact deployed and
remote SHAs. Use the repository owner for Git. Verify the configured **and
connected** database is exactly `legarya`, with revision `0020_legacy_stories`
before the first L19 deployment. Backend, personality and media workers must be
healthy; private S3/SSE-C must be configured and resource headroom adequate.

Before migration, obtain a fresh custom-format backup through the authorized
protected-backup procedure. Keep it on-server, in a restrictive directory with
file mode 0600; verify nonzero size and successful `pg_restore --list`. Record
the exact path/size without displaying customer rows or credentials. A backup
authorization/verification failure is a release stop, not a reason to skip it.

## Deployment order

1. Publish only reviewed Phase C changes; preserve unrelated local changes.
2. Pull the exact accepted backend commit with `git pull --ff-only`; no reset,
   force-push or unreviewed branch history. Keep `VISUAL_PRESENCE_ENABLED=false`
   and `VISUAL_PREPARATION_ENABLED=false` during schema/runtime setup.
3. Install only required, tested backend dependency changes. Run migration
   `0020_legacy_stories` → `0021_visual_companions` after rechecking the backup
   and database identity. Do not downgrade production to validate a migration.
4. Restart the backend normally and verify `/health` and all existing workers.
5. Provision a private `/opt/legarya-visual/venv` using the accepted CPython
   3.14.4/Linux x86-64 environment. Install backend requirements for the worker
   and enforce `deploy/visual-native-linux.lock` with `--require-hashes` and
   `--only-binary=:all:`. Run `pip check` and record the full package inventory.
   Keep applicable wheel/model licenses and notices. The API process must not
   import MediaPipe or inherit the native dependency closure.
6. Provision the vetted `face_landmarker.task` from the numbered manifest
   source. Verify exact size 3758596 and SHA-256
   `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`.
   Use root-owned runtime files readable by the service group, not writable by
   the worker; private model file mode 0640 and parent directory 0750. Do not
   put weights or private portraits in Git or any HTTP/static directory.
7. If required, privately extract the accepted OS-library pins from the model
   manifest into `/opt/legarya-visual/sysroot`; do not change global linking or
   install native dependencies in unrelated production virtual environments.
8. Add only the scoped `VISUAL_WORKER_PYTHON`, `VISUAL_MODEL_PATH` and optional
   `VISUAL_NATIVE_LIBRARY_DIR` settings, preserving `.env` ownership/mode and
   unrelated values. These paths must match the actual verified installation.
9. Install `waffleberry-visual-worker.service` and the visual-health service/timer.
   Use a normal daemon reload and start the worker with preparation still gated.
   The parent needs DB/S3 networking; the native child retains Landlock/seccomp,
   a credential-free environment, no sockets and no request-time downloads.
10. Deploy the accepted frontend commit to Vercel. Verify versioned imports,
    existing Rya/L12/L15 behavior and capability-disabled fallback.
11. Enable the L19 flags for authorized QA, restart only necessary affected
    services, and run normal authenticated synthetic prepare/preview/approve,
    Legacy-call, disable/replacement/delete, access-isolation and zero-factual-
    effects checks. Track exact synthetic storage objects/versions for erasure
    proof without arbitrary bucket listing or customer data access.
12. Verify intended final flags, worker/private storage/health/logs and cleanup.
    Only after every mandatory gate passes, record final backend/frontend SHAs
    and tag the final **backend** commit `legarya-l19-visual-speaking-companion`.

## Restricted operational verification

The worker handles one global preparation at a time. Its unit caps memory at
768 MiB, disables swap, limits CPU to 50% and tasks to 64, and uses private temp
storage, a non-root service identity and no extra privileges. The child keeps
its existing address-space, deadline, cancellation and process-group controls.

`waffleberry-visual-health.timer` runs once per minute. The oneshot command
`python -m scripts.l19_visual_health_check` emits only aggregate erasure status,
counts, oldest age and allowlisted error codes. Initialization errors are also
sanitized. Error status exits nonzero so the unit visibly fails; operators can
check `systemctl status waffleberry-visual-health.service` and its journal.
This is local operational monitoring, not a claim of an external paging service.

The Phase B thresholds remain: oldest pending purge 3600 seconds, repeated
failure attempts 5, and per-Legacy cleanup backlog admission stop 2. Do not add
a public health endpoint exposing portrait/source metadata. Verify this timer
and structured signal on the target host before acceptance.

## Rollback

Disable **both** presentation and new preparation flags, restart only affected
services, and retain the erasure worker so already requested purges complete.
Do not delete Memory/Timeline/Story records or downgrade/drop the additive
schema as a routine feature rollback. Disable/remove frontend entry as needed
while preserving working voice calls. A code rollback must be reviewed for
schema compatibility. Database restoration is a separately controlled recovery
operation, not an automatic response to a failed visual QA case.
