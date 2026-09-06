# L15 Phase F release contract

L15 B-E implements authenticated, server-scoped live voice with durable L14
admission, shared reasoning/tools, interruption-safe playback receipts and
shared builder effects. Phase F adds the frontend product overlay and the
authenticated, read-only `GET /api/v1/realtime/capabilities` flag. Reading it
creates no session or conversation. `POST /realtime/sessions` continues to
authorize Legacy setup, role, mode, actor, origin and conversation scope.

All L15 changes are released together. The prior checkpoint is backend
`5905e75ca0d9baf9ae52063dce29eb2f9d1a8a94` and frontend
`f075f6a569db20bbfe5173d175fe0fa6f149029b`. Before release, run the complete
backend/frontend suites, isolated PostgreSQL races, Python/JS syntax, static
asset checks and local migration current/head checks. Inventory all dirty files
before the authorized commits; no production migration precedes these gates.

Production uses `/home/waffleberry/WaffleBerry-Backend/backend`, the existing
venv, backend service and personality worker. Confirm the active database name
is `legarya` and revision is `0015_conversation_turns`. Take and validate a
timestamped custom-format backup before upgrading normally to
`0016_realtime_sessions`. Compare preexisting tables/rows; the only new table
must be `realtime_sessions`. Do not stamp, force migration, or touch unused DBs.

Audit actual TLS/WSS routing and allowed origins, not just the proposed Nginx
fragment. The audited existing proxy already forwards upgrade headers with
HTTP/1.1 and a 120-second read timeout. The product uses a direct WSS connection
to this backend, while authentication retains the frontend HTTP API path.
Enable `REALTIME_ENABLED` only as part of the authorized release. Verify service
health, worker health, HTTP health, startup logs and actual secure connections.

Deploy the frontend through its normal Vercel production workflow; preserve
existing domain redirects and SEO configuration. Smoke-test normal text,
New Chat, visitor, dashboard and L12 flows. Production live-call acceptance
must cover normal completion, human barge-in, Stop speaking, End Call during
speech, silent/new chat, Legacy grounding/transparency and brand pronunciation.
Record physical mobile availability and background/lock behavior explicitly.

If a serious Live Voice blocker appears, disable `REALTIME_ENABLED` and the
entry rather than blindly reversing the database after writes. Preserve
L1-L14 and record the exact rollback decision.

The final release report belongs in the workspace release evidence folder and
records actual SHAs, backup path/size, migration, deployment and acceptance.
Create and push annotated `legarya-l15-live-voice-conversation` tags in both
repos only when production is healthy and the required human barge-in gate
has actually passed. The tag message is:

`LegaRya L15 Live Voice Conversation production checkpoint`

Fresh local microphone fixtures require an explicit `L15_LOCAL_TEST_PASSWORD`
in the launching environment; no reusable password is shipped in that helper.
