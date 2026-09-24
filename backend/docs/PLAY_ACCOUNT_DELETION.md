# Play account deletion — implementation audit and runbook

Status: application implemented in source; **application rollout pending**.
The operator accepted cloud/offsite inventory and one historical unjournaled
visual PUT as release exceptions, not independently verified resolutions. The
follow-up [storage hardening record](PLAY_DELETION_STORAGE_HARDENING.md) defines
and implements backup expiry, independent recovery and cancellable new S3
writes, and supersedes the earlier source-only operational findings below.
The standalone backup expiry control is active for its protected 39-file root.
The newly discovered predecessor PostgreSQL database and 21 exact historical
backup/SQLite/companion files were permanently removed with explicit user
authorization, with live LegaRya preserved; see the hardening record. The application is
NOT deployed. Backend baseline
`7f267eae1a659aee621656e6c5c12acdeafac42a`; frontend baseline
`4e0add14cd6e7649fb215d60dcb941e6a2ded84e`. Work is isolated on
`play/account-deletion`. Read-only deployment inspection occurred in the
2026-09-24 follow-up, followed by scoped synthetic S3 acceptance and installation
of standalone backup expiry after deployment authorization, then the separately
authorized predecessor-store retirement. No application deployment or live
LegaRya migration/data deletion occurred.
The operator-created independent journal volume is now persistently mounted,
and durable SMTP alerts/volume-and-expiry monitoring are active. Journal SQL
binding and account-worker monitoring must be activated with the application
rollout; provisioning storage does not itself activate account deletion.

## Pre-implementation inventory and ownership contract

All 49 accepted model tables were inspected, including foreign keys without an
`ondelete` action, JSON provenance, non-FK voice requester IDs and email-addressed
authentication/invitation records. There is no supported ownership transfer.
Owned Legacies therefore use the existing irreversible Legacy erasure process.

Canonical memories, revisions, relationships, timelines and stories are scoped
to a Legacy, not to a contributor. Existing authorization gives its owner
deletion authority; contributors cannot delete canonical history. Deleting a
contributor removes account attribution and private conversations, not another
owner's canonical history. It is not semantic redaction of every mention of a
person in another account's content. Such a request requires separate review.

Source uploads are different: the uploader and owner can read them, while other
collaborators cannot. This flow purges the deleting user's uploads even under a
different owner's Legacy, removes their private extracted evidence/drafts, and
detaches source-support links without deleting already-canonical memories or
stories. Sources uploaded by other users are not selected. Visual/voice owner
consent cannot legitimately belong to a different owner's Legacy in the current
model; an inconsistent historical ownership/consent linkage must fail closed for
operator review, never erase another owner's profile to force completion.

