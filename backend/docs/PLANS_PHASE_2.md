# Plans phase 2 — shadow accounting

## Boundary

This release measures usage. It cannot enforce a plan: `enforcement_enabled` is
always false, and no chat/call/upload/create route branches on a usage decision.
`PLANS_TRACKING_ENABLED` defaults false; enable only after migration 0023.
Switching it off stops hooks and the periodic reconciliation task. Existing
accounting tables remain intact; operational rollback does not downgrade them.

Normal accounts resolve to Free without modifying their authentication records.
Plus/Pro are definitions only, with no checkout or public plan-mutation route.
The operator CLI can grant exactly the approved verified testing account an
ID-bound exemption, after separately confirming its ID. Exempt users are still
measured. Their data/permission/security rules are unchanged.

| Per account | Free | Plus | Pro |
| --- | ---: | ---: | ---: |
| Rya messages/day | 40 | 120 | 400 |
| Legacy messages/day | 40 | 120 | 400 |
| Rya call seconds/day | 60 | 180 | 600 |
| Legacy call seconds/day | 60 | 180 | 600 |
| Owned Legacies | 1 | 3 | 10 |
| Uploaded original bytes | 100,000,000 | 1,000,000,000 | 5,000,000,000 |

Daily resets use midnight UTC, independent of streak timezone and UI language.
Memory caps remain undecided and unenforced. Legacy cloned voice remains deferred;
any existing standard-voice Legacy call uses its own legacy-voice meter, without
claiming to offer cloned voice. Ancillary features retain existing safeguards.

## Durable vs best-effort data

- Text receipts mirror durable `ConversationTurn` outcomes, never provider-call
  counts. A pending turn holds one shadow reservation; completed consumes one;
  failed/interrupted releases it. Same operation upserts rather than increments,
  so repeated reconciliation and terminal replay cannot charge/refund twice.
- Receipt keys include turn ID and admission timestamp. Deleting conversation
  history does not delete usage receipts. Account deletion cascades its receipts.
- Standard dictated text counts as a text reply. Internal realtime transcripts
  do not also consume text messages. Public HTTP schemas cannot submit realtime
  input mode. Admission day owns a text receipt crossing midnight.
- Call intervals follow server-confirmed ready state and existing DB-fenced
  connection generations. Connecting/reconnecting gaps do not count. Intervals
  retain microseconds and split at UTC midnight, then report milliseconds.
- An unexpected process death has no perfectly knowable disconnect instant.
  Recovery retains the confirmed lower bound, flags an uncertain tail, and never
  assumes the whole remaining allowance was used. Missing ready tracking can
  resume from a later confirmed heartbeat, explicitly flagged as uncertain.
- Text repair scans at most 500 eligible turns per pass, cycling so pending
  sources are revisited. It does not steal claims, retry providers, or change
  canonical messages/memory. A process-crashed pending turn remains pending until
  the existing turn-recovery policy resolves it; do not guess success or failure.
- If accounting fails AND the source turn is deleted before reconciliation,
  historical usage cannot be reconstructed. Fixed degraded log events must be
  investigated; shadow data is not claimed complete under arbitrary outages.
- Optional accounting uses savepoints without committing the caller's product
  transaction. Savepoint rollback expires ORM state before the caller continues,
  preserving earlier Core-update turn claims. Product-write failures still follow
  the existing rollback/error paths rather than being swallowed.
- Capacity is derived from canonical ownership and physical original-artifact
  records. Collaborator uploads belong to the owner. Uploads in progress reserve
  declared bytes until expiry; verified stored originals consume actual bytes.
  Purge-pending originals count until purge; derivatives and DP selection do not
  duplicate the original's charge. This is measurement, not atomic cap admission.
- Auxiliary HTTP request outcomes (STT, read-aloud, preview, story generation,
  media retry) use a bounded queue drained to separate receipts. Cache hits are
  recorded separately. Queue overflow/storage failure/process death can lose
  auxiliary metrics: they are explicitly best effort, not a billing ledger.
  Existing provider token/audio telemetry remains separate; no dollar costs are
  invented for missing provider measurements. Retained story/access counts are
  inventory, not generation-attempt totals.

## Interfaces and privacy

`GET /api/v1/plans/usage` returns only the authenticated user's shadow snapshot,
with private/no-store caching. It has no `user_id` selector and no mutation route.
It reports limits, used/reserved/released values, UTC reset, capacity,
`would_block_next`, tracking/reconciliation timestamps and uncertainty flags.
`would_block_next` is illustrative: storage additionally needs the proposed file
size, and real concurrent admission belongs to phase 3.

No message text, document contents, audio, email, URLs, tokens or transport tickets
are stored in usage receipts or telemetry queues. Logs use fixed event names,
never SQL/exception strings. The operator script prints only approved account ID,
verification/exemption status or numeric usage snapshots.

Frontend changes add a fresh `client_turn_id` per submitted message. Existing
authentication transport retry reuses that request body/key. An intentional new
submission gets a new key. The UI still does not auto-resend a failed message.

## Deployment and rollback

1. Require a clean production checkout matching the audited baseline and healthy
   existing services. Verify the configured database identity is `legarya` and
   revision is `0022_legacy_deletion` without printing credentials/content.
2. Keep tracking off. Apply additive migration `0023_plan_shadow_usage` with
   bounded lock/statement timeouts. It creates four accounting tables only and
   initializes a tracking start marker; it does not rewrite customer rows.
3. Resolve the exact verified testing account with:
   `python -m scripts.plan_usage_admin verify-testing-account`.
   Grant using `grant-testing-exemption --user-id <verified ID>`; never infer an
   ID from an email entered by the frontend. Repeated grant is idempotent.
4. Set `PLANS_TRACKING_ENABLED=true` for the API via the supplied systemd drop-in,
   reload systemd and restart only the API. No worker/provider configuration,
   call policy, storage backend, payment configuration or credentials change.
5. Verify health, schema, flags, testing-account snapshot, auth denial for the
   public usage endpoint, existing service states and absence of degraded shadow
   events. Publish the compatible frontend turn-key change and verify assets.
6. Rollback: remove/disable ONLY the new API tracking override and restart API,
   or return to the previous application commit. Keep 0023 tables/data; do not run
   a schema downgrade, clear counters or touch customer content.

Example operator reads:

```
python -m scripts.plan_usage_admin snapshot --user-id <verified ID>
python -m scripts.plan_usage_admin reconcile
```

No public UI usage panel, payment buttons, subscription prices, reward grant,
voice-cloning implementation or quota enforcement is included.

## Next gate: phase 3 is not automatically authorized

Observe normal shadow traffic and reconcile anomalies before enabling any limit.
Phase 3 still needs concurrency-safe actual admission for each cap, durable
bounded voice reservations, warnings/cutoff handling, dedicated quota UI in all
languages, and a separate enforcement rollback switch. Fix any uncertainty or
lost-accounting paths that would make hard enforcement unfair. Existing 30-day
Plus reward copy is not a backend entitlement grant; its contract is separate.

## Validation record

See the release report for final test counts and deployed revisions. Dedicated
tests cover duplicate terminal receipts, failure/refund recovery, ledger outage
isolation, over-limit shadow continuation, owner-vs-actor attribution, UTC split,
reconnect gaps, real app WebSocket lifecycle, storage reservation/expiry/purge,
private self-only snapshots, exact exemption checks, additive migration, and
real PostgreSQL concurrent writes/savepoint failures. Legacy migration reversal
tests are pinned to their historical revision; a separate 0023 test covers the
new additive migration and deliberate refusal to destroy accounting on downgrade.
