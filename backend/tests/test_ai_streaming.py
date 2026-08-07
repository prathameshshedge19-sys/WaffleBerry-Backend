"""Tests for provider-neutral AI streaming behavior."""

import unittest
from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace

from app.services.ai.ai_service import AIService
from app.services.ai.context_builder import ContextBuilder
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.provider import AIMessage, AIProvider, GenerationOptions


class FakeStreamingProvider(AIProvider):
    """Deterministic provider used without external API calls."""

    async def generate_response(
        self,
        messages: Sequence[AIMessage],
    ) -> str:
        return "Hello world"

    async def stream_response(
        self,
        messages: Sequence[AIMessage],
    ) -> AsyncIterator[str]:
        yield "Hello"
        yield " "
        yield "world"


class FakeOpenAIEventStream:
    """Async stream shaped like the OpenAI SDK response stream."""

    def __init__(self):
        self._events = iter(
            [
                SimpleNamespace(
                    type="response.output_text.delta",
                    delta="Hello",
                ),
                SimpleNamespace(
                    type="response.output_text.delta",
                    delta=" Berry",
                ),
                SimpleNamespace(type="response.completed"),
            ]
        )

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._events)
        except StopIteration:
            raise StopAsyncIteration from None

    async def close(self):
        return None


class AIStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_history_and_latest_message_order(self):
        service = AIService(FakeStreamingProvider())
        messages = ContextBuilder(10).build_chat_messages(
            [
                SimpleNamespace(
                    role="user",
                    content="Earlier question",
                ),
                SimpleNamespace(
                    role="assistant",
                    content="Earlier answer",
                ),
            ],
            "Latest question",
        )

        self.assertEqual(
            [message.role for message in messages],
            ["system", "user", "assistant", "user"],
        )
        self.assertEqual(messages[-1].content, "Latest question")

    async def test_stream_deltas_remain_in_provider_order(self):
        service = AIService(FakeStreamingProvider())
        messages = ContextBuilder(10).build_chat_messages([], "Hello")

        deltas = [
            delta
            async for delta in service.stream_response(messages)
        ]

        self.assertEqual(deltas, ["Hello", " ", "world"])

    async def test_openai_sdk_style_event_attributes_are_streamed(self):
        provider = object.__new__(OpenAIProvider)
        provider._settings = SimpleNamespace(ai_model="test-model")
        provider._client = SimpleNamespace(
            responses=SimpleNamespace(
                create=self._create_sdk_style_stream
            )
        )

        deltas = [
            delta
            async for delta in provider.stream_response(
                [AIMessage(role="user", content="Hello")]
            )
        ]

        self.assertEqual(deltas, ["Hello", " Berry"])

    async def test_live_call_output_budget_is_forwarded_to_openai_stream(self):
        provider = object.__new__(OpenAIProvider)
        provider._settings = SimpleNamespace(ai_model="test-model")
        captured = {}

        async def create(**kwargs):
            captured.update(kwargs)
            return FakeOpenAIEventStream()

        provider._client = SimpleNamespace(
            responses=SimpleNamespace(create=create)
        )
        options = GenerationOptions(max_output_tokens=240)
        deltas = [
            delta async for delta in provider.stream_response(
                [AIMessage(role="user", content="Hello")],
                generation_options=options,
            )
        ]
        self.assertEqual(deltas, ["Hello", " Berry"])
        self.assertEqual(captured["max_output_tokens"], 240)

    @staticmethod
    async def _create_sdk_style_stream(**_kwargs):
        return FakeOpenAIEventStream()


if __name__ == "__main__":
    unittest.main()