| Exact tables | Account deletion contract |
| --- | --- |
| `users` | Irreversible deleting marker; replace refresh credential fingerprint; remove login/profile/settings row only after cleanup. |
| `auth_challenges` | Remove verification/reset/authorization artifacts addressed to this normalized email, including consumed artifacts. |
| `legacies` | All owned Legacies enter existing safe deletion, no ownership transfer. |
| `legacy_access_invites`, `legacy_access_events` | Remove invites addressed to/by/accepted by this user, and identity-bearing access event records. Owned-Legacy records cascade. |
| `legacy_collaborators`, `legacy_viewer_accesses`, `legacy_visitor_profiles` | Remove this account's membership, visitor identity and permissions; do not delete others' Legacy or membership. |
| `conversations`, `messages`, `message_web_sources`, `conversation_turns`, `turn_effects` | Fence active turns; remove all conversations owned by this user, including private visitor conversations and their children. |
| `memories`, `memory_revisions`, `memory_entities`, `memory_entity_links` | Delete in owned Legacies. Else retain Legacy-owned canonical content; null account attribution and deleted personal conversation/message provenance. |
| `legacy_personality_profiles` | Delete owned-Legacy projections; invalidate affected shared-Legacy derived projections when their private source support is removed. |
| `life_events`, `life_event_memories`, `life_event_evidence`, `life_event_entities` | Delete owned scope; retain other owners' canonical events, remove this user's identity/source associations where applicable. |
| `stories`, `story_versions`, `story_chapters`, `story_support_links` | Delete owned scope; retain others' canonical stories while removing deleted source evidence links and account attribution. |
| `media_sources`, `media_artifacts`, `media_processing_jobs` | Fence and purge all owned-Legacy sources plus sources uploaded by this user elsewhere; retain exact registry until positive erasure proof. |
| `source_evidence`, `source_memory_candidates`, `source_candidate_evidence`, `memory_source_links` | Remove private extraction/drafts and support links for purged sources; do not remove already-preserved canonical memories. |
| `visual_companions`, `visual_companion_versions`, `visual_companion_assets`, `visual_generation_jobs` | Existing owned-Legacy generation/revocation/positive-absence purge contract, including static display pictures and historical derived assets. |
| `voice_profiles`, `voice_consent_receipts`, `voice_profile_versions` | Existing account-wide L21 hook revokes owned scope, fences versions, purges originals/references and then removes metadata/consent. |
| `voice_jobs`, `voice_assets` | Also cancel this actor's generated preview/message/live jobs in others' Legacies and purge only their exact generated assets, not others' voice profiles. |
| `realtime_sessions` | Revoke all actor sessions/tickets immediately, independent of voice flags; remove with account/conversations. |
| `builder_activities`, `daily_prompts` | Delete owned-Legacy and user-specific rows; null contributor links in other owners' per-Legacy progression. |
| `plan_entitlements`, `plan_usage`, `plan_voice_intervals` | Remove this account's plan/usage rows; no billing-retention exception invented. |
| `plan_tracking_state` | Global accounting reconciliation cursor, not an account record; retain. |
| `account_deletion_reauth` (new) | Store only short-lived hashed proof/session/credential bindings and a rate-limit window; remove at admission or final account deletion. |
| `account_deletions` (new) | Durable parent and minimal pseudonymous deletion obligation: request ID, former numeric account ID, state, origin, timestamps and retry counters. Clear proof/session hashes on completion. This is not anonymous data and has no invented expiry. |

JWTs are stateless: there is no refresh-token/session table. Denial must therefore
check durable User state on every authenticated request and realtime admission;
refresh fingerprint rotation alone is insufficient for access JWTs. New account
IDs must never reuse the deleted identity. Browser preferences/guide state and
transient audio are retired with the existing session lifecycle.

## In-app flow and API

Journey selection has **Account settings**. Builder/collaborator and visitor chat
profile menus also link to **Account settings → Account → Delete account**.
There is no owner-only restriction on deleting one's own account. The flow is:
scope warning → Continue → fresh password or linked Google verification → type
exactly `DELETE` → Delete account permanently. Cancel before submission clears
local proof and inputs; it cannot undo a request already accepted by the server.

The existing bearer-token API exposes:

| Operation | Contract |
| --- | --- |
| `GET /api/v1/account/deletion` | Active authenticated account only; returns safe readiness/reauth information, never storage keys. |
| `POST /api/v1/account/deletion/reauth` | Exactly one current password or Google credential, verified using existing security primitives. Returns a five-minute random proof; only its hash is stored. |
| `POST /api/v1/account/deletion` | Exact `DELETE` plus proof bound to this access JWT and current credential fingerprint; HTTP 202 `deleting` after atomic commit. No client-selectable account ID. |

All account responses use `Cache-Control: no-store`. Validation errors redact
input rather than echoing passwords or tokens. Reauth is durably limited to five
attempts per minute per account. Google verification retains signature, issuer,
audience, verified-email and exact-subject checks and additionally requires an
issued-at timestamp no more than five minutes old. External verification and
password checking do not run while holding SQL locks. Credential changes during
verification invalidate the proof. Reauth alone never schedules deletion.

These routes do not accept refresh-cookie-only authentication; deletion needs
the explicit bearer plus fresh proof and strong confirmation. Existing CORS and
refresh-cookie protections remain. An exact consumed proof/access-token pair
may repeat only the same deletion request while the tombstoned User exists. It
does not permit any normal account access. Other proofs/tokens fail closed; a
completed/missing identity cannot be revived through this endpoint.

