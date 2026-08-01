"""Deterministic lexical relevance ranking for approved memories."""

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
        query_phrase = " ".join(query_tokens)
        ranked: list[RankedApprovedMemoryItem] = []

        for memory in memories:
            title_tokens = _tokens(memory.title)
            summary_tokens = _tokens(memory.summary)
            category_tokens = _tokens(memory.category)
            title_set = set(title_tokens)
            summary_set = set(summary_tokens)
            category_set = set(category_tokens)
            title_overlap = len(query_set & title_set) / len(query_set)
            summary_overlap = len(query_set & summary_set) / len(query_set)
            category_overlap = len(query_set & category_set) / len(query_set)

            phrase_bonus = 0.0
            if query_phrase in " ".join(title_tokens):
                phrase_bonus = 0.35
            elif query_phrase in " ".join(summary_tokens):
                phrase_bonus = 0.25

            score = min(
                1.0,
                phrase_bonus
                + (0.40 * title_overlap)
                + (0.20 * summary_overlap)
                + (0.05 * category_overlap),
            )
            if score <= 0:
                continue

            searchable = title_set | summary_set | category_set
            matched_terms = [
                term for term in query_tokens if term in searchable
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
