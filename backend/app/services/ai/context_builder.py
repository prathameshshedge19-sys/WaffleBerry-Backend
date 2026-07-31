"""Provider-neutral conversation context preparation."""

from collections.abc import Iterable
from typing import Protocol

from app.services.ai.exceptions import AIInvalidResponseError
from app.services.ai.prompt_builder import PromptBuilder
from app.services.ai.provider import AIMessage, AIMessageRole


class ConversationMessage(Protocol):
    """Stored message fields used by context preparation."""

    role: object
    content: str


class ContextBuilder:
    """Build bounded Berry context with stable chronological ordering."""

    def __init__(
        self,
        max_context_messages: int,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        if max_context_messages < 2:
            raise ValueError(
                "max_context_messages must allow a system and user message."
            )
        self.max_context_messages = max_context_messages
        self._prompt_builder = prompt_builder or PromptBuilder()

    @property
    def history_query_limit(self) -> int:
        """Maximum persisted rows that can fit beside prompt and latest user."""
        return max(0, self.max_context_messages - 2)

    def build_messages(
        self,
        history: Iterable[ConversationMessage],
        latest_user_message: str | None,
        *,
        grounding_context: str | None = None,
    ) -> list[AIMessage]:
        """Return one system prompt, recent history, and the latest user."""
        system_prompt = self._prompt_builder.build_berry_system_prompt()
        if grounding_context:
            system_prompt = f"{system_prompt}\n\n{grounding_context}"
        system_message = AIMessage(
            role="system",
            content=system_prompt,
        )
        latest = self._normalize_content(latest_user_message)
        history_budget = self.max_context_messages - 1 - bool(latest)

        valid_history = []
        for message in history:
            normalized = self._normalize_stored_message(message)
            if normalized is not None:
                valid_history.append(normalized)

        selected = self._select_recent_history(
            valid_history,
            history_budget,
        )
        messages = [system_message, *selected]
        if latest is not None:
            messages.append(AIMessage(role="user", content=latest))
        return messages

    @staticmethod
    def _select_recent_history(
        messages: list[AIMessage],
        budget: int,
    ) -> list[AIMessage]:
        if budget <= 0:
            return []
        if len(messages) <= budget:
            return messages

        start = len(messages) - budget

        # Prefer not to begin with an orphaned assistant reply. Dropping it
        # preserves complete recent turns without sacrificing newer messages.
        if (
            messages[start].role == "assistant"
            and start + 1 < len(messages)
        ):
            start += 1

        return messages[start:]

    @classmethod
    def _normalize_stored_message(
        cls,
        message: ConversationMessage,
    ) -> AIMessage | None:
        role = cls._normalize_role(getattr(message, "role", None))
        content = cls._normalize_content(
            getattr(message, "content", None)
        )
        if role is None or role == "system" or content is None:
            return None
        return AIMessage(role=role, content=content)

    @staticmethod
    def _normalize_role(role: object) -> AIMessageRole | None:
        value = getattr(role, "value", role)
        if value in {"user", "assistant"}:
            return value
        return None

    @staticmethod
    def _normalize_content(content: object) -> str | None:
        if content is None:
            return None
        if not isinstance(content, str):
            return None
        normalized = content.strip()
        return normalized or None

    def build_chat_messages(
        self,
        history: Iterable[ConversationMessage],
        latest_user_message: str,
        *,
        grounding_context: str | None = None,
    ) -> list[AIMessage]:
        """Build chat context and require a valid latest user message."""
        if self._normalize_content(latest_user_message) is None:
            raise AIInvalidResponseError(
                "Latest user message must not be blank."
            )
        messages = self.build_messages(
            history,
            latest_user_message,
            grounding_context=grounding_context,
        )
        return messages

    def build_story_messages(
        self,
        history: Iterable[ConversationMessage],
        *,
        chapter: str,
        relationship: str,
        display_name: str,
    ) -> list[AIMessage]:
        """Build bounded Story Guide context without database persistence."""
        system_message = AIMessage(
            role="system",
            content=self._prompt_builder.build_story_guide_system_prompt(
                chapter=chapter,
                relationship=relationship,
                display_name=display_name,
            ),
        )
        valid_history = [
            normalized
            for message in history
            if (
                normalized := self._normalize_stored_message(message)
            ) is not None
        ]
        selected = self._select_recent_history(
            valid_history,
            self.max_context_messages - 1,
        )
        if not selected:
            selected = [
                AIMessage(
                    role="user",
                    content=(
                        "Open this chapter naturally with a brief, warm "
                        "introduction, then invite the person to share one "
                        "memory. Do not begin with a cold direct prompt."
                    ),
                )
            ]
        return [system_message, *selected]