On accepted deletion the browser clears account/conversation/guide/reward state,
retires its session generation, aborts pending requests, stops existing Live,
microphone/recording/playback controllers and transient Blob URLs, broadcasts
same-origin tab retirement, and replaces the screen with signed-out auth. A lost
response is not falsely reported as completion. Other devices' old JWTs/refresh
cookies are denied by durable server state; they cannot regain access. Google
sign-up after completion creates a new numeric identity with no old private
state. SQLite also checks the deletion-obligation ID watermark to avoid reuse
in databases created before AUTOINCREMENT; PostgreSQL uses its sequence.

## Durable orchestration and transaction boundaries

The API transaction locks the account, retires realtime sessions/tickets and
active conversation turns, locks the discovered Legacy scopes in stable order,
stages each owned Legacy through its existing deletion service, invokes the L21
account-wide voice hook, stages this user's private uploads in shared Legacies,
removes access/auth artifacts, rotates the refresh credential fingerprint, and
commits the deleting marker together with one parent obligation. Failure rolls
back the whole admission. No object client or model is required for admission.

Parent states are `queued → waiting_for_purge → completed`; completion can also
occur directly from queued when no child cleanup is needed. There is no
reactivation or terminal "give up but pretend deleted" state. Child media,
visual, voice and Legacy purge records retain their existing durable leases and
generation fences. The parent retries indefinitely with bounded backoff (up to
300 seconds), including after process restart. Duplicate requests converge on
one parent; competing finalizers may safely defer and converge on one receipt.

Authenticated writes recheck and lock the active account before ORM flush or
bulk mutation. NOWAIT handles inverted existing Legacy/turn lock order without
holding a deadlocked account writer. Creation of new owned Legacies and private
speech is additionally fenced. Late password reset/Google-linking work rechecks
the account; no new auth artifact can restore a retired identity.

Finalization uses three phases: read a durable registry snapshot; close that
transaction and positively erase exact registered objects/runtime scratch; then
lock and recheck the account, parent, Legacy scopes and unchanged registry. Only
after owned Legacies have disappeared, shared uploaded sources are proved
purged, and actor-generated speech is proved absent are the remaining private
rows removed and the User deleted. FK restrictions remain enabled. Unsupported
historical cross-owner consent/approval links stop completion for review rather
than cascading into another owner's content. No SQL lock spans storage I/O.

## Media, voice and private runtime bytes

Media uses the existing exact-key eraser, including all versions/delete markers
for an exact S3 key. Backend/encryption identity and source prefix are validated;
unconfirmed S3 uploads fail closed rather than claiming absence while a remote
writer may still exist. Storage errors or a false DELETE acknowledgement leave
durable pending state. The registry is retained and rechecked until positive
absence is proved. No account deletion performs an unscoped bucket wipe.

The follow-up replaces new real S3 PUTs with a durable multipart cancellation
journal. Source/visual admission reserves before dispatch; upload handles commit
before private bytes; closing tombstones prevent redispatch and drive bounded,
restart-safe abort/all-version erasure. New unreceived or interrupted uploads
can finish deletion without an object ever appearing. Real Hetzner/SSE-C
acceptance and PostgreSQL interruption tests are recorded in the hardening note.
Historical unjournaled PUTs cannot acquire a cancellation handle retroactively.
The live audit found zero unconfirmed source artifacts but one uncertain visual
PUT with no present versions. That legacy case still requires provider terminal
evidence; a fake digest, timeout override or direct User deletion is not a fix.

The existing L21 hook covers active/replacement/historical profiles, consent,
original/reference audio, generated preview/message/Live audio, running/queued
jobs and purge state. Actor-private speech in another owner's Legacy is selected
by its synthesis requester; only its jobs/assets are purged, not that owner's
profile/reference. Writer deadlines, leases and publication generations fence
late completions. Cleanup is independent of all four voice feature flags and
loads no IndicF5/ASR model. Inference configuration/model pins are unchanged.

