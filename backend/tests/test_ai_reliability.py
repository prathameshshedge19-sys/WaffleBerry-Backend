"""Deterministic tests for provider-neutral AI reliability behavior."""

import unittest
from collections.abc import AsyncIterator, Sequence

from app.services.ai.ai_service import AIService
from app.services.ai.exceptions import (
    AIAuthenticationError,
    AIConnectionError,
    AIInvalidResponseError,
    AIProviderUnavailableError,
    AIQuotaExceededError,
    AIRateLimitError,
    AITimeoutError,
)
from app.services.ai.provider import AIMessage, AIProvider
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.retry import AIRetryPolicy


async def no_wait(_delay: float) -> None:
    return None


class SequencedProvider(AIProvider):
    """Return or raise configured outcomes once per provider attempt."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate_response(
        self,
        messages: Sequence[AIMessage],
    ) -> str:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def stream_response(
        self,
        messages: Sequence[AIMessage],
    ) -> AsyncIterator[str]:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        for item in outcome:
            if isinstance(item, Exception):
                raise item
            yield item


class AIReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def service(self, provider: AIProvider) -> AIService:
        return AIService(
            provider,
            retry_policy=AIRetryPolicy(
                max_retries=2,
                base_delay_seconds=0,
                max_delay_seconds=0,
                jitter_seconds=0,
                sleep=no_wait,
            ),
        )

    async def test_connection_failure_retries_then_succeeds(self):
        provider = SequencedProvider(
            [
                AIConnectionError("connection failed"),
                "Recovered",
            ]
        )

        result = await self.service(provider).generate_response(
            [AIMessage(role="user", content="Hello")]
        )

        self.assertEqual(result, "Recovered")
        self.assertEqual(provider.calls, 2)

    async def test_provider_unavailable_and_timeout_are_retryable(self):
        for error in (
            AIProviderUnavailableError("unavailable"),
            AITimeoutError("timeout"),
        ):
            with self.subTest(error=type(error).__name__):
                provider = SequencedProvider([error, "Recovered"])
                result = await self.service(provider).generate_response(
                    [AIMessage(role="user", content="Hello")]
                )
                self.assertEqual(result, "Recovered")
                self.assertEqual(provider.calls, 2)

    async def test_temporary_rate_limit_is_retryable(self):
        provider = SequencedProvider(
            [AIRateLimitError("limited"), "Recovered"]
        )

        result = await self.service(provider).generate_response(
            [AIMessage(role="user", content="Hello")]
        )

        self.assertEqual(result, "Recovered")
        self.assertEqual(provider.calls, 2)

    async def test_authentication_and_quota_are_not_retried(self):
        for error in (
            AIAuthenticationError("invalid credentials"),
            AIQuotaExceededError("quota"),
        ):
            with self.subTest(error=type(error).__name__):
                provider = SequencedProvider([error, "Not reached"])
                with self.assertRaises(type(error)):
                    await self.service(provider).generate_response(
                        [AIMessage(role="user", content="Hello")]
                    )
                self.assertEqual(provider.calls, 1)

    async def test_empty_output_is_invalid_and_not_retried(self):
        provider = SequencedProvider(["", "Not reached"])

        with self.assertRaises(AIInvalidResponseError):
            await self.service(provider).generate_response(
                [AIMessage(role="user", content="Hello")]
            )

        self.assertEqual(provider.calls, 1)

    async def test_stream_retries_only_before_first_delta(self):
        provider = SequencedProvider(
            [
                [AIConnectionError("connection failed")],
                ["Hello", " Berry"],
            ]
        )

        deltas = [
            delta
            async for delta in self.service(provider).stream_response(
                [AIMessage(role="user", content="Hello")]
            )
        ]

        self.assertEqual(deltas, ["Hello", " Berry"])
        self.assertEqual(provider.calls, 2)

    async def test_stream_does_not_retry_after_first_delta(self):
        provider = SequencedProvider(
            [
                [
                    "Partial",
                    AIConnectionError("connection failed"),
                ],
                ["Must not run"],
            ]
        )
        received = []

        with self.assertRaises(AIConnectionError):
            async for delta in self.service(provider).stream_response(
                [AIMessage(role="user", content="Hello")]
            ):
                received.append(delta)

        self.assertEqual(received, ["Partial"])
        self.assertEqual(provider.calls, 1)

    def test_structured_stream_quota_error_is_classified(self):
        event = type(
            "Event",
            (),
            {
                "error": type(
                    "Error",
                    (),
                    {"code": "insufficient_quota"},
                )()
            },
        )()

        with self.assertRaises(AIQuotaExceededError):
            OpenAIProvider._raise_stream_event_error(event)


if __name__ == "__main__":
    unittest.main()
