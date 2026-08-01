"""Deterministic structured, intent, and lexical approved-memory ranking."""

import re
import unicodedata
from datetime import datetime, timezone

from app.schemas.memory import (
    ApprovedMemoryRetrievalItem,
    RankedApprovedMemoryItem,
)


_WORD_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
# Structural words add little retrieval meaning. This deliberately small list
# avoids silently removing names, places, or other autobiographical terms.
_STOP_WORDS = frozenset(
    {"a", "an", "and", "are", "for", "in", "is", "of", "s", "the", "to"}
)

_INTENT_TERMS = {
    "profession": frozenset(
        {"profession", "occupation", "career", "job", "work"}
    ),
    "birthplace": frozenset(
        {"born", "birthplace", "birth"}
    ),
    "education": frozenset(
        {"school", "education", "study", "studied", "teach", "taught", "grade"}
    ),
}


def _tokens(value: str | None) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return [
        _singularize(token)
        for token in _WORD_PATTERN.findall(normalized)
        if token not in _STOP_WORDS
    ]


def _singularize(token: str) -> str:
    """Normalize common English plurals without stemming names or short words."""
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith("ses"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _query_intents(tokens: set[str], value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    intents = {
        intent
        for intent, terms in _INTENT_TERMS.items()
        if tokens & terms
    }
    if "place of birth" in normalized or "where" in tokens and "born" in tokens:
        intents.add("birthplace")
    return intents


def _semantic_values(memory: ApprovedMemoryRetrievalItem) -> dict[str, str]:
    details = memory.details
    if details is None:
        return {}
    attributes = details.semantic_attributes
    return {
        key: value
        for key, value in attributes.model_dump().items()
        if isinstance(value, str) and value.strip()
    }


def _memory_intents(semantic_values: dict[str, str]) -> set[str]:
    intents: set[str] = set()
    if semantic_values.get("profession"):
        intents.add("profession")
    if semantic_values.get("birthplace"):
        intents.add("birthplace")
    if (
        semantic_values.get("taught_relationship")
        or semantic_values.get("education_level")
    ):
        intents.add("education")
    return intents


class MemoryRelevanceRanker:
    """Rank normalized memories without AI, network calls, or side effects."""

    def rank(
        self,
        memories: list[ApprovedMemoryRetrievalItem],
        query: str,
    ) -> list[RankedApprovedMemoryItem]:
        query_tokens = list(dict.fromkeys(_tokens(query)))
        if not query_tokens:
            return []
        query_set = set(query_tokens)
        query_intents = _query_intents(query_set, query)
        query_phrase = " ".join(query_tokens)
        ranked: list[RankedApprovedMemoryItem] = []

        for memory in memories:
            title_tokens = _tokens(memory.title)
            summary_tokens = _tokens(memory.summary)
            category_tokens = _tokens(memory.category)
            semantic_values = _semantic_values(memory)
            semantic_tokens = _tokens(" ".join(semantic_values.values()))
            title_set = set(title_tokens)
            summary_set = set(summary_tokens)
            category_set = set(category_tokens)
            semantic_set = set(semantic_tokens)
            memory_intents = _memory_intents(semantic_values)
            matched_intents = query_intents & memory_intents
            title_overlap = len(query_set & title_set) / len(query_set)
            summary_overlap = len(query_set & summary_set) / len(query_set)
            category_overlap = len(query_set & category_set) / len(query_set)
            semantic_overlap = len(query_set & semantic_set) / len(query_set)

            expanded_terms = set().union(
                *(_INTENT_TERMS[intent] for intent in query_intents)
            ) if query_intents else set()
            normalized_intent_match = bool(
                expanded_terms & (title_set | summary_set | category_set)
            )

            phrase_bonus = 0.0
            if query_phrase in " ".join(title_tokens):
                phrase_bonus = 0.35
            elif query_phrase in " ".join(summary_tokens):
                phrase_bonus = 0.25

            score = min(
                1.0,
                phrase_bonus
                + (0.55 if matched_intents else 0.0)
                + (0.20 if normalized_intent_match else 0.0)
                + (0.40 * title_overlap)
                + (0.20 * summary_overlap)
                + (0.05 * category_overlap)
                + (0.10 * semantic_overlap),
            )
            if score <= 0:
                continue

            searchable = title_set | summary_set | category_set | semantic_set
            matched_terms = [
                term
                for term in query_tokens
                if term in searchable
                or any(
                    term in _INTENT_TERMS[intent]
                    for intent in matched_intents
                )
            ]
            ranked.append(
                RankedApprovedMemoryItem(
                    **memory.model_dump(),
                    relevance_score=round(score, 6),
                    matched_terms=matched_terms,
                )
            )

        ranked.sort(
            key=lambda item: (
                -item.relevance_score,
                -(item.importance or 0),
                -_timestamp(item.updated_at),
                item.memory_id,
            )
        )
        return ranked
