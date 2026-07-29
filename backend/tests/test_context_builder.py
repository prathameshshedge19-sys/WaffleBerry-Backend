"""Tests for bounded provider-neutral conversation context."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.ai.context_builder import ContextBuilder
from app.services.chat_service import ChatService


def message(role, content):
    return SimpleNamespace(role=role, content=content)


class CapturingAIService:
    def __init__(self):
        self.generated_messages = None
        self.streamed_messages = None

    async def generate_response(self, messages):
        self.generated_messages = messages
        return "generated"

    async def stream_response(self, messages):
        self.streamed_messages = messages
        yield "streamed"


class ContextBuilderTests(unittest.IsolatedAsyncioTestCase):
    def test_story_context_uses_dedicated_prompt_and_ordered_history(self):
        messages = ContextBuilder(6).build_story_messages(
            [
                SimpleNamespace(
                    role="user",
                    content="We lived near the mountains.",
                ),
                SimpleNamespace(
                    role="assistant",
                    content="What felt most like home?",
                ),
            ],
            chapter="Childhood",
            relationship="Father",
            display_name="Dad",
        )

        self.assertEqual(
            [message.role for message in messages],
            ["system", "user", "assistant"],
        )
        self.assertIn("story guide", messages[0].content.lower())
        self.assertIn("Chapter: Childhood", messages[0].content)
        self.assertIn("Relationship: Father", messages[0].content)
        self.assertIn("Display name: Dad", messages[0].content)
        prompt = messages[0].content.lower()
        self.assertIn("memory archivist", prompt)
        self.assertIn("one main story prompt at a time", prompt)
        self.assertIn("not the companion or legacy person", prompt)
        self.assertIn("never roleplay", prompt)
        self.assertIn("something sensitive", prompt)
        self.assertIn("ask to stop", prompt)

    def test_new_story_session_requests_a_warm_chapter_opening(self):
        messages = ContextBuilder(6).build_story_messages(
            [],
            chapter="Childhood",
            relationship="Father",
            display_name="Dad",
        )

        self.assertEqual(
            [message.role for message in messages],
            ["system", "user"],
        )
        self.assertIn(
            "Open this chapter naturally",
            messages[-1].content,
        )
        self.assertIn(
            "Do not begin with a cold direct prompt",
            messages[-1].content,
        )

    async def test_story_stream_reuses_chat_and_ai_services(self):
        ai_service = CapturingAIService()
        service = ChatService(
            ai_service,
            ContextBuilder(8),
        )

        deltas = [
            delta
            async for delta in service.stream_story_response(
                [
                    SimpleNamespace(
                        role="user",
                        content="We lived near the mountains.",
                    )
                ],
                chapter="Childhood",
                relationship="Father",
                display_name="Dad",
            )
        ]

        self.assertEqual(deltas, ["streamed"])
        self.assertIn(
            "story guide",
            ai_service.streamed_messages[0].content.lower(),
        )
        self.assertEqual(
            ai_service.streamed_messages[-1].content,
            "We lived near the mountains.",
        )

    def test_empty_conversation_contains_prompt_and_latest_user(self):
        messages = ContextBuilder(6).build_chat_messages([], "Hello")

        self.assertEqual(
            [item.role for item in messages],
            ["system", "user"],
        )
        self.assertEqual(messages[-1].content, "Hello")

    def test_only_system_prompt_is_supported(self):
        messages = ContextBuilder(6).build_messages([], None)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, "system")

    def test_normal_multi_turn_order_is_preserved(self):
        history = [
            message("user", "Question one"),
            message("assistant", "Answer one"),
            message("user", "Question two"),
            message("assistant", "Answer two"),
        ]

        messages = ContextBuilder(8).build_chat_messages(
            history,
            "Latest question",
        )

        self.assertEqual(
            [item.content for item in messages[1:]],
            [
                "Question one",
                "Answer one",
                "Question two",
                "Answer two",
                "Latest question",
            ],
        )

    def test_long_history_is_trimmed_to_configured_limit(self):
        history = [
            message(
                "user" if index % 2 == 0 else "assistant",
                f"Message {index}",
            )
            for index in range(20)
        ]

        messages = ContextBuilder(6).build_chat_messages(
            history,
            "Latest",
        )

        self.assertLessEqual(len(messages), 6)
        self.assertEqual(messages[0].role, "system")
        self.assertEqual(messages[-1].content, "Latest")
        self.assertEqual(
            [item.content for item in messages[1:-1]],
            ["Message 16", "Message 17", "Message 18", "Message 19"],
        )

    def test_recent_pairs_are_preserved_where_budget_allows(self):
        history = [
            message("user", "Old question"),
            message("assistant", "Old answer"),
            message("user", "Recent question"),
            message("assistant", "Recent answer"),
        ]

        messages = ContextBuilder(5).build_chat_messages(
            history,
            "Follow-up",
        )

        self.assertEqual(
            [item.content for item in messages[1:-1]],
            ["Recent question", "Recent answer"],
        )

    def test_system_messages_and_malformed_history_are_ignored(self):
        history = [
            message("system", "Duplicate prompt"),
            message("unknown", "Malformed role"),
            message("user", "   "),
            message("user", "Valid"),
            message("assistant", "Reply"),
        ]

        messages = ContextBuilder(6).build_chat_messages(
            history,
            "Latest",
        )

        self.assertEqual(
            [item.role for item in messages],
            ["system", "user", "assistant", "user"],
        )
        self.assertEqual(
            sum(item.role == "system" for item in messages),
            1,
        )

    async def test_streaming_and_non_streaming_use_identical_context(self):
        history_descending = [
            message("assistant", "Answer"),
            message("user", "Question"),
        ]
        query = MagicMock()
        query.filter.return_value.order_by.return_value.limit.return_value.all.side_effect = [
            list(history_descending),
            list(history_descending),
        ]
        db = MagicMock()
        db.query.return_value = query
        conversation = SimpleNamespace(conversation_id=7)
        ai_service = CapturingAIService()
        service = ChatService(
            ai_service,
            ContextBuilder(6),
        )

        await service.generate_response(
            db,
            conversation,
            "Latest",
        )
        streamed = [
            delta
            async for delta in service.stream_response(
                db,
                conversation,
                "Latest",
            )
        ]

        self.assertEqual(streamed, ["streamed"])
        self.assertEqual(
            ai_service.generated_messages,
            ai_service.streamed_messages,
        )
        query.filter.return_value.order_by.return_value.limit.assert_called_with(
            4
        )


if __name__ == "__main__":
    unittest.main()
