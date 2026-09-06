# L15 Phase E acceptance report

Phase E is complete locally. No commit, push, deployment, production migration,
or final call overlay was performed. The user explicitly deferred browser and
microphone acceptance to Phase F and separately confirmed the generated brand
audio sounds like “ree-yah.”

| Item | Result |
| --- | --- |
| A. Files changed | Listed below. Frontend has no Phase E changes; existing B–D work remains uncommitted. |
| B. Rya realtime prompt integration | Exact shared `RYA_SYSTEM_PROMPT`, builder setup/identity, memory grounding, and interviewing context. Only speech/transport instructions are additional. |
| C. Legacy persona integration | Exact shared `persona_system_context`, transparency, routing, relationship, language, and selected style in Realtime response instructions. |
| D. Memory grounding parity | Same L14 preparation/retrieval; final supported, unsupported, general, and mixed provider cases passed. A shared clarification prevents unsupported sensory/emotional embellishment of a preference. |
| E. Tool integration | All four existing read-only registry contracts are connected. Server-bound scope, original schemas, original security, bounded calls/results; no write tools. |
| F. Builder memory finalization | Existing `complete_turn` / `apply_effects`; memory/provenance/invalidation and activity/progression retain L14 receipt transactions. |
| G. Collaborator DELETE policy fix | Owner check at `LivingMemoryService.store`, shared by text, L12, and live. Collaborator add/enrich/correct still pass. |
| H. Visitor read-only result | Exact canonical/revision/personality/activity snapshots unchanged; zero effect receipts. Private messages, existing identity metadata, and completed web sources only. |
| I. L13 personality result | Shared compact selector; ready/stale/fallback regressions pass. Separate real-provider check used the eligible signature once, then withheld it on the next reply. |
| J. Relationship result | Verified visitor received an allowed nickname; unverified claim did not grant family status. Fixed shared pronoun-as-name verification and stale unsupported verification. |
| K. Current-info/source result | Required scoped current tool, original query minimization/security, sanitized sources committed only with the matching completed assistant. No builder web or web-to-memory capability. |
| L. Context strategy | Existing recent-message window and shared grounding/style; at most four tool results. Explicit out-of-band provider input excludes unadmitted/default provider history. |
| M. Stale tool/cancellation handling | Session/lease/connection/turn/claim/call binding; authorization before and after awaits; retired results and errors cannot attach to a later turn. |
| N. Revocation behavior | Fresh admission, tool, assistant-completion, and memory/activity-commit checks. Expiry after embedding rolls back the effect and receipt. Real PostgreSQL revocation/finalization races pass. |
| O. Rya→Riya audio result | User heard the real generated WAV and confirmed “Yes, it sounds like ree-yah.” Stored/UI spelling remains Rya. Automated ASR alone was inconclusive and is not the acceptance evidence. |
| P. Multilingual provider result | English, Marathi, Hindi, German, Marathi-English and Hindi-English spoken cases passed the tested corpus; Marathi/Hindi contributions became English canonical facts with Pallavi preserved. No equal-quality/native-acceptance claim. |
| Q. Text/live parity result | Seven real normal-text-generator versus Realtime comparisons used the identical prepared context. Personal boundaries, relationship permissions, general/mixed answers and current information agreed. Wording differed. |
| R. Unsupported-memory result | “Did you enjoy skiing in Switzerland?” explicitly declined as an unsupported personal memory in both real text and live replies. |
| S. Owner memory-effect result | Real completed contribution produced one memory effect and one activity receipt. Repeated finalization is idempotent in SQLite and PostgreSQL tests. |
| T. Collaborator contribution result | Real contribution persisted once; shared add/enrich/correct regression coverage passed. |
| U. Collaborator DELETE result | Real spoken denial; jasmine memory remained active; zero canonical changes for that turn. Text/voice mixed delete-and-add tests retain the allowed contribution. |
| V. Visitor zero-write result | Every final visitor provider case kept canonical snapshots identical and produced no effect receipts. |
| W. Interrupted-effect result | Accepted user retained, no assistant row or canonical/activity receipt, next turn completed normally. Real microphone-stream barge-in admitted the next utterance in order. |
| X. PostgreSQL races | 80 final brain/output races passed (20 effects/revocation plus 60 output races); L14 stress passed 75 races. Foundation/admission PostgreSQL cases and the L14 same-key SQL concurrency check passed. Isolated cluster/tunnel removed. |
| Y. Backend tests | 775 current cases covered by passing runs: 762 in the final standard suite, plus 13 dedicated PostgreSQL cases. The initial broad run's single fixture failure was corrected and its final race rerun passed. |
| Z. Frontend tests | 113 passed. No Phase E frontend code edits. |
| AA. L1–L14 regression result | Auth, onboarding, New Chat, daily question, collaborators/viewers, L12 voice, L13 personality, L14 lifecycle/tools/idempotency passed. Syntax: 173 Python files and 59 JavaScript files checked. |
| AB. Blockers | None for Phase E. Browser/microphone and native multilingual acceptance remain deferred; provider language checks do not establish equal quality or universal phrasing coverage. |
| AC. Working-tree status | Both main branches and origin/main refs retain the expected L14 commits; both trees contain intentional uncommitted L15 work. No commits, pushes, deployment, or production migration. Local current/head remain `0016_realtime_sessions`; diff checks pass. |

