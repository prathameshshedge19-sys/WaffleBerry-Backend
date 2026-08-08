"""Provider-independent AI orchestration."""

import logging
from collections.abc import AsyncIterator, Mapping, Sequence

from app.services.ai.exceptions import AIResponseError
from app.services.ai.provider import (
    AIMessage, AIProvider, ExternalKnowledgeMode, GenerationOptions,
)
from app.services.ai.retry import AIRetryPolicy


logger = logging.getLogger(__name__)


class AIService:
    """Delegate prepared provider-neutral context to an AI provider."""

    def __init__(
        self,
        provider: AIProvider,
        retry_policy: AIRetryPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._retry_policy = retry_policy or AIRetryPolicy.no_retries()

    async def generate_response(
        self,
        messages: Sequence[AIMessage],
        *,
        structured_response_schema: Mapping[str, object] | None = None,
        external_knowledge_mode: ExternalKnowledgeMode | None = None,
        generation_options: GenerationOptions | None = None,
    ) -> str:
        """Generate and validate assistant text through the configured provider."""
        retry_number = 0
        while True:
            try:
                if (structured_response_schema is None
                        and external_knowledge_mode is None
                        and generation_options is None):
                    response = await self._provider.generate_response(messages)
                else:
                    options = {}
                    if structured_response_schema is not None:
                        options["structured_response_schema"] = structured_response_schema
                    if external_knowledge_mode is not None:
                        options["external_knowledge_mode"] = external_knowledge_mode
                    if generation_options is not None:
                        options["generation_options"] = generation_options
                    response = await self._provider.generate_response(messages, **options)
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
        *,
        external_knowledge_mode: ExternalKnowledgeMode | None = None,
        generation_options: GenerationOptions | None = None,
    ) -> AsyncIterator[str]:
        """Yield validated plain-text deltas from the configured provider."""
        retry_number = 0
        while True:
            received_text = False
            try:
                options = {}
                if external_knowledge_mode is not None:
                    options["external_knowledge_mode"] = external_knowledge_mode
                if generation_options is not None:
                    options["generation_options"] = generation_options
                stream = self._provider.stream_response(messages, **options)
                async for delta in stream:
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