L12 private message TTS no longer uses the unregistered process cache. Its
response is no-store and account/message authorization is rechecked after
synthesis; the non-personal fixed preview cache is unchanged. This avoids
unbounded private speech remaining in API-process memory after deletion. It
does not erase a copy already downloaded to a user's device.

New preparation and synthesis scratch live in a dedicated `VOICE_TEMP_PATH`.
Process-crash-safe prefix locks fence active writers; fresh job/account admission
is checked under the lock before files are written. The dedicated serialized
synthesis process also confines the pinned library's unnamed temporary outputs
to its scoped directory. Cleanup proves directory absence and waits if a writer
is active; it never claims success after ignored filesystem errors.

**Activation prerequisites, not verified infrastructure facts:** every preparation,
synthesis and account-cleanup process must see the same dedicated private root
with working cross-process file locks. Do not use a model directory/general OS
temp directory; do not run this synthesis implementation inside a shared API
process because it temporarily scopes the library's process-global temp path.
If workers run on separate hosts with local disks, this implementation alone
does not prove cross-host scratch erasure; configure and validate the shared
root or add an explicitly reviewed per-host acknowledgement scheme first.

**Historical-runtime follow-up:** accepted L21 previously created `prepare-*`
scratch under its voice root and `legarya-synthesis-*`/unlabelled preprocessing
WAVs under OS temp. A process crash could leave unregistered bytes. This change
does not identify arbitrary old unlabelled files by guessing. The subsequent
known-host audit inspected all documented locations and each active service's
private temp namespace: no voice scratch remained, and no voice service/GPU/
table was deployed there. The backup root contains two synthetic historical QA
WAVs now covered by expiry. Before additional worker activation, inventory prior hosts,
stop/drain old writers, identify their private scratch (without broad temp/model
deletion), securely remove only verified private leftovers, and record evidence.
The new gate covers its configured root, not an unevidenced historical fleet.

## Worker and safe recovery

Run from `backend/` using the intended environment's existing settings and
storage credentials. These commands are documentation for later reviewed
activation; no production execution occurred:

```text
python -m app.services.account_deletion_worker run
python -m app.services.account_deletion_worker run --once
```

The account worker is a model-free purge-only coordinator. Keep it running even
when creation/playback feature flags are false. It drains media/visual/voice
purges, invokes the existing Legacy finalizer, then checks due parents. Existing
workers may coexist: claims and finalization are idempotent. Restart normally to
resume; do not delete jobs, reset the account marker or force-delete User rows.

Privacy-safe cycle events report only a bounded outcome. Parent state, attempts,
due time and safe error code expose retry progress to authorized operators. No
password, auth token, private text, object key, transcript or voice bytes are
logged by this path. Inspect pending child states privately, restore the correct
storage backend/key/permissions, and let retries prove absence. A blocked writer
or inconsistent registry is not proof of deletion. Alerting/worker supervision
must be configured during later activation; none is claimed provisioned here.

Migration `0027_account_deletion` follows `0026_voice_live_synthesis`. Upgrade,
empty downgrade and re-upgrade are tested against disposable PostgreSQL.
Downgrade refuses both pending and completed deletion obligations or a deleting
marker; otherwise it would discard privacy obligations needed after restore.
Application rollback must keep schema, fences and purge workers compatible.

## Backups / retention — original pre-hardening review

**Historical review below, superseded by
[the implemented 30-day policy and deployment evidence](PLAY_DELETION_STORAGE_HARDENING.md).**
The policy now exists in code and tests and is active for the inspected host's
protected backup root. Cloud/offsite inventory remains unverified; that prevents
a complete retention-certification claim.

The accepted repository records protected database backup creation, but defines
no enforced backup-expiry window. L21's runbook delegates this to an operator's
reviewed policy; it does not supply one. No production backups were inspected or
changed. A 30-day (or any other) expiry must NOT be represented as existing fact.

