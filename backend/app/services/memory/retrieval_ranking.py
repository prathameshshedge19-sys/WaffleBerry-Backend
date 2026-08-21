"""Deterministic structured, topic, intent, and lexical memory ranking."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from app.schemas.memory import ApprovedMemoryRetrievalItem, RankedApprovedMemoryItem
from app.services.memory.multilingual_retrieval import retrieval_tokens


_STOP_WORDS = frozenset({
    "a", "about", "an", "and", "are", "for", "in", "is",
    "had", "has", "have", "i", "me", "of", "our", "s", "shared",
    "something", "tell", "the", "to", "we", "what", "were", "you",
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
    "family": frozenset({"family", "relative", "relationship"}),
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
    "pets": TOPIC_GROUPS["pets"],
}

# Explicit subtypes supported by the current normalized pet taxonomy. Generic
# "pet"/"animal" memories remain eligible when they do not contradict one.
_PET_SUBTYPES = frozenset({"dog", "cat"})

_SUBJECT_ALIASES = {
    "family": frozenset({
        "relative", "relationship", "spouse", "husband", "wife", "partner",
        "sibling", "brother", "sister", "parent", "mother", "father",
        "child", "son", "daughter",
    }),
    "pet": frozenset({"animal", "dog", "cat"}),
    "dog": frozenset({"pet", "animal"}),
    "cat": frozenset({"pet", "animal"}),
    "tv": frozenset({"television"}),
    "television": frozenset({"tv"}),
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


def _raw_tokens(value: str | None) -> list[str]:
    return [token for token in retrieval_tokens(value) if token not in _STOP_WORDS]


def _one_edit_apart(left: str, right: str) -> bool:
    if left == right or min(len(left), len(right)) < 3 or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right, strict=True)) == 1
    short, long = (left, right) if len(left) < len(right) else (right, left)
    index = errors = 0
    for char in long:
        if index < len(short) and char == short[index]:
            index += 1
        else:
            errors += 1
            if errors > 1:
                return False
    return errors == 1


def _speech_token_match(left: str, right: str) -> bool:
    """Allow one conservative ASR error when at least one spoken token is 4+ chars."""
    if max(len(left), len(right)) < 4:
        return False
    edit_match = _one_edit_apart(left, right) or _one_edit_apart(
        _singularize(left), _singularize(right)
    )
    return edit_match and _phonetic_key(left) == _phonetic_key(right)


def _phonetic_key(token: str) -> str:
    groups = {
        **dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"),
        **dict.fromkeys("dt", "3"), "l": "4",
        **dict.fromkeys("mn", "5"), "r": "6",
    }
    value = unicodedata.normalize("NFKD", token).casefold()
    letters = [char for char in value if "a" <= char <= "z"]
    if not letters:
        return ""
    codes = []
    previous = groups.get(letters[0])
    for char in letters[1:]:
        code = groups.get(char)
        if code and code != previous:
            codes.append(code)
        previous = code
    return (letters[0].upper() + "".join(codes) + "000")[:4]


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
        raw_tokens = set(_raw_tokens(query))
        for triggers in _TOPIC_TRIGGERS.values():
            for trigger in triggers:
                if any(_speech_token_match(token, trigger) for token in raw_tokens):
                    tokens.add(_singularize(trigger))
        normalized = unicodedata.normalize("NFKC", query).casefold()
        topic = next((name for name, triggers in _TOPIC_TRIGGERS.items() if tokens & triggers), None)
        broad_language = any(phrase in normalized for phrase in (
            "tell me about", "what do you remember about", "what do you remember of",
            "remember about", "remember of", "what about",
        ))
        personal_subject = bool(re.search(
            r"\b(?:tell me about|what do you remember (?:about|of))\s+"
            r"(?:our|your|my)\s+[\w'-]+",
            normalized,
        ))
        broad = bool(
            broad_language
            and (topic or personal_subject)
            and not (tokens & _SPECIFIC_MARKERS)
            or (
                topic
                and re.search(r"\b(?:my|our|your)\b", normalized)
                and not (tokens & _SPECIFIC_MARKERS)
            )
        )
        intent = topic if broad else None
        if not broad:
            if tokens & _INTENT_TRIGGERS["occupation"] or "what did you do" in normalized:
                intent = "occupation"
            elif tokens & _INTENT_TRIGGERS["birthplace"] or "place of birth" in normalized:
                intent = "birthplace"
            elif tokens & _INTENT_TRIGGERS["education"]:
                intent = "education"
            elif tokens & _TOPIC_TRIGGERS["family"]:
                intent = "family"
            elif tokens & _TOPIC_TRIGGERS["parents"]:
                intent = "parents"
            elif tokens & _TOPIC_TRIGGERS["pets"] and re.search(
                r"\b(your|my|our|did (?:you|we) have|do (?:you|we) have|"
                r"(?:you|we) (?:had|have))\b", normalized
            ):
                intent = "pets"
        return QueryClassification(intent=intent, broad=broad)

    @classmethod
    def classify_query_intent(cls, query: str) -> str | None:
        return cls.classify_query(query).intent

    @staticmethod
    def build_fallback_query(query: str) -> str:
        """Expand only canonical subject taxonomy for one bounded retry."""
        tokens = set(_tokens(query))
        aliases = {
            alias
            for token in tokens
            for alias in _SUBJECT_ALIASES.get(token, ())
        }
        classification = MemoryRelevanceRanker.classify_query(query)
        if classification.intent == "family":
            aliases.update(_SUBJECT_ALIASES["family"])
        elif classification.intent == "pets":
            aliases.update({"pet", "animal", "dog", "cat"})
        if not aliases:
            return query
        return f"{query}\n{' '.join(sorted(aliases))}"

    def rank(
        self,
        memories: list[ApprovedMemoryRetrievalItem],
        query: str,
        *,
        semantic_scores: dict[int, float] | None = None,
    ) -> list[RankedApprovedMemoryItem]:
        query_tokens = list(dict.fromkeys(_tokens(query)))
        raw_query_tokens = list(dict.fromkeys(_raw_tokens(query)))
        if not query_tokens:
            return []
        query_set = set(query_tokens)
        classification = self.classify_query(query)
        requested_pet_subtypes = (
            query_set & _PET_SUBTYPES
            if classification.intent == "pets" else set()
        )
        query_phrase = " ".join(query_tokens)
        subject_matches = list(re.finditer(
            r"\b(?:my|our|your|the)\s+([\w'-]+)|\b(?:what about|tell me about)\s+(?:(?:the|my|our|your)\s+)?([\w'-]+)",
            query.casefold(),
        ))
        explicit_subjects = (
            {_singularize(next(value for value in subject_matches[-1].groups() if value))}
            if subject_matches else set()
        )
        if "\n" in query:
            explicit_subjects.update(
                _singularize(token) for token in _raw_tokens(query.rsplit("\n", 1)[1])
            )
        explicit_subjects -= {
            "name", "breed", "size", "brand", "model", "detail", "color", "colour",
        }
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
            raw_searchable = set(_raw_tokens(
                f"{memory.title} {memory.summary} {memory.category} {_structured_text(memory)}"
            ))
            subject_matches_intent = bool(
                explicit_subjects & TOPIC_GROUPS.get(classification.intent, frozenset())
            )
            enforce_subject = classification.intent is None or (
                "\n" in query and not subject_matches_intent
            )
            if explicit_subjects and enforce_subject and not any(
                subject == candidate or _speech_token_match(subject, candidate)
                for subject in explicit_subjects for candidate in raw_searchable
            ):
                continue
            fuzzy_matches = {
                query_token
                for query_token in raw_query_tokens
                if any(_speech_token_match(query_token, candidate) for candidate in raw_searchable)
            }
            candidate_pet_subtypes = searchable & _PET_SUBTYPES
            if (
                classification.intent == "pets"
                and not (searchable & TOPIC_GROUPS["pets"])
            ):
                continue
            if (
                requested_pet_subtypes
                and candidate_pet_subtypes
                and requested_pet_subtypes.isdisjoint(candidate_pet_subtypes)
            ):
                continue
            semantic_values = _semantic_values(memory)
            intent_attribute_match = classification.intent in _memory_intents(semantic_values)
            topic_matches = expansion & searchable
            if (
                classification.broad
                and classification.intent in _BROAD_MEMORY_CORE
                and memory.memory_id not in (semantic_scores or {})
                and not (_BROAD_MEMORY_CORE[classification.intent] & searchable)
            ):
                continue
            phrase_bonus = 0.35 if query_phrase in " ".join(title_tokens) else (0.25 if query_phrase in " ".join(summary_tokens) else 0.0)
            lexical_score = min(1.0, phrase_bonus + (0.55 if intent_attribute_match else 0.0) + (0.20 if expansion & (title_set | summary_set | category_set) else 0.0) + (0.40 * len(query_set & title_set) / len(query_set)) + (0.20 * len(query_set & summary_set) / len(query_set)) + (0.05 * len(query_set & category_set) / len(query_set)) + (0.10 * len(query_set & structured_set) / len(query_set)) + (0.35 * len(fuzzy_matches) / max(1, len(raw_query_tokens))) + (0.18 * min(2, len(topic_matches)) if classification.broad else 0.0))
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
            matched = [term for term in query_tokens if term in searchable or term in fuzzy_matches or (intent_attribute_match and classification.intent and term in _INTENT_TRIGGERS.get(classification.intent, frozenset()))]
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

        ranked.sort(key=lambda item: (
            int(bool(item.uncertainty_note or item.contradiction_group_id)) if classification.broad else 0,
            -item.relevance_score,
            -self._pet_entity_specificity(item) if classification.intent == "pets" else 0,
            -(item.importance or 0), -_timestamp(item.updated_at), item.memory_id,
        ))
        if classification.intent == "pets":
            specific = [item for item in ranked if self._pet_entity_specificity(item)]
            if specific:
                if query_set & {"name", "named", "breed"}:
                    ranked = specific
                else:
                    ranked = [
                        item for item in ranked
                        if self._pet_entity_specificity(item)
                        or not self._negative_pet_placeholder(item)
                    ]
        supported = [item for item in ranked if not item.uncertainty_note]
        if supported:
            ranked = [
                item for item in ranked
                if not item.uncertainty_note
                or not any(self._uncertain_duplicate(item, fact) for fact in supported)
            ]
        if classification.broad:
            ranked = self._diversify(ranked)
        return ranked

    @staticmethod
    def _uncertain_duplicate(
        uncertain: RankedApprovedMemoryItem,
        supported: RankedApprovedMemoryItem,
    ) -> bool:
        """Drop a cautious restatement when a current supported claim subsumes it."""
        uncertain_title = set(_tokens(uncertain.title))
        supported_title = set(_tokens(supported.title))
        if not uncertain_title or not supported_title:
            return False
        overlap = len(uncertain_title & supported_title)
        return overlap / min(len(uncertain_title), len(supported_title)) >= 0.75

    @staticmethod
    def _pet_entity_specificity(memory: RankedApprovedMemoryItem) -> int:
        """Prefer concrete pet entities/details over generic 'we have pets' placeholders."""
        text = f"{memory.title} {memory.summary}".casefold()
        score = 2 if re.search(r"\bnamed\s+[^.,;]+", text) else 0
        details = getattr(memory, "details", None)
        for value in (getattr(details, "model_extra", None) or {}).values():
            if isinstance(value, dict) and any(
                isinstance(value.get(key), str) and value[key].strip()
                for key in ("name", "breed")
            ):
                score = max(score, 2)
        if any(
            relationship and _singularize(relationship.casefold()) in _PET_SUBTYPES
            for relationship in memory.participant_relationships
        ):
            score = max(score, 2)
        generic_tags = {"animal", "dog", "family", "household", "pet"}
        if set(_tokens(" ".join(memory.tags))) - generic_tags:
            score = max(score, 1)
        return score

    @staticmethod
    def _negative_pet_placeholder(memory: RankedApprovedMemoryItem) -> bool:
        text = f"{memory.title} {memory.summary} {memory.uncertainty_note or ''}".casefold()
        return bool(re.search(
            r"\b(?:no|without)\b[^.]{0,60}\b(?:name|breed|detail)s?\b|"
            r"\b(?:name|breed|detail)s?\b[^.]{0,60}\bnot (?:specified|known|provided)\b|"
            r"\bdoes not (?:provide|specify)\b[^.]{0,60}\b(?:name|breed|detail)s?\b|"
            r"\bdid not (?:state|provide|specify)\b[^.]{0,60}\b(?:name|breed|detail)s?\b|"
            r"\bno additional\b[^.]{0,60}\bdetail",
            text,
        ))

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
