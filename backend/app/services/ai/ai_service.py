"""Provider-independent AI orchestration."""

import logging
from collections.abc import AsyncIterator, Iterable, Sequence
from typing import Protocol

from app.services.ai.exceptions import AIResponseError
from app.services.ai.prompt_builder import PromptBuilder
from app.services.ai.provider import AIMessage, AIMessageRole, AIProvider
from app.services.ai.retry import AIRetryPolicy


logger = logging.getLogger(__name__)


class ConversationMessage(Protocol):
    """Structural type required for persisted conversation messages."""

    role: object
    content: str


class AIService:
    """Prepare Berry conversations and delegate generation to a provider."""

    def __init__(
        self,
        provider: AIProvider,
        prompt_builder: PromptBuilder | None = None,
        retry_policy: AIRetryPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._retry_policy = retry_policy or AIRetryPolicy.no_retries()

    def build_messages(
        self,
        history: Iterable[ConversationMessage],
        user_message: str,
    ) -> list[AIMessage]:
        """Convert stored history and a new user message into AI input."""
        normalized_user_message = self._normalize_content(user_message)
        messages = [
            AIMessage(
                role="system",
                content=self._prompt_builder.build_berry_system_prompt(),
            )
        ]

        for message in history:
            messages.append(
                AIMessage(
                    role=self._normalize_role(message.role),
                    content=self._normalize_content(message.content),
                )
            )

        messages.append(AIMessage(role="user", content=normalized_user_message))
        return messages

    async def generate_response(
        self,
        messages: Sequence[AIMessage],
    ) -> str:
        """Generate and validate assistant text through the configured provider."""
        retry_number = 0
        while True:
            try:
                response = await self._provider.generate_response(messages)
                if not isinstance(response, str) or not response.strip():
                    raise AIResponseError(
                        "AI provider returned an empty response."
                    )
                return response.strip()
            except Exception as exc:
                retry_number += 1
                if not self._retry_policy.should_retry(exc, retry_number):
                    raise
                logger.warning(
                    "Retrying AI generation after %s (attempt %d).",
                    getattr(exc, "code", "provider_error"),
                    retry_number,
                )
                await self._retry_policy.wait(exc, retry_number)

    async def stream_response(
        self,
        messages: Sequence[AIMessage],
    ) -> AsyncIterator[str]:
        """Yield validated plain-text deltas from the configured provider."""
        retry_number = 0
        while True:
            received_text = False
            try:
                async for delta in self._provider.stream_response(messages):
                    if not isinstance(delta, str):
                        raise AIResponseError(
                            "AI provider returned an invalid stream delta."
                        )
                    if not delta:
                        continue

                    received_text = True
                    yield delta

                if not received_text:
                    raise AIResponseError(
                        "AI provider returned an empty response."
                    )
                return
            except Exception as exc:
                retry_number += 1
                if (
                    received_text
                    or not self._retry_policy.should_retry(
                        exc, retry_number
                    )
                ):
                    raise
                logger.warning(
                    "Retrying AI stream before first delta after %s "
                    "(attempt %d).",
                    getattr(exc, "code", "provider_error"),
                    retry_number,
                )
                await self._retry_policy.wait(exc, retry_number)

    @staticmethod
    def _normalize_role(role: object) -> AIMessageRole:
        value = getattr(role, "value", role)
        if value not in {"system", "user", "assistant"}:
            raise AIResponseError(f"Unsupported conversation role: {value!r}.")
        return value

    @staticmethod
    def _normalize_content(content: str) -> str:
        if not isinstance(content, str) or not content.strip():
            raise AIResponseError("Conversation message content must not be blank.")
        return content.strip()
