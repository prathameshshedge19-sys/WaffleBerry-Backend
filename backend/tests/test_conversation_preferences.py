"""Conversation presentation preferences stay separate from grounding truth."""

import unittest

from app.services.ai.provider import AIMessage
from app.services.chat_service import ChatService


class ConversationPresentationTests(unittest.TestCase):
    def test_style_and_length_add_only_allowlisted_presentation_guidance(self):
        original = [
            AIMessage(role="system", content="Grounding contract"),
            AIMessage(role="user", content="Tell me the memory"),
        ]
        messages = ChatService._apply_presentation_preferences(
            list(original), "gentle", "short", False,
        )
        self.assertEqual(messages[0], original[0])
        self.assertEqual(messages[2], original[1])
        self.assertIn("calm, soft wording", messages[1].content)
        self.assertIn("Keep the answer concise", messages[1].content)
        self.assertIn("never alter retrieval, identity, grounding", messages[1].content)

    def test_live_call_grounding_messages_are_not_modified_by_chat_presentation(self):
        original = [AIMessage(role="system", content="Live grounding")]
        messages = ChatService._apply_presentation_preferences(
            original, "expressive", "detailed", True,
        )
        self.assertIs(messages, original)

    def test_invalid_internal_values_fail_closed(self):
        with self.assertRaises(KeyError):
            ChatService._apply_presentation_preferences([], "raw prompt", "short", False)

