# Legacy Persona memory fidelity

Phase 7.3 adds a post-selection interpretation layer without changing approved
Memory retrieval, lexical ranking, grounding budgets, provenance, lifecycle, or
database state. After the existing grounding selector chooses its bounded
memories, `MemoryFidelityService` inspects only those selected approved Memory
IDs for existing contradiction-group and uncertainty metadata. It never adds a
new fact or an unselected Memory to the prompt.

`MemoryFidelityAnalyzer` deterministically classifies support as High, Medium,
or Low. These labels remain application-internal and are never rendered into
the system prompt or response. Multiple compatible memories with adequate
relevance and extraction confidence receive High support. A focused supported
memory receives Medium support. Missing retrieval, no selected memories,
recorded contradictions, or explicit uncertainty produce Low support. The
classification changes response caution, not which memories are retrieved.

When several compatible selected memories concern the topic, the Persona is
instructed to synthesize only their stated facts into one natural first-person
answer. It must preserve qualifications and may not invent causal links,
sequence, dates, locations, relationships, transitions, or other connective
details. This allows facts such as an approved tea preference, evening routine,
and balcony habit to appear coherently without treating an unstated connection
as fact.

Current visible user conversation provides temporary reference context for
follow-ups such as “what happened after that?” The Persona may resolve the
reference only against user-provided visible context and currently supplied
approved memories. Prior assistant statements are never evidence. Nothing from
the conversation is promoted, persisted, or approved by this phase; durable
conversation continuity remains Phase 7.4.

If any selected Memory has a contradiction group, the guidance forbids choosing
one account or merging incompatible details. The Persona instead expresses
natural first-person uncertainty. Explicit uncertainty notes likewise lower
support and preserve cautious wording. With missing or insufficient evidence,
the Persona must not guess a person, relationship, event, date, location, or
other absent fact and should answer briefly that it does not remember or is not
sure.

Fidelity metadata queries project only Memory ID, contradiction-group ID, and
uncertainty note for the already selected owner-scoped Legacy. A metadata-query
failure falls back to cautious uncertainty; it cannot broaden grounding.
Grounded JSON retains the existing injection-resistant boundary, Unicode and
multilingual content, ranking order, whole-memory budget behavior, and internal
response-to-memory provenance.

Limitations: this layer guides provider synthesis but does not generate a
deterministic prose response itself, and prompt constraints cannot
mathematically guarantee provider behavior. Lexical retrieval may not surface
all semantically related facts for vague follow-ups. Persistent conversation
continuity, broader semantic retrieval, automated Persona quality evaluation,
and final validation belong to Phases 7.4–7.6. No schema or migration change is
required; the head remains `0005_companion_provenance`.
