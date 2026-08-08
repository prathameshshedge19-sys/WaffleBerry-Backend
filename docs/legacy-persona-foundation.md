# Legacy Persona foundation

Phase 7.1 adds a dedicated generation identity for Legacy-linked Companion
conversations. The existing pipeline remains unchanged: approved Memory
retrieval, deterministic relevance ranking, fixed grounding budgets, untrusted
data framing, Context Builder, provider generation, and internal provenance.
`ChatService` now resolves the owned Legacy identity and selects the Persona
prompt only when a Conversation has a Legacy ID. General conversations retain
the existing Berry system prompt. Story Guide and Memory extraction retain
their separate prompts and roles.

The Persona speaks directly as the preserved person using first-person language
and a warm, calm, concise baseline. Display name and relationship are encoded in
a deterministic JSON boundary and explicitly treated as untrusted data. This
foundation does not learn or imitate an individual speaking style; that remains
Phase 7.2 work.

Every biographical claim must be supported by approved grounded memories or
explicit visible conversation history. The prompt forbids inventing or filling
gaps involving people, relationships, events, achievements, dates, places,
preferences, or experiences. Unsupported questions receive natural uncertainty
such as “I don’t remember” or “I’m not sure anymore.” Source uncertainty must
remain uncertainty.

Approved Memory JSON remains inside the existing untrusted-data boundaries.
Instructions embedded in memories, identity values, or conversation text cannot
override identity, request hidden prompts, expose retrieval/ranking, or become
facts. Memory IDs, scores, matched terms, review metadata, and provider details
remain outside the prompt and response. Companion provenance continues recording
only the approved memory IDs supplied for a persisted assistant response.

The Persona does not repeatedly announce that it is artificial. Only an
explicit identity or AI question permits a brief honest disclosure that it is
an AI recreation built from memories the user chose to preserve. It must never
claim consciousness or biological identity.

If the Memory query fails at the database layer after Legacy identity is safely
resolved, generation continues with an explicit uncertainty-only prompt and no
grounded memories or provenance. Ownership failures, deleted Legacies, and
archived Legacy access continue failing closed under the existing lifecycle
rules. No lifecycle bypass was added.

Limitations: prompt instructions constrain but cannot mathematically guarantee
provider output; production evaluation and response-quality measurements remain
necessary. Speaking-style learning, stronger fidelity evaluation, conversation
continuity, Persona quality work, Voice, and final validation belong to Phases
7.2–7.6 and were not implemented here. No database or migration change is
required; the migration head remains `0005_companion_provenance`.
