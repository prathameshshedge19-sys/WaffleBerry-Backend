"""Deterministic structured, topic, intent, and lexical memory ranking."""

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from app.schemas.memory import ApprovedMemoryRetrievalItem, RankedApprovedMemoryItem
from app.services.memory.multilingual_retrieval import retrieval_tokens


_STOP_WORDS = frozenset({
    "a", "about", "an", "and", "are", "for", "in", "is",
    "me", "of", "our", "s", "tell", "the", "to", "what", "were", "you",
    "your",
})

TOPIC_GROUPS = {
    "family": frozenset({
        "family", "parent", "mother", "father", "spouse", "husband", "wife",
        "partner", "child", "son", "daughter", "sibling", "brother", "sister",
        "grandparent", "relative", "home", "pet", "dog", "cat",
    }),
    "childhood": frozenset({
        "childhood", "growing", "early", "hometown", "home", "school", "game",
        "friend", "parent", "sibling",
    }),
    "education": frozenset({
        "school", "education", "study", "subject", "teacher", "mark", "grade",
        "college", "university", "poem", "learning",
    }),
    "marriage": frozenset({
        "marriage", "married", "wedding", "spouse", "husband", "wife", "partner",
        "meeting", "engagement", "place", "year",
    }),
    "work": frozenset({
        "work", "profession", "occupation", "career", "job", "employment",
        "teacher", "tutor", "farmer", "business",
    }),
    "parents": frozenset({"parent", "mother", "father", "mamma", "papa", "mom", "dad"}),
    "pets": frozenset({"pet", "dog", "cat", "animal"}),
}

_TOPIC_TRIGGERS = {
    "family": frozenset({"family"}),
    "childhood": frozenset({"childhood", "growing", "early"}),
    "education": frozenset({"school", "education", "study", "college", "university"}),
    "marriage": frozenset({"marriage", "married", "wedding"}),
    "work": frozenset({"work", "profession", "occupation", "career", "job", "employment"}),
    "parents": frozenset({"parent", "mother", "father", "mamma", "papa", "mom", "dad"}),
    "pets": frozenset({"pet", "dog", "cat", "animal"}),
}

_BROAD_MEMORY_CORE = {
    **TOPIC_GROUPS,
    # A generic year or place is not enough to make a memory about marriage.
    "marriage": TOPIC_GROUPS["marriage"] - {"year", "place"},
}

_SPECIFIC_MARKERS = frozenset({
    "name", "named", "when", "where", "which", "who", "was", "year", "place",
    "profession", "job", "breed",
})

_BUCKET_TERMS = {
    "parents": frozenset({"parent", "mother", "father", "mamma", "papa", "mom", "dad"}),
    "siblings": frozenset({"sibling", "brother", "sister"}),
    "spouse_partner": frozenset({"spouse", "husband", "wife", "partner", "marriage", "married", "wedding"}),
    "children": frozenset({"child", "son", "daughter"}),
    "pets": frozenset({"pet", "dog", "cat", "animal"}),
    "home_activities": frozenset({"home", "family", "game", "activity", "relative", "grandparent"}),
    "school": frozenset({"school", "education", "college", "university", "teacher"}),
    "marks_subjects": frozenset({"mark", "grade", "subject", "poem", "learning", "study"}),
    "work": frozenset({"work", "profession", "occupation", "career", "job", "employment", "teacher", "tutor", "farmer", "business"}),
    "meeting": frozenset({"meeting", "met", "engagement"}),
    "wedding_time_place": frozenset({"wedding", "married", "year", "place"}),
}

_INTENT_TRIGGERS = {
    "occupation": frozenset({"profession", "occupation", "career", "job", "work", "employment"}),
    "birthplace": frozenset({"born", "birthplace", "birth"}),
    "education": frozenset({"school", "education", "study", "studied", "teach", "taught", "grade"}),
}
_INTENT_EXPANSIONS = {
    **_INTENT_TRIGGERS,
    "occupation": _INTENT_TRIGGERS["occupation"] | frozenset({"teacher", "tutor", "tuition"}),
    "parents": TOPIC_GROUPS["parents"],
}


@dataclass(frozen=True)
class QueryClassification:
    intent: str | None
    broad: bool


