# L15 Phase E: shared brain integration

Phase E is local and uncommitted. Migration head remains
`0016_realtime_sessions`. The feature flag remains off by default. Manual
browser/microphone/barge-in acceptance is explicitly deferred to Phase F.

## One preparation and finalization policy

Provider-final user speech is admitted through the existing L14 durable turn
claim. `prepare_admitted` invokes `prepare_turn`; Rya uses `BuilderPreparedTurn`,
and Legacy uses `PersonaPreparedTurn`. The Realtime adapter places those exact
shared system messages in response instructions. Rya additionally uses the
existing `RYA_SYSTEM_PROMPT`. Recent accepted messages form explicit input.
Neither generator reads the provider's default conversation as durable history.

Realtime-only speech instructions request concise natural phrasing, interruption
tolerance, and the pronunciation of visible “Rya” as “Riya” (ree-yah). They do
not rename stored messages, Rya, or LegaRya. Rya remains the builder; Legacy
retains the existing AI-transparency, grounding, language, and relationship
contract. `select_personality_style` supplies compact cues, never raw profiles.

Two shared corrections were required by acceptance:

* Conversational DELETE is enforced as owner-only in `LivingMemoryService.store`.
  The actor must be the current owner and the builder conversation's actor.
  Collaborator add/enrich/correct operations remain available. The shared
  collaborator prompt also describes this permission.
* The L11 identity parser formerly extracted “Her” from “I'm her son” and
  matched the incidental phrase “her son” as verification. Pronouns are now
  excluded from names and relationship evidence. Previously verified metadata
  without current supporting evidence is downgraded in the read-only context.

The shared Legacy prompt also clarifies its existing no-invention rule:
liking a flower does not establish a reason, sensory association, or emotional
effect. Historical L13 prompt digests remain pinned after removing only the
two exact, separately asserted prompt additions.

## Read tools before speech

One silent text planning response selects at most four read calls. One audible
response then consumes their bound results. Planning text is discarded; it is
never an assistant message or spoken preamble. Both responses use
`gpt-realtime-2.1`. Planning uses medium reasoning and speech uses high reasoning;
the validated connection configuration remains low reasoning. This favors policy
following at the cost of an extra bounded planning request and read latency.

The existing `conversation_tools.REGISTRY`, argument schemas, result sanitizers,
capability matrix, and authorization code are reused without write tools.

| Mode | Exposed tools | Required before speech |
| --- | --- | --- |
| Rya owner/collaborator | `retrieve_legacy_memories` | No additional read required; shared preparation already analyzes/retrieves. |
| Legacy viewer | All four L14 read tools | Relationship and selected style; memory when the shared route requires it; current information when the shared route requires it. |

The other three names are `get_legacy_personality`,
`get_visitor_relationship_context`, and `get_current_information`.
The model cannot supply a Legacy ID, actor, role, mode, or write permission.
Current queries still pass the registry's minimization/private-data checks.
Builder mode receives no web capability. Unavailable/denied current data produces
the existing shared limitation context.

Each call/result is bound to the session ID, lease owner, connection generation,
L14 turn ID, claim UUID, request digest, and provider call ID. Fresh live and
durable authorization checks run before dispatch and after each awaited tool.
All results publish together after a final check. Interrupted results and errors
are discarded by the owning output object; they cannot affect the next turn.

## Bounded output and persistence

Limits include the existing recent-message window, compact selected style,
four calls, 4 KiB arguments and 8 KiB results per call, a 30-second preparation
or tool phase, and a 150-second logical response deadline. The existing transport
mailbox and I/O deadlines apply throughout.

Realtime may begin item N+1's transcript before item N's audio finishes. The
output gate therefore binds up to eight items to their provider output indices,
accepts PCM only in forward contiguous order, and retains each final transcript.
It requires every spoken item to finish before producing one combined transcript
and one full-playback seal. Aggregate limits remain 8,000 transcript characters,
120 seconds of output, 20 seconds of outstanding PCM, and 512 outstanding frames.
Foreign item IDs, reversed audio, incomplete parts, and forged receipts fail safely.

Only complete provider output plus the matching full-playback receipt can commit
one assistant message through L14's terminal CAS. Sanitized `MessageWebSource`
rows attach in that same transaction, only to the completed Legacy response.
Interrupted sources are discarded.

Rya completion then calls the existing `complete_turn` / `apply_effects` path.
Canonical memory and its receipt commit together; activity and its receipt commit
together. Repeated finalization consumes those receipts. Fresh authorization is
checked before each effect transaction and before its commit, including after
embedding. Authorization failure rolls back the pending effect. Assistant,
memory, and activity retain L14's existing three-transaction boundary; missing
receipts expose incomplete post-processing rather than silently replaying it.

Interrupted/failed answers retain accepted user messages and create no assistant
or canonical/activity effects. A completed visitor turn creates zero memory,
revision, personality, activity, progression, or effect-receipt rows. Existing
private messages, visitor identity metadata, and completed web sources remain
the allowed visitor writes.

## Acceptance evidence

`L15_PHASE_E_REPORT.md` and `L15_PHASE_E_TEST_ACCEPTANCE.json` summarize the final
checks. Disposable databases, generated WAVs, detailed provider transcripts,
and test logs are under workspace `backups/l15-phase-e/`.

The brain matrix uses real Realtime generation, memory analysis/embeddings, and
web search at the accepted-transcript boundary. The separate transport matrix
uses synthesized microphone PCM, real ASR and shared analysis, the application
WebSocket, and simulated playback acknowledgments. Neither claims physical
browser playback. The user separately confirmed the generated brand audio sounds
like ree-yah. Native multilingual quality remains an explicit Phase F acceptance
item; no equal-quality claim is made.
