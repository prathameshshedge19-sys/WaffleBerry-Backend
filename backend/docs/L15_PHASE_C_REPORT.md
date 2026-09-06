# L15 Phase C acceptance report

Implementation is local and uncommitted. Human microphone acceptance is pending;
the real-provider results below use explicitly disposable synthesized speech.
The Phase C completion phrase is withheld until the human microphone gate passes.

| Item | Result |
|---|---|
| A. Local migration | Normal `alembic upgrade head`: `0015_conversation_turns` → `0016_realtime_sessions`. Current and source head agree. All 20 historical tables have identical row counts and content hashes; SQLite integrity and foreign-key checks pass. Backup: `C:/Users/Saee/Desktop/Waffleberry-new/backups/l15-phase-c/local-before-0016-20260906T125445Z.sqlite3`. |
| B. Files changed | Phase C file inventory below. Approved Phase B changes are retained; none were reset or committed. |
| C. Browser capture | Secure mono microphone; echo cancellation/noise suppression; AudioWorklet; band-limited resampling to 24 kHz PCM16 LE; 50 ms frames; bounded worklet and WebSocket buffering. |
| D. Client module | Isolated `realtime-client.mjs`, PCM module, worklet and local-only developer surface. Authentication, capture, provider transcript display, receipt reconciliation and cleanup are implemented. No polished overlay. |
| E. Provisional/final | Provisional text is display-only. Only server-observed provider finals can cross admission. Text is bounded/normalized, empty or invalid text rejected. Browser-forged final events fail. |
| F. Ordering | Provider committed predecessor links determine order. Tests cover B-final-before-A-final, out-of-order commit observation, missing predecessors and duplicates. The transport admits A first; B receives explicit busy/repeat guidance while A is active. |
| G. L14 integration | Reuses client key, L14 request digest, pending/CAS claim and ordinary user link. Internal atomic admission option joins those operations to session binding. No new turn table. `prepare_admitted` calls the existing L14 preparation path; the live socket retains the claim for the future Phase D consumer. |
| H. Existing chat | Fixed authorized conversation only; no creation factory or rebinding. Normal history API returns the saved user transcript. |
| I. Unsaved New Chat | Silence/provisional/failure creates no conversation. First accepted final atomically creates the conversation, binds the session, creates/claims the turn, and saves its user Message. Injected message-insert failure rolls back all four. |
| J. Exactly once | PostgreSQL: 25 repeated first-utterance races each produced one conversation, one binding, one turn, one user Message. |
| K. Duplicate events | Same key/text returns the durable receipt; changed text conflicts. Duplicate provider events do not create rows. Old connection generations are rejected before admission or replay. |
| L. Visitor safety | Viewer-private conversation/user Message and turn only. Full canonical snapshots remain unchanged, including memory revisions, personality projection and builder activity. No canonical effects. |
| M. Collaborator | Existing builder policy and actor/source provenance preserved. No write tool exposure or collaborator DELETE policy change. |
| N. Preparation parity | Full ordered L14 prompts/grounding match text preparation for owner, collaborator and viewer. Read-only handoff rolls back preparation changes before returning; no completion/effect path runs. |
| O. ASR failure | No fake/empty Message or turn; client receives repeat guidance; previously accepted user messages remain accessible. |
| P. Shutdown | At most one second to reconcile already-in-flight provider finals. Otherwise provisional speech is discarded with a repeat notice. Unanswered user-only L14 claims terminate without an assistant Message or effects. No Phase D assistant interruption persistence. |
| Q. Reconnect | Reads durable receipts after ownership/generation validation. No turn recreation, old-token reclaim, microphone reacquisition or automatic audio replay. Lease recovery also releases unanswered claims so text can continue. |
| R. Audio ownership | Tiny shared microphone acquisition wrapper uses same-origin Web Locks across tabs and features. Pending permission requests are exclusive; late permission after navigation is stopped. `voice-chat.js` is byte-for-byte unchanged. |
| S. Real-provider languages | All four pipeline checks passed against GPT-Realtime-2.1 + gpt-live-transcribe. English: 6 provisional events; Marathi: 16; Hindi-English: 14; German: 9. Each had matching committed/final identity, one turn/message, zero effects, L14 preparation and successful normal-chat refresh. Hindi-English changed tense and rendered English words in Devanagari; no equal-quality claim. |
| T. PostgreSQL concurrency | New Phase C acceptance passed 75 races across 25 iterations: matching first final, same-key/different-text conflict, stale-generation rejection. Separate disposable PostgreSQL cluster/port; no production database access. Unchanged L14 stress also passed 25 iterations / 75 races. Temporary cluster and tunnel removed after verification. |
| U. Backend tests | 692 distinct backend cases passed across the full regression, final targeted and dedicated PostgreSQL runs; zero uncovered/skipped cases. Results are recorded in `L15_PHASE_C_TEST_ACCEPTANCE.json`. Two pre-existing dependency deprecation warnings; no ignored application failures. |
| V. Frontend tests | 95 passed, zero failed/skipped, including the existing L12/New Chat/onboarding-related contracts and new capture/ownership/client tests. |
| W. Compile/syntax/migrations | 165 Python sources compile in memory; 54 JS/MJS sources pass syntax checking. Local current/head 0016; existing migration tests pass. No 0017 migration. |
| X. New Chat/onboarding | Existing backend/frontend suites pass; Phase C also tests no empty chat and complete rollback of first-message admission. Setup-incomplete sessions remain rejected by Phase B. |
| Y. L12/L13/L14 | Existing suites retained. L12 voice file unchanged; L13 canonical snapshots/parity protected; L14 lifecycle/idempotency/read tools and response/effect boundaries preserved. |
| Z. Blockers | Human live-microphone exit test is pending. Browser runtime reported no available browser connections. A local disposable test page is prepared and the user has been asked to run the exact jasmine sentence/refresh test. Synthesized audio is not represented as human microphone acceptance. |
| AA. Working trees | Intentionally dirty with local Phase B+C work. Backend HEAD/main/origin-main: `5905e75ca0d9baf9ae52063dce29eb2f9d1a8a94`. Frontend HEAD/main/origin-main: `f075f6a569db20bbfe5173d175fe0fa6f149029b`. No commit, push, tag, deployment or production migration. Normal configuration keeps realtime disabled. |

