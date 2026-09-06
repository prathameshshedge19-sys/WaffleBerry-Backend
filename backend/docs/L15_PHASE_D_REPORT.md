# L15 Phase D acceptance report

Automated acceptance is green. The subsequent Phase E instruction explicitly
defers manual browser/barge-in acceptance to Phase F after deployment. The earlier
human-record reconciliation note below is historical and does not block Phase E.

| Item | Result |
| --- | --- |
| A. Files changed | See the Phase D file list below. B+C changes remain local too. |
| B. Generation binding | Session, lease connection generation, L14 turn/claim UUID, and matching provider response ID; one request per new admitted turn. |
| C. Playback | Ordered mono PCM Web Audio scheduling with bounded sources and output-latency-aware drain acknowledgement. |
| D. Transcript | Provider deltas accumulate only in bounded memory; only its final transcript can become one assistant Message. |
| E. Persistence | Provider success + final transcript + matching full drain + fresh session/claim/auth; L14 CAS and assistant insertion commit atomically. |
| F. Acknowledgement | Full binding, exact sequence/sample totals, and a fresh terminal seal; stale/forged/premature receipts tested. |
| G. Barge-in | Browser clears output before cancellation transport; server retires the generation, cancels provider output, and fences late events. |
| H. Interrupted text | No assistant Message; user message remains. No estimated spoken prefix. |
| I. Stop speaking | Same interruption; call stays open. |
| J. End Call | Stops playback/capture, interrupts unfinished output, closes resources, preserves user messages. |
| K. Disconnect | Conservative interruption and lease fencing; controlled reconnect does not replay old generation. |
| L. Backpressure | 20-second/960 KB PCM playback bound, 512 frames, 16-slot mailbox, bounded waits and deadlines. |
| M. Stale events | Old audio, transcript, tools, starts, acknowledgements, callbacks, and connection generations cannot affect the next turn. |
| N. Telemetry/usage | L14 content-free timing/counters; terminal usage once, including supplied cancellation usage and actual generation correlation. No billing. |
| O. Real normal response | Passed with both voices; one assistant Message per full simulated playback receipt. |
| P. Real interruption | Passed controlled cancellation plus a second real transcribed utterance in the same call; ordered new turn. Physical barge-in has the separate human gate. |
| Q. Post-generation interruption | Passed; no assistant Message despite successful provider generation. |
| R. Voices | Marin and Cedar passed, no substitution. |
| S. PostgreSQL | Four groups, 60 races; extra final acknowledgement/interrupt run, 15 races. One terminal outcome, maximum one assistant. Existing L14 stress: 75 races passed. Temporary cluster/tunnel removed. |
| T. Backend tests | 710-test broad regression run; final affected suite 110 passed; PostgreSQL runs cover the separately executed cases. 724 current cases covered across these runs. |
| U. Frontend tests | 113 passed. |
| V. Syntax/migrations | 169 Python files compiled in memory, 59 JS/MJS files syntax-checked; current/head both 0016_realtime_sessions. No Phase D migration. |
| W. L1-L14 | Broad regression gates passed, including text/New Chat/onboarding/daily questions/access/L12/L13/L14. Phase B/C tests also passed after the D protocol additions. |
| X. Human browser | User selected “Passed all of these checks.” Local database/API verification currently shows only the old Phase C conversation/turn 1; no D assistant row. Conversation ID/URL clarification is pending. |
| Y. Blockers | Match the human test to its durable conversation and verify completed/interrupted/next-turn records. Do not declare Phase D complete until resolved. |
| Z. Working trees | Both remain intentionally dirty with B+C+D. Main/HEAD/origin-main unchanged; no commit, push, tag, deployment, or production migration. |

Backend HEAD/main/origin-main: `5905e75ca0d9baf9ae52063dce29eb2f9d1a8a94`.
Frontend HEAD/main/origin-main: `f075f6a569db20bbfe5173d175fe0fa6f149029b`.
Normal `git diff --check` passes in both repositories.

## Phase D files

Backend application:

- `app/api/routes/realtime.py`
- `app/schemas/realtime.py`
- `app/services/realtime_responses.py` (new)
- `app/services/realtime_provider.py`
- `app/services/turn_observability.py`

Backend tests/acceptance:

- `tests/fake_realtime.py`
- `tests/test_realtime_l15.py`
- `tests/test_realtime_transcripts_l15.py`
- `tests/test_realtime_provider_l15.py`
- `tests/test_realtime_responses_l15.py` (new)
- `tests/test_realtime_output_postgresql_l15.py` (new)
- `scripts/l15_output_acceptance.py` (new)
- `docs/L15_PHASE_D_PROTOCOL.md`, this report, and the D acceptance JSON files.

Frontend:

- `js/realtime-client.mjs`, `js/realtime-worklet.js`, `js/realtime-dev.mjs`
- `js/realtime-playback.mjs`, `js/audio-ownership.js` (new)
- `js/voice-chat.js` (one ownership registration line)
- `chat.html`, `legacy-chat.html` (guard inclusion/cache version)
- `realtime-dev.html`
- `tests/realtime-playback-l15.test.mjs` (new)

## Evidence and local test surface

Reports and logs are under workspace `backups/l15-phase-d/`:
`backend-regression.log`, `backend-targeted-final.log`, `frontend-regression.log`,
`l14-postgresql.log`, `output-postgresql.log`, `ack-postgresql-final.log`,
`provider-final.log`, `repository-checks.json`, `postgresql-cleanup.txt`, and
`local-endpoint-check.json`. Provider fixtures use newly created disposable SQLite
files; they never use the application or production database.

The developer page is `http://127.0.0.1:5500/realtime-dev.html`; its served bytes
match this workspace. Shared auth points to `http://127.0.0.1:8100/api/v1`.
Local login, `/auth/me`, and the normal conversation list all returned 200.
The isolated manual database remains `backups/l15-phase-c/manual-microphone.sqlite3`.
The test server stays running for human acceptance; restart requires signing in again.

Observed and fixed during real-provider testing: short provider and microphone
bursts could fill an immediate-fail mailbox. Valid frames now apply bounded
backpressure, and authorization heartbeat work is not repeated for every audio
frame. The final six-case provider run and a deterministic burst regression pass.
Output and input limits remain enforced; no unbounded queue was introduced.

Human acceptance must include hearing a completed response, stopping another by
speaking immediately, continuing in a new ordered turn, ending the call, and
refreshing normal saved messages to confirm one assistant per completed turn and
none for the interrupted response. The automated provider probe cannot substitute
for that physical test or for matching the saved records.
