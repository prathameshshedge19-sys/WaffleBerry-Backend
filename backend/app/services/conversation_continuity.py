"""Transient conversation context used to clarify Persona retrieval queries."""

import re
from collections.abc import Iterable

from app.services.ai.context_builder import ConversationMessage


_WORD_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_EXPLICIT_SWITCH_PATTERNS = (
    re.compile(r"\bchange (?:the )?(?:subject|topic)\b", re.IGNORECASE),
    re.compile(r"\b(?:moving|move) on\b", re.IGNORECASE),
    re.compile(r"\b(?:instead|enough about)\b", re.IGNORECASE),
    re.compile(r"\b(?:now |next )?tell me about\b", re.IGNORECASE),
)
_REFERENCE_TERMS = frozenset(
    {
        "after",
        "again",
        "did",
        "feel",
        "happened",
        "he",
        "her",
        "him",
        "it",
        "next",
        "she",
        "that",
        "then",
        "there",
        "they",
        "those",
        "what",
        "when",
        "where",
        "who",
        "why",
    }
)


class ConversationContinuity:
    """Build a bounded query from current-conversation user context only."""

    def __init__(self, *, max_prior_user_messages: int = 4) -> None:
        if max_prior_user_messages < 0:
            raise ValueError("max_prior_user_messages must not be negative.")
        self._max_prior_user_messages = max_prior_user_messages

    def build_retrieval_query(
        self,
        history: Iterable[ConversationMessage],
        latest_user_message: str,
    ) -> str:
        """Add recent user context only when the latest turn is referential."""
        latest = latest_user_message.strip()
        if (
            not latest
            or self._max_prior_user_messages == 0
            or not self._needs_prior_context(latest)
        ):
            return latest

        prior_user_messages = [
            content
            for item in history
            if self._is_user(item)
            and (content := self._content(item)) is not None
        ]
        for index in range(len(prior_user_messages) - 1, -1, -1):
            if self._is_explicit_switch(prior_user_messages[index]):
                prior_user_messages = prior_user_messages[index:]
                break
        selected = prior_user_messages[-self._max_prior_user_messages :]
        return "\n".join([*selected, latest])

    @staticmethod
    def _needs_prior_context(message: str) -> bool:
        if ConversationContinuity._is_explicit_switch(message):
            return False
        words = [word.casefold() for word in _WORD_PATTERN.findall(message)]
        if not words:
            return False
        return len(words) <= 6 or bool(_REFERENCE_TERMS.intersection(words))

    @staticmethod
    def _is_explicit_switch(message: str) -> bool:
        return any(pattern.search(message) for pattern in _EXPLICIT_SWITCH_PATTERNS)

    @staticmethod
    def _is_user(message: ConversationMessage) -> bool:
        role = getattr(message, "role", None)
        return getattr(role, "value", role) == "user"

    @staticmethod
    def _content(message: ConversationMessage) -> str | None:
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            return None
        normalized = content.strip()
        return normalized or None
