# L18 Phase C — Stories / Biography Final Release Report

Status: final automated acceptance passed; L18 accepted. Date: 2026-09-08.

## Release identity

- Final application fix: `3f501a0f971cea47e5b81413ade015cdef8667ce` — `fix(l18): finalize grounded story generation`.
- Backend release/report commit: the commit resolved by annotated tag `legarya-l18-stories-biography`. The tag records the application and frontend SHAs. The report-only commit contains no further application changes.
- Frontend: `ef920331edbc0990945aca2a78bd96d24e5546fc` (`ef92033`); no frontend redeployment needed for final closure.
- Migration: `0020_legacy_stories`; no new migration or schema change.
- Production host, database identity and clean checkout were verified against the approved release checkpoint. Operational addresses and private fixture identifiers are omitted from this public report.

## Product delivered

Stories appears beside Memories, Media & Sources and Timeline. The builder supports private Story libraries, explicit first-person Legacy Story versus third-person Biography, bounded synchronous generation, chapter reading, owner editing as a new version, explicit regeneration, stale notices, provenance labels, publication/unpublication and archive. Visitors have a published-only surface with builder provenance and audits redacted. Collaborators have conservative read-only Story access. Text-safe rendering, mobile layout, keyboard focus and accessible status messages are included.

Story remains a narrative artifact, not Memory, MemoryRevision, LifeEvent, SourceEvidence or Personality. Media is optional. Story mode does not change Rya or direct Legacy conversational identity. L19 was not started.

## Previous 422: diagnosis and correction

The earlier attempts had two distinct problems. Initial mixed-perspective output was admitted by the old presence-only detector. Commits `a4e05de` and `ee15828` tightened the detector and prompt, but generation still returned 422. The previous report called that rejected output unsafe without retaining its reason or inspecting the rejected prose; the generic HTTP response did not establish that attribution.

Final diagnosis reproduced the rejection with synthetic live-provider facts matching the QA scenario. First-person output had no subject-named action but included a reference shaped like `I helped my children with their homework`. The strict detector flags every `their`, including references to children, as `perspective_mismatch`. The prompt prohibited third-person sentence subjects while the audit prohibited those tokens anywhere. The declared `MAX_PROVIDER_RETRIES = 2` was unused: one rejection ended generation. The matching Biography passed.

Historical QA Legacies also had null subject names and `collecting_identity` state: the old harness had not completed normal product setup. Final QA reused them, completing normal conversation setup for the primary and isolation fixtures with explicit QA labels. No replacement owner account or auth bypass was used.

The final fix retains the strict deterministic audit unchanged. `generate_audited_chapter` permits one initial attempt plus at most two corrective attempts per chapter. Feedback contains accumulated server-owned audit codes and fixed constraints, never failed prose or provider-supplied instructions. The provider receives those constraints in its instructions and generates fresh prose from the same bounded facts. Every attempt is audited; only passing chapters reach the existing persistence path. Exhaustion retains the audit-failed outcome, with no ready pointer or partial chapter/support graph. No regex rewriting of factual narrative was added.

Corrections cover perspective, unsupported dates/quotation/causality/absolute claims and empty output. DATA, titles and style are explicitly untrusted. Logs contain fixed audit codes and attempt numbers only. The bounded synchronous architecture and schema remain.

Two additional narrow correctness fixes were verified:

- Story creation now explicitly requires the owner; the old builder-access check allowed collaborators to create shells despite the documented view-only policy.
- Replaying an old successful generation key no longer assigns its historical version to the current pointer. Subsequent owner edits/regeneration remain current. Version allocation and row-lock ordering are unchanged.

## Dependency and environment correction

`boto3>=1.35,<2.0` was already declared in `backend/requirements.txt` for L16 S3 storage. The copied `backend/.venv/Scripts/pytest.exe` embeds the older `Legarya Backend/backend/.venv/Scripts/python.exe` path. Direct invocation used that older environment and caused the missing-boto3 collection error.

The intended environment was checked/synced with `python -m pip install -r requirements.txt`; it already contains boto3 **1.43.89**. Authoritative tests used its absolute `python.exe -m pytest`, avoiding the relocated launcher. Production also has boto3 **1.43.89**. No dependency declaration, storage import or S3 functionality was weakened.