## Phase C file inventory

Backend existing/Phase B files extended:

- `app/api/routes/realtime.py`: bounded audio frames, ordered final admission, failure/shutdown/reconcile events.
- `app/schemas/realtime.py`: strict PCM-frame schema.
- `app/services/realtime_sessions.py`: unanswered L14 claim cleanup when fencing/recovering a connection.
- `app/services/turn_lifecycle.py`: internal atomic admission option and live/text serialization guard; ordinary HTTP claim invocation remains compatible.
- `app/services/conversation_turns.py`: internal `realtime_voice` input-mode type.

Backend additions:

- `app/services/realtime_transcripts.py`.
- `tests/test_realtime_transcripts_l15.py`.
- `tests/test_realtime_admission_postgresql_l15.py`.
- `scripts/l15_transcript_acceptance.py` and `scripts/l15_local_acceptance_server.py`.
- `docs/L15_PHASE_C_PROTOCOL.md`, this report, provider acceptance JSON and test acceptance JSON.

Frontend:

- `js/microphone-ownership.js`.
- `js/realtime-client.mjs`, `js/realtime-pcm.mjs`, `js/realtime-worklet.js`.
- `js/realtime-dev.mjs`, `realtime-dev.html`.
- `tests/realtime-audio-l15.test.mjs`.
- `chat.html` and `legacy-chat.html`: load the ownership guard before existing L12 voice code.

Evidence and test database/audio fixtures are in workspace `backups/l15-phase-c`,
outside both repositories. The protocol document includes reproduction commands
and the disposable local microphone account. The real-provider report contains
only declared disposable fixture text and safe event/count metadata.
