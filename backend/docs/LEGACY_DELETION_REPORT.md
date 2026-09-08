# Owner-confirmed Legacy deletion

Status: deployed and accepted after explicit user approval ("yes always") for the
protected rollback backup, migration, service restarts, and production checks.
No release tag was created. Customer contents were not inspected or used in QA;
the authorized full rollback dump remains exclusively on the production server.

Base commits: backend `146515c4f80fabab1890999d8fd79aa3eed79c89`, frontend
`18e4848fe05ede9680b87238ee5680f4d3b077cd`. Feature commits:
backend `3cbbe16c370c931f02157903953f05e77b5997f6`, frontend
`7afc291b3f073531389c24fd3a06044b3532e14e`, both published to their normal `main`
branches. Work used `backups/l19-phase-c-backend` and `backups/l19-phase-c-frontend`
to preserve unrelated original working-tree edits.

## Product behavior

- Owner-only **Delete this Legacy** in the builder's Legacy selector. Collaborator
  and visitor experiences cannot invoke the control; the backend independently
  verifies ownership. Unnamed/incomplete Legacies use their unambiguous numeric ID.
- The warning identifies the selected Legacy, shows counts, and names memories,
  chats, timeline, saved Stories, uploads, prepared portraits, and revoked access.
- Exact `DELETE LEGACY <id>` plus a separate irreversible-deletion acknowledgement
  are required. The initial focused action is **Keep Legacy**. Opening, cancelling,
  mismatched confirmation, stale selection, or preview failure cannot delete.
- `DELETE /legacies/{id}` returns 202 only after durable admission. A deletion marker
  immediately removes the Legacy from the list and blocks ordinary access. The
  account and unrelated Legacies remain. The gateway explicitly says file cleanup
  may still be running; neither it nor the API claims completed physical erasure.
- Protected backups are not rewritten by this feature; the warning says so.

## Backend safety

Migration `0022_legacy_deletion` adds one nullable indexed timestamp. There is no
data backfill or deletion during migration. Downgrade refuses while an erasure
marker remains, rather than silently losing cleanup obligations.

The admission transaction locks the Legacy and scoped sources, schedules existing
media/portrait purge paths without partial commits, revokes codes, and commits the
marker atomically. Duplicate requests are idempotent. Source admission and canonical
writes share the Legacy lock; authorization refreshes stale identity-map entries.
Existing realtime scope checks refresh access and reject the marked Legacy.

The existing media worker attempts finalization after ordinary jobs, including idle
cycles. It waits for source and portrait purge completion, checks storage identity
and each source's exact registered key, erases only those keys and their versions,
and rechecks the registry under transaction locks. Child-first deletion handles the
existing restrictive evidence/provenance FKs. Active account pointers are cleared;
the account itself is never deleted. A blocked cleanup does not starve another
pending Legacy. Storage errors retain the registry and allow retry.

Operational requirements/limitations:

- The media worker must run to finish deletion, including Legacies without files;
  the visual worker must run when portrait cleanup is pending.
- An uncertain historical S3 upload lacking a committed hash is intentionally
  retained as pending: absence alone is not proof that an earlier remote PUT cannot
  arrive later. Its registry and Legacy rows must not be discarded to report a
  false success. This needs exact-object operational reconciliation if encountered;
  no automatic timeout bypass or broad bucket deletion is provided.
- Production S3, services, and authenticated owner workflow passed the separate
  release checks below. No remote-writer uncertainty was observed in these checks.
- The user explicitly approved this release and asked not to be repeatedly asked
  to publish requested, tested changes. This does not authorize using customer
  data for tests or bypassing application authorization.

## Local verification

- Focused deletion tests: **14 passed** (ownership, confirmation, API, cancellation,
  stale access, file cleanup/retry, other-Legacy isolation, complete linked
  evidence/timeline/Story cleanup, and visual-worker purge).
- Migration/historical preservation tests: **11 passed**. Initial regression found
  four assertions pinning the former head plus one old-schema fixture instantiating
  today's Legacy model. Assertions now expect the new head; old-schema fixtures and
  snapshots compare the original columns without dropping preservation checks.
- PostgreSQL **17.11**, fresh SCRAM-authenticated loopback-only disposable database
  `legacy_delete_test`: **20 passed**. Includes the 14 deletion checks and both
  winning orders of deletion versus upload admission, canonical mutation admission,
  and duplicate deletion. Actual contention is observed through `pg_blocking_pids`
  on separate connections, not inferred from elapsed time. The canonical race tests
  the shared mutation lock, not a live model/provider call.