Live erasure must not rewrite historical backups. Completed deletion obligations
must be kept outside any restored snapshot and reapplied through the same
orchestrator before restored data is exposed to users. Backup access must remain
restricted. The operator must supply/approve an enforced maximum retention
window, expiry mechanism, and tested restore/deletion-replay procedure before
Play submission. This remains an acceptance/publication blocker until evidenced.

The concrete required operator policy is: name every database/object/snapshot
backup system; specify an enforced maximum expiry for each and evidence of the
expiry job; restrict and audit access; keep a protected current deletion ledger
independent of restored snapshots; restore into isolation with API/login/writers
disabled; migrate to compatible deletion code, replay obligations through the
same service, finish positive purge, then verify no retired account can log in
before opening traffic. Bind ledger entries to their original database lineage
so numeric account IDs are never applied to an unrelated/reset database. Test a
restore containing a previously deleted account and a second preserved account.
The repository does not provision that independent ledger transport or backup
expiry system. Operators must supply/test them; the database receipt alone would
be lost when an older backup is restored and is not a complete recovery policy.

Live deleting state remains until positive cleanup; minimal pseudonymous receipts
remain to enforce obligations. No broad legal/fraud/content-retention exception
is introduced. Receipt expiry cannot safely precede the oldest restorable copy;
its final bounded policy also depends on the operator's evidenced backup policy.
Support email/case records are held by the existing support provider, outside
these product tables; their access/retention must also be reviewed and disclosed
if retained. Public copy states the uncertainty, not a fictitious time limit.

## External request path

Intended (not live) Play Console URL:
`https://waffleberry.app/legarya/delete-account`.
The public static page identifies LegaRya by WaffleBerry and uses the already
configured privacy contact `waffleberry.app@gmail.com`. Ownership verification is
required; neither an email address nor a support email alone authorizes erasure.
There must be no anonymous delete-by-email API.

`delete-account.html` is readable without auth. The source rewrite maps
`/legarya/delete-account` to that page; its assets work at the nested route.
Android bundles the direct file fallback. Privacy section 8 links to the page
and describes real deletion/shared-canonical exceptions and asynchronous purge.
This page/privacy wording remains provisional for the unresolved backup policy.

### Verified support procedure

1. Receive the request through the existing published support address. Do not
   accept a third party's bare email claim or forward as authority. Never ask for
   passwords, Google ID tokens, refresh tokens, voice recordings or documents
   unnecessary for verification.
2. Prefer directing a user with access to the in-app fresh-auth path. For an
   external request, use a human-reviewed, restricted support case to verify
   control of the account address through a fresh independently delivered
   challenge/reply and check the exact existing account identity. For ambiguity,
   compromised mail or disputed ownership, escalate; do not run the command.
   The CLI does not implement mailbox verification or turn its flag into proof.
3. Explain owned-Legacy erasure, shared-canonical exceptions and irreversibility;
   obtain explicit confirmed intent. Record only necessary verification/approval
   evidence under the support retention/access policy, not private product data.
4. An authorized operator resolves the exact numeric account ID in the correct
   environment, checks it twice, and invokes the same admission service:

   ```text
   python -m app.services.account_deletion_worker verified-support-request --user-id VERIFIED_ID --confirm-user-id VERIFIED_ID --ownership-verified
   ```

5. Keep workers running and inspect the privacy-safe parent state. A request is
   "deleting", not "fully erased", until its obligation is completed. Storage
   uncertainty remains pending. Reply truthfully; include applicable backup
   limits only after an enforced policy is known. There is no direct SQL erasure,
   public administrator endpoint or anonymous email-triggered deletion.

## Staging and later production activation (not performed)

Before any activation, resolve retention, historical-runtime and any applicable
unconfirmed-S3-write blockers above and review support/receipt
retention. Review the uncommitted backend/frontend diff and migration. On
isolated staging: upgrade to 0027; start model-free cleanup with creation flags
off; test password and real Google fresh authentication in browser/Android;
delete synthetic owned/shared accounts with actual private storage, interrupt
workers and storage, verify retry/absence/no cross-account damage, and test the
backup restore/replay procedure. Check historical runtime inventory and shared
scratch visibility/locking on every worker host. Verify account settings on a
physical Android device and no microphone/audio/session survives deletion.