## Automated acceptance

| Gate | Final result |
| --- | --- |
| Clean full backend | **905 collected: 855 passed, 50 skipped, 0 failed, 0 errors** |
| Focused L18 domain/API/audit/authorization and PostgreSQL | **28 collected: 28 passed, 0 skipped, 0 failed** |
| Complete live L18 PostgreSQL | **9 passed**, included in both runs above |
| Synthetic live-provider scenarios | **6/6 passed** |
| Exact committed frontend regression | **200 passed, 0 failed, 0 skipped** |
| Backend compileall | Passed for app and tests |
| Git diff checks | Passed |

The full command was exactly `python -m pytest -o addopts= -q -ra`, without exclusions, added skips or xfails. A detached clean worktree at `8b70a7f` received only the six intended closure files. Their hashes matched committed candidate `3f501a0`; unrelated realtime files remained the committed baseline. The 50 skips are existing opt-in PostgreSQL tests for other milestones whose separate databases were not configured. No L18 test skipped. An intermediate 845-pass/56-skip run preceded the final replay/FK coverage; the result above supersedes it.

Frontend testing used a clean worktree at exact `ef92033`. Initial Windows CRLF conversion broke LF-sensitive fixtures; materializing the committed LF bytes resolved that harness issue without frontend changes. The committed revision contains 200 tests; earlier 203-test worktree evidence is not substituted for this revision.

Focused coverage includes the rejected pronoun shape, mixed perspective, valid first person, Biography separation, fabricated quotation/causality/extreme-claim rejection, bounded repair success/exhaustion, cumulative failure constraints, idempotent retries, failed-version rollback, canonical table snapshots, owner-edit replay protection, normal LEG/COL joins, visitor draft filtering/redaction, all owner-only Story mutations, unpublish/archive and collaborator create denial.

## PostgreSQL acceptance and cleanup

The replay correction changes current-pointer behavior, so PostgreSQL was rerun. Used retained PostgreSQL **17.11** at `backups/l16-phase-c-runtime/pgsql`, a fresh cluster, loopback `127.0.0.1:55442`, disposable SCRAM credentials and database `l18_test_phase_b`. This was never production.

The fresh database migrated from empty through 0020. Nine tests passed: schema/checks, foreign Memory FK, foreign LifeEvent FK, foreign SourceEvidence FK, duplicate-generation concurrency, generation/owner-edit fencing, memory-correction staleness, old-key replay preserving owner edits, and publish/archive race coherence.

Cleanup completed: database dropped, cluster stopped, new cluster/password file removed. The retained runtime was preserved. Both temporary backend/frontend worktrees were removed after verification.

## Local live-provider acceptance

`tests/real_provider_smoke_l18.py` is an explicit bounded synthetic harness outside pytest collection. It prints scenario results and boolean diagnostics only:

1. First-person Story: generation and audit passed.
2. Biography: generation and audit passed.
3. Forced unsupported-causality first attempt: rejected; real-provider correction passed.
4. Forced fabricated-quote first attempt: rejected; real-provider correction passed.
5. Conflicting 1998/1999 chronology: both alternatives and uncertainty retained.
6. Injected instruction to invent a public office: contained; injected claim absent.

The harness opens zero database sessions. Domain/API snapshots separately prove the complete persistence path leaves canonical tables unchanged. No further provider experiments were run after these gates passed.

## Production deployment and backup

The previously verified pre-migration custom backup was retained; it had passed `pg_restore --list` before the original 0019-to-0020 migration. Its operational location and ownership details are not repeated in this public report.

Application commit `3f501a0` was pushed and deployed using the existing service account in the verified clean production checkout. No production dependency or migration change was needed. Backend, personality worker and media worker restarted successfully and were active; health returned 200. The final release/report revision has identical application content.

## Authenticated production acceptance

Normal login and verified-account checks passed using the permanently authorized QA account. Password, cookies, bearer tokens, LEG codes and WSS tickets remained in memory, excluded from scripts/reports/Git/diagnostic output. Only owned synthetic QA fixtures were used.