- PostgreSQL migration upgrade/downgrade/re-upgrade passed. The two full PostgreSQL
  runs used separate fresh clusters; no production database was connected.
- Frontend regression: **301 passed**. Local synthetic headless browser checks at
  1280, 390, and 320 pixels passed for safe focus, exact confirmation, cancellation
  without DELETE, and no horizontal overflow. A simulated failure keeps the dialog
  open, and a collaborator cannot see the control. Browser skill connection was
  unavailable, so the existing project browser test dependency was used.
- Full backend regression: **1282 passed, 188 skipped, 2 existing dependency
  deprecation warnings** (301.85 seconds). The 20 new explicit-URL PostgreSQL cases
  are skipped in the default suite and were run separately against real PostgreSQL
  as recorded above; the other 168 skips are environment-gated baseline cases.
- `compileall`, tracked `git diff --check`, and new-file whitespace checks: passed.

Local cleanup: the disposable databases were dropped, servers stopped, generated QA
credential files removed, and all three exact task-created cluster directories
(including the sandbox startup failure) removed. Content-free test results and QA runner scripts remain under ignored
workspace `backups/`; they are not part of either application diff. Pre-existing
edits in the original backend/frontend working trees have identical hashes.

## Production release acceptance

- Preflight confirmed the expected clean backend base and four healthy services.
- Synthetic-only storage check required the correct SSE-C key, read the registered
  bytes correctly, erased the exact test key through the bounded all-version
  adapter, and confirmed the content unavailable. The two write responses carried
  null version IDs. No broad bucket/customer-prefix listing or deletion occurred.
- Full server-only rollback backup:
  - Path: `/var/backups/legarya/legacy-deletion-20260908T202519Z/legarya-before-0022.dump`
  - Size: **511107 bytes**
  - Timestamp: **2026-09-08 20:25:19 UTC**
  - Verification: **PASS**; configured/connected database exactly `legarya`,
    nonzero custom-format dump, root-owned 0600 file under root-owned 0700
    directories, `pg_restore --list` succeeded without exposing its contents.
- The exact backend feature commit was fast-forwarded into the clean production
  checkout. Migration **0021 -> 0022** completed. Backend, media worker, personality
  worker, and visual worker were restarted and all returned active; `/health`
  returned the expected healthy service response. No configuration/model changes.
- Frontend published through its normal Git-connected workflow. The public chat
  page serves `js/legacy-deletion.js?v=delete1` and the owner deletion control.
- Authenticated browser acceptance used only newly created QA Legacies **15 and
  16**, after verifying they belonged to the dedicated QA account. The active
  target received a synthetic canonical memory through Rya and a synthetic
  document through the normal upload flow. The second Legacy remained unfinished.
- Warning checks at **1280, 390, 320px** passed: target identity, safe initial focus,
  wrong-target text rejected, no horizontal overflow, and Cancel preserves data.
  The server independently rejected a mismatched confirmation and a missing
  acknowledgement with 422.
- Normal UI confirmation removed the target from access; the separate QA Legacy
  remained available. The deletion-preview endpoint eventually returned 404,
  proving the durable marker/Legacy row was gone rather than merely hidden. The
  unfinished QA Legacy was then deleted through the same UI.
- The final original owned-Legacy ID set matched the pre-test set and the QA account
  remained available. No existing QA/customer Legacy was deleted. Browser page
  errors: **0**.
- A read-only audit restricted to these two verified synthetic Legacies confirmed
  their scoped rows were gone. Their exact registered upload was independently
  checked unavailable in production storage after deletion. No direct database
  mutation was used to seed, repair, or erase the QA fixtures.
- Initial acceptance attempts stopped safely before deletion: first during frontend
  publication, then while answering the setup name question, and then because the
  test tried clicking a menu item after Cancel closed the menu. The test was made
  resumable, reused the same fixtures, answered the outstanding name question,
  and reopened the menu. No product safeguards were weakened or application-code
  fix required; the final run passed.
- Production QA fixtures and synthetic storage objects are fully cleaned. Temporary
  server release helpers/registries are removed after retaining this content-free
  record; the protected rollback backup is retained. Final service check: all four
  active, backend healthy. No tag, new milestone, or unrelated changes.