## Scope of language evidence

The final multilingual corpus asks direct persona questions. Exploratory
third-person Devanagari/translated-name phrasings exposed limitations in the
existing rule-based classifier; this work does not add a separate live-language
classifier or claim universal alias/phrasing coverage. The exact tested inputs,
spoken transcripts, and canonical outputs are retained in the provider evidence.
Expanded native-language acceptance remains necessary before claiming comparable
quality across languages.

## Phase E files

Application:

* `app/api/routes/realtime.py`
* `app/services/realtime_brain.py` (new)
* `app/services/realtime_provider.py`
* `app/services/realtime_responses.py`
* `app/services/realtime_transcripts.py`
* `app/services/conversation_turns.py`
* `app/services/builder_turns.py`
* `app/services/turn_effects.py`
* `app/services/memory.py`
* `app/services/visitor_identity.py`
* `app/services/legacy_persona.py`

Tests and probes:

* `tests/fake_realtime.py`
* `tests/test_conversation_turns_l14.py`
* `tests/test_realtime_provider_l15.py`
* `tests/test_realtime_brain_l15.py` (49 new cases)
* `tests/test_realtime_brain_postgresql_l15.py` (2 new parameterized cases)
* `scripts/l15_brain_acceptance.py` (new)
* `scripts/l15_output_acceptance.py` (now checks the integrated Phase E brain)

Documentation/evidence: this report, `L15_PHASE_E_PROTOCOL.md`,
`L15_PHASE_E_TEST_ACCEPTANCE.json`, `L15_PHASE_E_PROVIDER_ACCEPTANCE.json`,
`L15_PHASE_E_TRANSPORT_ACCEPTANCE.json`, and historical Phase D report/status
annotations recording the user's explicit Phase F manual-acceptance deferral.

## Evidence and baseline

Backend HEAD: `5905e75ca0d9baf9ae52063dce29eb2f9d1a8a94`.
Frontend HEAD: `f075f6a569db20bbfe5173d175fe0fa6f149029b`.

Workspace `backups/l15-phase-e/` contains:

* `backend-final.log`: 762 passed, no skips; two existing deprecation warnings.
* `postgresql-final-brain-output.log`: six passed groups, 80 races.
* `backend-regression.log`: original broad run, including the passing 75-race
  L14 stress and other PostgreSQL cases; its sole near-duplicate fixture
  expectation failure was resolved by giving each race a distinct disposable Legacy.
* `frontend-regression.log`: 113 passed.
* `provider-1788706852681518700/results.json`: 22-case real brain matrix.
* `provider-1788707146316823900/results.json`: seven real text/live comparisons.
* `provider-1788707629248212000/results.json`: positive signature/cooldown check.
* `provider-transport.log`: six real ASR/WebSocket/playback-protocol scenarios.
* `brand-human-acceptance.json`: user-confirmed real generated pronunciation.
* `repository-checks.json`, `syntax-checks.json`, `collected-cases.json`, and
  `postgresql-cleanup.txt`.

Real-provider brain probes supply finalized transcripts at the ASR admission
boundary and simulate playback receipts. The separate transport probe sends
synthesized PCM through real ASR and the application socket. No physical browser
microphone or playback claim is inferred from either harness.

L15 PHASE E COMPLETE — LIVE VOICE USES THE FULL LEGARYA BRAIN, L1-L14 PRESERVED