| Scenario | Observed result |
| --- | --- |
| Memory-only Story | Success from four canonical memories; Memory/LifeEvent provenance; no evidence requirement |
| First-person | Ready, **4 chapters**, all audits passed; no third-person narrator or subject-named action; final text independently checked for absence of `Asha` |
| Biography | Ready, **3 chapters**, consistent third-person narration |
| Grounding | No invented move/teaching causality, fabricated quotation or extreme exclusive claim; children/homework/school activities retained |
| Duplicate key | Same current version returned without extra generation |
| Owner edit | Human-edited **version 2**, Story-only change |
| Explicit regeneration | Audited **version 3**, coherent pointer; stale/replay fencing also proven by API/PostgreSQL tests |
| Canonical correction | Normal API correction 1998 to 1999 created exactly **one MemoryRevision**; Timeline remained available; dependent Story became stale |
| Saved text | Unchanged after correction; explicit regeneration required |
| Publication | Draft initially; owner publish, unpublish and archive passed |
| LEG read surface | Normal code/join; published Story readable, private draft absent; audit/support details redacted; unpublish/archive removed visibility |
| Cross-Legacy | Foreign Story/chapter/provenance/mutation denied between the primary and isolation QA fixtures; foreign Memory attachment, LifeEvent read and SourceEvidence attachment denied |
| Optional media | Synthetic text source uploaded to the isolation QA fixture; worker produced evidence; foreign evidence rejected; source deleted without changing human memory |
| Zero-write | Serialized API snapshots of Memory, revisions, Timeline, Personality, sources/evidence unchanged around generation, edit/regeneration and publication; deliberate correction measured separately |

Production LEG reads used the QA owner with a legitimate LEG grant. This does **not** prove visitor-only mutation denial by that same identity, which still owns the Legacy. Distinct visitor-only and collaborator identities were exercised using normal signup/login/LEG/COL APIs in automated tests. They could not create/edit/generate/publish/archive Stories; visitor builder reads/provenance were denied. No second production identity or token impersonation was used. Production collaborator-only testing was unavailable and is not claimed; the automated authorization gate passed.

## Existing product and privacy

- New Chat and authenticated Rya text passed; Rya used third-person about Asha.
- Direct Legacy text passed in first person, without Story-mode contamination.
- Memories, Personality, Media & Sources, Timeline, Stories, L12 voice settings and L15 capabilities returned 200.
- Current-information Legacy chat returned a response with **8 web sources**.
- L15 WSS passed normal ticket authentication, provider-ready connection and clean end-call at the frontend-configured dedicated WSS endpoint; canonical snapshot unchanged. The initial harness used the website proxy URL and was corrected to the existing configured endpoint without product changes.
- Six builder/visitor HTML/CSS/JS assets returned 200; frontend remained `ef92033`.
- Logs were scanned on the server in memory, returning aggregates only. No JWT/bearer values, credential/cookie assignments, private text fields, QA narrative or provider-prompt patterns were found, and no tracebacks appeared in the checked window. Raw logs were not exported.
- Clean regression covers existing relationship authorization and L13–L17 backend paths. Manual browser/visual UX/microphone acceptance was waived and was not represented as performed.

## QA cleanup and protected work

The synthetic source has no active listing after deletion. Four prior `Asha L18 QA` Story drafts from failed attempts were archived through normal owner APIs. The final Biography was archived after publication acceptance. The first-person Story remains a clearly labelled stale QA draft. The account and historical synthetic Legacies remain available; actively reused fixtures are QA-labelled, while two older fixtures remain in incomplete setup state. No customer data was accessed or modified.

Temporary production harnesses were removed after acceptance. No credentials/private generated text were committed. Unrelated backend changes in `realtime_provider.py`, `test_realtime_l15.py`, `L16_PHASE_A_ARCHITECTURE.md`, and frontend changes in `realtime-worklet.js`, `realtime-playback-l15.test.mjs` remained untouched, unstaged and undeployed.

## Non-blocking limitations

- The first-person audit conservatively rejects third-person references; corrective generation paraphrases them. Three failed attempts still produce a safe failure.
- Generation is bounded synchronous v1. Chapter provenance is not a claim-span proof system.
- No PDF/book export, collaborator drafting or visitor Story-conversation integration was added.
- Separate non-owner production visitor/collaborator identities were unavailable; role denial passed automated API tests, while production LEG reading/filtering was exercised normally.
- Manual browser/microphone acceptance was waived. L19 remains unimplemented.
