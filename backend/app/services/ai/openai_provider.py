"""OpenAI Responses API provider adapter."""

from collections.abc import AsyncIterator
from typing import NoReturn, Sequence

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)

from app.config import Settings, get_settings
from app.services.ai.exceptions import (
    AIAuthenticationError,
    AIConfigurationError,
    AIConnectionError,
    AIProviderError,
    AIRateLimitError,
    AIResponseError,
    AITimeoutError,
)
from app.services.ai.provider import AIMessage, AIProvider


class OpenAIProvider(AIProvider):
    """Generate assistant text with the asynchronous OpenAI Responses API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._validate_configuration()

        try:
            self._client = AsyncOpenAI(api_key=self._settings.openai_api_key)
        except OpenAIError:
            raise AIConfigurationError(
                "OpenAI client configuration is invalid."
            ) from None

    async def generate_response(
        self,
        messages: Sequence[AIMessage],
    ) -> str:
        """Return assistant text without exposing OpenAI SDK objects."""
        if not messages:
            raise AIResponseError("At least one AI message is required.")

        request_messages = [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in messages
        ]

        try:
            response = await self._client.responses.create(
                model=self._settings.ai_model.strip(),
                input=request_messages,
            )
        except OpenAIError as exc:
            self._raise_provider_error(exc)

        try:
            assistant_text = response.output_text
        except (AttributeError, TypeError):
            raise AIResponseError(
                "OpenAI returned an unreadable response."
            ) from None

        if not isinstance(assistant_text, str) or not assistant_text.strip():
            raise AIResponseError("OpenAI returned an empty response.")

        return assistant_text.strip()

    async def stream_response(
        self,
        messages: Sequence[AIMessage],
    ) -> AsyncIterator[str]:
        """Yield only user-visible text deltas from the Responses API."""
        if not messages:
            raise AIResponseError("At least one AI message is required.")

        request_messages = [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in messages
        ]
        stream = None
        completed = False

        try:
            stream = await self._client.responses.create(
                model=self._settings.ai_model.strip(),
                input=request_messages,
                stream=True,
            )

            async for event in stream:
                event_type = getattr(event, "type", None)
                if not isinstance(event_type, str):
                    raise AIResponseError(
                        "OpenAI returned an invalid stream event."
                    )

                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", None)
                    if not isinstance(delta, str):
                        raise AIResponseError(
                            "OpenAI returned an invalid text delta."
                        )
                    if delta:
                        yield delta
                elif event_type == "response.completed":
                    completed = True
                elif event_type in {
                    "response.failed",
                    "response.incomplete",
                    "error",
                }:
                    raise AIProviderError(
                        "OpenAI did not complete the response."
                    )
        except AIProviderError:
            raise
        except AIResponseError:
            raise
        except OpenAIError as exc:
            self._raise_provider_error(exc)
        finally:
            if stream is not None:
                try:
                    await stream.close()
                except OpenAIError:
                    pass

        if not completed:
            raise AIProviderError("OpenAI stream ended before completion.")

    def _validate_configuration(self) -> None:
        provider = self._settings.ai_provider.strip().lower()
        if provider != "openai":
            raise AIConfigurationError(
                f"Unsupported AI provider: {provider or '<empty>'}."
            )
        if not self._settings.ai_model.strip():
            raise AIConfigurationError("AI_MODEL must be configured.")
        if (
            not isinstance(self._settings.openai_api_key, str)
            or not self._settings.openai_api_key.strip()
        ):
            raise AIConfigurationError("OPENAI_API_KEY must be configured.")

    @staticmethod
    def _raise_provider_error(exc: OpenAIError) -> NoReturn:
        if isinstance(exc, RateLimitError):
            raise AIRateLimitError(
                "OpenAI rate limit or quota was reached."
            ) from None
        if isinstance(exc, AuthenticationError):
            raise AIAuthenticationError(
                "OpenAI rejected the configured credentials."
            ) from None
        if isinstance(exc, APITimeoutError):
            raise AITimeoutError(
                "OpenAI response generation timed out."
            ) from None
        if isinstance(exc, APIConnectionError):
            raise AIConnectionError(
                "OpenAI could not be reached."
            ) from None
        raise AIProviderError(
            "OpenAI could not generate a response."
        ) from None
