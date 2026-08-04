"""OpenAI Responses API provider adapter."""

import logging
from collections.abc import AsyncIterator
from collections.abc import Mapping
from typing import NoReturn, Sequence

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)

from app.config import Settings, get_settings
from app.services.ai.exceptions import (
    AIAuthenticationError,
    AIConfigurationError,
    AIConnectionError,
    AIInvalidResponseError,
    AIProviderError,
    AIProviderUnavailableError,
    AIQuotaExceededError,
    AIRateLimitError,
    AITimeoutError,
)
from app.services.ai.provider import (
    AIMessage,
    AIProvider,
    ExternalKnowledgeMode,
)


logger = logging.getLogger(__name__)


class OpenAIProvider(AIProvider):
    """Generate assistant text with the asynchronous OpenAI Responses API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.validate_configuration(self._settings)

        try:
            timeout = httpx.Timeout(
                connect=self._settings.ai_connect_timeout_seconds,
                read=self._settings.ai_read_timeout_seconds,
                write=self._settings.ai_connect_timeout_seconds,
                pool=self._settings.ai_connect_timeout_seconds,
            )
            self._client = AsyncOpenAI(
                api_key=self._settings.openai_api_key,
                timeout=timeout,
                max_retries=0,
            )
        except (OpenAIError, TypeError, ValueError):
            raise AIConfigurationError(
                "OpenAI client configuration is invalid."
            ) from None

    async def generate_response(
        self,
        messages: Sequence[AIMessage],
        *,
        structured_response_schema: Mapping[str, object] | None = None,
        external_knowledge_mode: ExternalKnowledgeMode | None = None,
    ) -> str:
        """Return assistant text without exposing OpenAI SDK objects."""
        if not messages:
            raise AIInvalidResponseError(
                "At least one AI message is required."
            )

        request_messages = [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in messages
        ]

        request_options = {
            "model": self._settings.ai_model.strip(),
            "input": request_messages,
        }
        if structured_response_schema is not None:
            request_options["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "structured_response",
                    "schema": dict(structured_response_schema),
                    "strict": True,
                }
            }
        if external_knowledge_mode == "web_search":
            request_options["tools"] = [{"type": "web_search"}]

        try:
            response = await self._client.responses.create(**request_options)
        except OpenAIError as exc:
            self._raise_provider_error(exc)

        try:
            assistant_text = response.output_text
        except (AttributeError, TypeError):
            raise AIInvalidResponseError(
                "OpenAI returned an unreadable response."
            ) from None

        if not isinstance(assistant_text, str) or not assistant_text.strip():
            raise AIInvalidResponseError(
                "OpenAI returned an empty response."
            )

        assistant_text = assistant_text.strip()
        if external_knowledge_mode == "web_search":
            citations = self._citation_links(response)
            if citations:
                assistant_text = (
                    f"{assistant_text}\n\nSources:\n"
                    + "\n".join(
                        f"- [{title}]({url})" for title, url in citations
                    )
                )
        return assistant_text

    @staticmethod
    def _citation_links(response: object) -> list[tuple[str, str]]:
        """Project URL annotations without exposing raw SDK tool payloads."""
        links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for output in getattr(response, "output", ()) or ():
            if getattr(output, "type", None) != "message":
                continue
            for content in getattr(output, "content", ()) or ():
                for annotation in getattr(content, "annotations", ()) or ():
                    if getattr(annotation, "type", None) != "url_citation":
                        continue
                    url = getattr(annotation, "url", None)
                    title = getattr(annotation, "title", None)
                    if isinstance(url, str) and url and url not in seen:
                        links.append((title or url, url))
                        seen.add(url)
        return links

    async def stream_response(
        self,
        messages: Sequence[AIMessage],
        *,
        external_knowledge_mode: ExternalKnowledgeMode | None = None,
    ) -> AsyncIterator[str]:
        """Yield only user-visible text deltas from the Responses API."""
        if not messages:
            raise AIInvalidResponseError(
                "At least one AI message is required."
            )

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
            request_options = dict(
                model=self._settings.ai_model.strip(),
                input=request_messages,
                stream=True,
            )
            if external_knowledge_mode == "web_search":
                request_options["tools"] = [{"type": "web_search"}]
            stream = await self._client.responses.create(**request_options)

            async for event in stream:
                event_type = getattr(event, "type", None)
                if not isinstance(event_type, str):
                    raise AIInvalidResponseError(
                        "OpenAI returned an invalid stream event."
                    )

                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", None)
                    if not isinstance(delta, str):
                        raise AIInvalidResponseError(
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
                    self._raise_stream_event_error(event)
        except AIProviderError:
            raise
        except AIInvalidResponseError:
            raise
        except OpenAIError as exc:
            self._raise_provider_error(exc)
        finally:
            if stream is not None:
                try:
                    await stream.close()
                except OpenAIError:
                    logger.warning(
                        "OpenAI stream cleanup failed.",
                        exc_info=True,
                    )

        if not completed:
            raise AIProviderError("OpenAI stream ended before completion.")

    @staticmethod
    def validate_configuration(settings: Settings) -> None:
        """Validate OpenAI-specific settings without making a network call."""
        if not isinstance(settings.ai_model, str) or not settings.ai_model.strip():
            raise AIConfigurationError("AI_MODEL must be configured.")
        if (
            not isinstance(settings.openai_api_key, str)
            or not settings.openai_api_key.strip()
        ):
            raise AIConfigurationError("OPENAI_API_KEY must be configured.")

    @classmethod
    def _raise_provider_error(cls, exc: OpenAIError) -> NoReturn:
        if isinstance(exc, RateLimitError):
            if cls._error_code(exc) == "insufficient_quota":
                raise AIQuotaExceededError(
                    "OpenAI quota is exhausted."
                ) from None
            raise AIRateLimitError(
                "OpenAI temporarily rate limited the request.",
                retry_after=cls._retry_after(exc),
            ) from None
        if isinstance(
            exc,
            (AuthenticationError, PermissionDeniedError),
        ):
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
        if isinstance(exc, (InternalServerError, APIStatusError)):
            if getattr(exc, "status_code", 0) >= 500:
                raise AIProviderUnavailableError(
                    "OpenAI is temporarily unavailable."
                ) from None
        if isinstance(exc, BadRequestError):
            raise AIProviderError(
                "OpenAI rejected the request."
            ) from None
        raise AIProviderError(
            "OpenAI could not generate a response."
        ) from None

    @staticmethod
    def _error_code(exc: OpenAIError) -> str | None:
        body = getattr(exc, "body", None)
        if isinstance(body, Mapping):
            error = body.get("error", body)
            if isinstance(error, Mapping):
                code = error.get("code")
                return code if isinstance(code, str) else None
        code = getattr(exc, "code", None)
        return code if isinstance(code, str) else None

    @staticmethod
    def _retry_after(exc: OpenAIError) -> float | None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        value = headers.get("retry-after") if headers is not None else None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _raise_stream_event_error(event: object) -> NoReturn:
        response = getattr(event, "response", None)
        error = (
            getattr(event, "error", None)
            or getattr(response, "error", None)
        )
        code = getattr(error, "code", None)

        if code == "insufficient_quota":
            raise AIQuotaExceededError(
                "OpenAI quota is exhausted."
            )
        if code in {"rate_limit_exceeded", "rate_limit"}:
            raise AIRateLimitError(
                "OpenAI temporarily rate limited the request."
            )
        if code in {"server_error", "service_unavailable"}:
            raise AIProviderUnavailableError(
                "OpenAI is temporarily unavailable."
            )
        raise AIProviderError(
            "OpenAI did not complete the response."
        )
