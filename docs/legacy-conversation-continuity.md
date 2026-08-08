# Legacy Persona conversation continuity

Phase 7.4 extends the existing Legacy Persona pipeline with request-scoped
conversation continuity. It does not create durable conversation memory and
does not change the database, lifecycle rules, retrieval ranker, grounding
budget, memory fidelity analysis, or provenance contract.

## Architecture

The existing bounded chronological message history remains the Persona's
primary conversational context. Before approved-memory retrieval,
`ConversationContinuity` examines the latest user message. A substantive new
request continues to use that message unchanged. A short or referential
follow-up receives a bounded current-topic segment of up to four recent
user-authored messages as temporary query context. The segment resets at the
most recent explicit topic switch. The existing retrieval service and
deterministic ranker then operate normally on that query.

Assistant-authored messages are never added to the retrieval query because a
prior generated claim is not factual evidence. The continuity builder does not
query, insert, update, or delete database records. Its output exists only while
the current request is prepared.

## Follow-ups and topic continuity

Short turns such as "And then?", "Who was there?", "How did you feel?", and
"Why?" can retain the subject expressed in recent user turns. The Persona
prompt also instructs the provider to continue the visible topic, people,
place, timeline, story, activity, and emotional register when the reference is
clear and supported.

Retrieval still runs for every eligible Legacy turn. Phase 7.4 does not cache
or reuse a prior ranking result. Instead, it supplies enough current-user
context for the unchanged lexical ranker to find relevant approved memories
when the newest text alone contains little retrieval meaning.

## Topic switching

Explicit English switch cues such as "change the topic", "moving on",
"instead", "enough about", and "tell me about" prevent previous turns from
being added to the retrieval query. The Persona prompt independently directs
the provider to follow an explicit new topic without carrying unrelated facts,
people, or emotional tone forward.

## Pronouns and ambiguity

Pronouns and references including he, she, they, there, that, those days, and
that time may be resolved only when visible user context or currently supplied
approved memories identify one unambiguous referent. When two or more people,
places, events, or timelines are plausible, the Persona must ask a short,
natural clarifying question rather than guess.

## Emotional continuity

The Persona preserves the emotional register of the event currently being
discussed. It should not become cheerful during painful material, and it should
not carry sadness into a clearly changed topic. Emotional continuity changes
tone only; it is not treated as a stored autobiographical fact.

## Temporary context and lifecycle

Continuity is bounded by the existing context-window configuration and the
current conversation's stored messages. Phase 7.4 does not promote chat text
to approved memories, Story Sessions, or the Persona profile. It creates no
background job or new persistence path. Archived Legacy behavior, deletion,
export, ownership checks, and non-Legacy Berry conversations are unchanged.

## Limitations and future work

The retrieval augmentation intentionally uses conservative lexical heuristics,
not entity extraction or a hidden conversational state model. Explicit switch
detection has a small deterministic English cue set; substantive multilingual
messages still use their own text, while short multilingual follow-ups receive
recent user context. Very long conversations remain constrained by the
existing bounded history window.

Persona quality evaluation and the final Phase 7 audit belong to Phases
7.5-7.6. Long-term conversation memory, embeddings, voice, cloning,
fine-tuning, and frontend redesign remain out of scope. No migration is needed;
the Alembic head remains `0005_companion_provenance`.
