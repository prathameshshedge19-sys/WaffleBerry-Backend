# Plans phase 3 — enforcement release

## Scope

Enforces the approved phase-2 definitions: Free 40/60 seconds, Plus 120/180
seconds, Pro 400/600 seconds daily **per experience**; owned-Legacy caps 1/3/10;
owner original-storage caps 100 MB/1 GB/5 GB (decimal). All ordinary accounts
default Free. The exact verified testing-account ID remains exempt, without
changing authentication, membership, or other technical safeguards.

No prices, checkout, paid activation, reward grants, saved-memory cap, new
ancillary-feature cap, or cloned voice are introduced. Existing standard voice
uses the appropriate experience meter. Existing content stays accessible.

## Gates and cutover

`PLANS_TRACKING_ENABLED` and `PLANS_ENFORCEMENT_ENABLED` must both be true.
The new flag defaults false. An immutable, idempotently initialized
`plan_tracking_state[name=enforcement]` timestamp starts a fresh allowance;
no unrestricted shadow-period usage is imported. `quota_` features and
`quota:` receipt keys coexist with the original shadow metrics. Existing
schema 0023 already supports these rows; no schema migration is necessary.

Do not clear/rewrite receipts or the cutover marker on redeploy or rollback.
The CLI `python -m scripts.plan_usage_admin initialize-enforcement` creates
the marker once. Missing marker while enabled rejects new metered work with
a retriable 503, never a misleading exhausted-allowance message.

## Transaction contracts

- Text admission follows scope authorization and idempotent replay lookup.
  An account+experience transaction advisory lock serializes competing
  admissions. After waiting, replay is checked again. Pending reservation and
  durable turn commit together before provider execution. Terminal quota
  receipt and saved reply commit together; failed/interrupted turns release
  their reservation. Live transcripts are excluded. Completed JSON replay
  remains possible at the cap. History deletion does not delete receipts.
- PostgreSQL admission lock waits are bounded to two seconds and statements
  to three seconds. These settings are restored before product execution.
  Accounting-unavailable admission produces 503/retry, with no provider call.
  Enforcement is intentionally not fail-open; the explicit flag is the
  operational escape hatch. SQLite uses a transaction write for test safety.
- Legacy creation uses the existing bootstrap user-first order plus a
  per-owner creation lock. Existing pending setup can resume. Storage uses a
  separate per-owner lock before Legacy/source locks, including receive, so
  finalization/expiry cannot race a new reservation across owned Legacies.
  Replay is checked before capacity rejection. Byte-size and format checks
  remain authoritative. Purge-pending originals retain their storage charge.
- Voice relies on the existing durable unique active-actor session and
  generation-fenced lease as its exclusive reservation: a second call cannot
  consume the same budget, even in another experience. Setup consumes zero.
  Authorization, consume, reconnect, ready, and heartbeat check the budget.
  Confirmed connected intervals split at UTC midnight and exclude reconnect
  gaps. A warning is sent during the final 20 seconds. The existing one-second
  controller check stops capture/provider work at exhaustion and emits a
  distinct `plan_limit_reached` reason. A scheduling/teardown delay may exceed
  the nominal deadline; charged time is clamped to the allowance. This is not
  a hard real-time/sub-millisecond audio cutoff guarantee.
- Process-loss timing remains conservative: confirmed lower-bound time is
  retained, unknown tails are flagged, and never invented. A stale call holds
  its active-actor slot until existing lease recovery fences it. This can
  undercount a short unknown tail; it cannot create concurrent live slots.

Read-aloud, dictation, stories, and sharing retain existing safeguards. Quota
errors do not change tokens, profiles, conversations, memory, or permissions.

## UI

Plans & usage is available on the gateway, Rya chat, and Legacy chat. The panel
shows separate messages/call time, pending usage, next reset in local time
(daily policy remains UTC), capacities, exemption status, and future plan
allowances. It explicitly says paid plans/prices are unavailable. Five-language
copy is registered as a supplement to existing catalogs; personal content is
not translated. Polling is optional, authenticated, bounded, epoch-fenced and
paused in background tabs. Logout clears usage. Quota errors preserve drafts.

## Deployment / rollback

1. Verify phase-2 revision, clean server checkout, healthy API/workers, recent
   reconciliation, aggregate uncertainty/degradation, exact testing exemption,
   and no active calls. Never inspect customer content or expose credentials.
2. Publish compatible frontend and backend; initially keep enforcement off.
3. Initialize the cutover once, install only the supplied enforcement systemd
   drop-in and restart only the API when no active calls remain.
4. Verify both process flags, schema 0023, self-usage response semantics,
   testing-account exemption, service health and anonymous 401 response.
5. Rollback: disable/remove only `plan-enforcement.conf`, daemon-reload and
   restart API safely. Keep tracking on, all receipts, marker and content.
   The compatible UI reports tracking-only mode. No schema downgrade.

Release evidence and exact commits live in the workspace release report.
Tests use synthetic users, fake providers, and a disposable local PostgreSQL.