def _singularize(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("ses"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(value: str | None) -> list[str]:
    return [
        _singularize(token)
        for token in retrieval_tokens(value)
        if token not in _STOP_WORDS
    ]


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _semantic_values(memory: ApprovedMemoryRetrievalItem) -> dict[str, str]:
    if memory.details is None:
        return {}
    return {key: value for key, value in memory.details.semantic_attributes.model_dump().items() if isinstance(value, str) and value.strip()}


def _structured_text(memory: ApprovedMemoryRetrievalItem) -> str:
    details = memory.details
    values = [
        *memory.participant_names, *memory.participant_relationships,
        *memory.tags, *memory.source_topics,
    ]
    if details:
        values.extend(
            value for value in _semantic_values(memory).values()
        )
        for place in details.places:
            values.extend(filter(None, (place.name, place.region, place.country)))
        for temporal in details.temporal_references:
            values.extend(filter(None, (temporal.text, temporal.start_date, temporal.end_date)))
    return " ".join(values)


def _memory_intents(values: dict[str, str]) -> set[str]:
    intents = set()
    if values.get("profession"):
        intents.add("occupation")
    if values.get("birthplace"):
        intents.add("birthplace")
    if values.get("taught_relationship") or values.get("education_level"):
        intents.add("education")
    return intents


class MemoryRelevanceRanker:
    """Rank normalized memories without AI, network calls, or side effects."""

    @staticmethod
    def classify_query(query: str) -> QueryClassification:
        tokens = set(_tokens(query))
        normalized = unicodedata.normalize("NFKC", query).casefold()
        topic = next((name for name, triggers in _TOPIC_TRIGGERS.items() if tokens & triggers), None)
        broad_language = any(phrase in normalized for phrase in (
            "tell me about", "what do you remember about", "what do you remember of",
            "remember about", "remember of",
        ))
        broad = bool(topic and broad_language and not (tokens & _SPECIFIC_MARKERS))
        intent = topic if broad else None
        if not broad:
            if tokens & _INTENT_TRIGGERS["occupation"] or "what did you do" in normalized:
                intent = "occupation"
            elif tokens & _INTENT_TRIGGERS["birthplace"] or "place of birth" in normalized:
                intent = "birthplace"
            elif tokens & _INTENT_TRIGGERS["education"]:
                intent = "education"
            elif tokens & _TOPIC_TRIGGERS["parents"]:
                intent = "parents"
        return QueryClassification(intent=intent, broad=broad)

    @classmethod
    def classify_query_intent(cls, query: str) -> str | None:
        return cls.classify_query(query).intent

    def rank(
        self,
        memories: list[ApprovedMemoryRetrievalItem],
        query: str,
        *,
        semantic_scores: dict[int, float] | None = None,
    ) -> list[RankedApprovedMemoryItem]:
        query_tokens = list(dict.fromkeys(_tokens(query)))
        if not query_tokens:
            return []
        query_set = set(query_tokens)
        classification = self.classify_query(query)
        query_phrase = " ".join(query_tokens)
        ranked = []
        expansion = TOPIC_GROUPS.get(classification.intent, frozenset()) if classification.broad else _INTENT_EXPANSIONS.get(classification.intent, frozenset())

        for memory in memories:
            title_tokens = _tokens(memory.title)
            summary_tokens = _tokens(memory.summary)
            category_tokens = _tokens(memory.category)
            structured_tokens = _tokens(_structured_text(memory))
            title_set, summary_set = set(title_tokens), set(summary_tokens)
            category_set, structured_set = set(category_tokens), set(structured_tokens)
            searchable = title_set | summary_set | category_set | structured_set
            semantic_values = _semantic_values(memory)
            intent_attribute_match = classification.intent in _memory_intents(semantic_values)
            topic_matches = expansion & searchable
            if (
                classification.broad
                and memory.memory_id not in (semantic_scores or {})
                and not (_BROAD_MEMORY_CORE[classification.intent] & searchable)
            ):
                continue
            phrase_bonus = 0.35 if query_phrase in " ".join(title_tokens) else (0.25 if query_phrase in " ".join(summary_tokens) else 0.0)
            lexical_score = min(1.0, phrase_bonus + (0.55 if intent_attribute_match else 0.0) + (0.20 if expansion & (title_set | summary_set | category_set) else 0.0) + (0.40 * len(query_set & title_set) / len(query_set)) + (0.20 * len(query_set & summary_set) / len(query_set)) + (0.05 * len(query_set & category_set) / len(query_set)) + (0.10 * len(query_set & structured_set) / len(query_set)) + (0.18 * min(2, len(topic_matches)) if classification.broad else 0.0))
            semantic_score = (semantic_scores or {}).get(memory.memory_id)
            score = (
                min(1.0, 0.65 * semantic_score + 0.35 * lexical_score)
                if semantic_score is not None
                else lexical_score
            )
            if score <= 0:
                continue
            bucket_source = searchable | topic_matches
            buckets = [name for name, terms in _BUCKET_TERMS.items() if bucket_source & terms]
            matched = [term for term in query_tokens if term in searchable or (intent_attribute_match and classification.intent and term in _INTENT_TRIGGERS.get(classification.intent, frozenset()))]
            ranked.append(RankedApprovedMemoryItem(
                **memory.model_dump(),
                participant_names=memory.participant_names,
                participant_relationships=memory.participant_relationships,
                tags=memory.tags,
                source_topics=memory.source_topics,
                uncertainty_note=memory.uncertainty_note,
                contradiction_group_id=memory.contradiction_group_id,
                embedding=memory.embedding,
                embedding_model=memory.embedding_model,
                embedding_version=memory.embedding_version,
                embedding_dimensions=memory.embedding_dimensions,
                embedded_at=memory.embedded_at,
                relevance_score=round(score, 6), matched_terms=matched,
                semantic_score=(
                    round(semantic_score, 6)
                    if semantic_score is not None else None
                ),
                topic_buckets=buckets,
            ))

        ranked.sort(key=lambda item: (-item.relevance_score, -(item.importance or 0), -_timestamp(item.updated_at), item.memory_id))
        if classification.broad:
            ranked = self._diversify(ranked)
        return ranked

    @staticmethod
    def _diversify(ranked: list[RankedApprovedMemoryItem]) -> list[RankedApprovedMemoryItem]:
        """Move first representatives of distinct buckets ahead of repeats."""
        diverse, deferred, covered = [], [], set()
        for memory in ranked:
            new_buckets = set(memory.topic_buckets) - covered
            if new_buckets:
                diverse.append(memory)
                covered.update(new_buckets)
            else:
                deferred.append(memory)
        return diverse + deferred