Only after separate publication/deployment authorization: deploy compatible
schema/API/purge workers and web/Android assets, supervise retries, verify the
public canonical HTTPS page without a session from outside the network, and
then enter that URL in Play Console. Source routing/local screenshots are not
public deployment proof. Do not enable preserved-voice flags or start L22 for
this task. No production migration, existing backup deletion, application deployment or Play
Console submission was performed.

## Verification evidence

All test accounts/files are synthetic; PostgreSQL is a new disposable loopback
cluster, never production. Coverage includes fresh password/Google proof, redacted validation,
rate/expiry/credential binding, cross-account denial, atomic rollback, expired
refresh/access sessions, flags-off media/voice purge, false DELETE response,
restart/retry, already-deleting and multiple Legacies, canonical shared identity,
actor-private preview/message/Live bytes and stale worker fencing, new Google
identity after deletion, concurrent admission/finalization and I/O lock probes.

Frontend tests cover warning/cancel/fresh verification/exact confirmation,
duplicate submit, logout races, local-state and cross-tab clearing, stale API
fencing, public content and existing voice lifecycle regressions. Separate
network-isolated local browser checks at 375px/1440px use mocked synthetic auth
and deletion responses; public and confirmation screenshots were inspected.
The in-app Browser binding was unavailable, so those checks used a fresh local
headless browser without existing user sessions. No real Google account,
physical-device interaction, live S3 or historical production data was tested.

Original focused evidence (2026-09-24, before storage-hardening follow-up):

| Check | Result |
| --- | --- |
| Account/API/failure tests, scratch tests, disposable PostgreSQL migration/concurrency | 32 passed (18 account, 4 scratch, 10 PostgreSQL). Includes explicit pending-state proof for unresolved S3 writes and runtime cleanup. |
| Full backend regression with account and L21 disposable PostgreSQL enabled | 1,539 passed, 205 skipped, 0 failures; 2 dependency deprecation warnings. Optional external/runtime/other opt-in PostgreSQL gates were not all enabled. |
| Existing Legacy deletion, L21 voice deletion, media sources and positive-absence storage suites | 108 passed. |
| Full frontend suite | 453 passed, 0 failed/skipped; 14 focused deletion/public-page/session cases included. |
| Android deterministic web build and Capacitor sync | Passed; explicit deletion screens/CSS/JS bundled, 134 manifest file hashes verified inside debug APK. |
| Android `testDebugUnitTest lintDebug assembleDebug --offline` | Passed; app unit tests 5/5; app lint 0 errors and 28 existing warnings. No native source/permission changes. |
| Local browser at 375px and 1440px | Public page, cancellation, confirmation, signed-out completion passed; no page errors/overflow. |
| Hygiene | compileall passed; 131 JS/MJS syntax checks passed; both diff checks passed; scoped secret-pattern and artifact scan clear; APK private-key/token/runtime scan clear. Existing public TLS certificate is not a private key. |

Two late-added fail-closed tests are included in the 32-test focused rerun, not
in the already-collected full-run count. Earlier failing assertions/fixtures were
corrected and rerun; a frontend asset-build race was resolved by serializing
test/build writers. These are final results, not a claim that every initial run
passed. Optional external/runtime-gated tests remain explicitly distinguished.

All changes remain unstaged and uncommitted in isolated `play/account-deletion`
worktrees. HEAD, fetched accepted branch and closure tag still resolve to the
starting SHAs. Original dirty worktrees and L21 publication history are untouched.
Model pins/configuration are unchanged. Private QA, PostgreSQL and build output
are outside Git or ignored; no model binaries, QA WAVs, database files, secrets,
logs, APKs or runtime directories are part of the proposed diff.

Google policy reference (reviewed during implementation):
https://support.google.com/googleplay/android-developer/answer/13327111?hl=en
This work does not claim legal review, deployment, Play submission or approval.
