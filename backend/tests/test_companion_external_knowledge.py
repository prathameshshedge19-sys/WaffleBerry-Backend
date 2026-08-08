"""Controlled Companion external-knowledge routing tests."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.memory import MemoryType
from app.schemas.memory import ApprovedMemorySearchResponse, RankedApprovedMemoryItem
from app.services.ai.ai_service import AIService
from app.services.ai.context_builder import ContextBuilder
from app.services.ai.exceptions import AIProviderError
from app.services.ai.external_knowledge import ExternalKnowledgeClassifier
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.provider import AIMessage, AIProvider
from app.services.chat_service import ChatService


class RecordingProvider(AIProvider):
    def __init__(self):
        self.calls = []

    async def generate_response(
        self,
        messages,
        *,
        structured_response_schema=None,
        external_knowledge_mode=None,
    ):
        self.calls.append(external_knowledge_mode)
        return "grounded answer"

    async def stream_response(self, messages, *, external_knowledge_mode=None):
        self.calls.append(external_knowledge_mode)
        yield "grounded answer"


class FailingWebAI:
    def __init__(self):
        self.modes = []

    async def generate_response(self, messages, *, external_knowledge_mode=None):
        self.modes.append(external_knowledge_mode)
        if external_knowledge_mode == "web_search":
            raise AIProviderError("tool failed")
        return "I was born in Dadar; current details could not be verified."

    async def stream_response(self, messages, *, external_knowledge_mode=None):
        self.modes.append(external_knowledge_mode)
        if external_knowledge_mode == "web_search":
            raise AIProviderError("tool failed")
        yield "memory fallback"


class CapturingWebAI:
    def __init__(self):
        self.calls = []

    async def generate_response(self, messages, *, external_knowledge_mode=None):
        self.calls.append((list(messages), external_knowledge_mode))
        if external_knowledge_mode == "web_search":
            return "Public Dadar history. https://example.test/dadar"
        return "Mixed synthesis"

    async def stream_response(self, messages, *, external_knowledge_mode=None):
        self.calls.append((list(messages), external_knowledge_mode))
        yield "Mixed synthesis"


class FakeRetrieval:
    def __init__(self, memories=()):
        self.memories = list(memories)

    def search_approved(self, db, *, user_id, legacy_id, query):
        response = ApprovedMemorySearchResponse(
            legacy_id=legacy_id,
            matched_memory_count=len(self.memories),
            memories=self.memories,
        )
        response._approved_memory_count = len(self.memories)
        return response


def memory():
    now = datetime(2026, 8, 4, tzinfo=timezone.utc)
    return RankedApprovedMemoryItem(
        memory_id=7,
        memory_type=MemoryType.ATOMIC,
        category="personal_detail",
        title="Birthplace",
        summary="I was born in Dadar.",
        importance=5,
        created_at=now,
        updated_at=now,
        relevance_score=1.0,
        matched_terms=["born", "dadar"],
    )


def fake_db():
    db = MagicMock()
    limited = db.query.return_value.filter.return_value.order_by.return_value.limit.return_value
    limited.all.return_value = []
    return db


def conversation():
    return SimpleNamespace(
        conversation_id=3,
        user_id=11,
        legacy_id=12,
        legacy=SimpleNamespace(
            owner_user_id=11,
            display_name="Mom",
            relationship="Mother",
        ),
    )


class ExternalKnowledgeClassificationTests(unittest.TestCase):
    def assert_plan(self, query, mode, web):
        plan = ExternalKnowledgeClassifier.classify(query)
        self.assertEqual(plan.query_mode, mode)
        self.assertEqual(plan.web_search_requested, web)

    def test_autobiographical_questions_do_not_request_web(self):
        for query in (
            "Where were you born?",
            "Who was your brother?",
            "Tell me about our family.",
            "Tell me about school.",
        ):
            with self.subTest(query=query):
                self.assert_plan(query, "autobiographical_memory", False)

    def test_stable_place_descriptions_use_model_without_web(self):
        for query in (
            "How is Dombivli?",
            "How's Dombivli?",
            "How’s Dombivli?",
            "Hows Mumbai?",
            "How was Dombivli in the 1990s?",
            "What is Dombivli like?",
            "What's Mumbai like?",
            "What’s Dombivli like?",
            "Whats Dadar like?",
            "What is Devrukh like?",
            "What kind of place is Dombivli?",
            "Tell me generally about Dombivli.",
            "Tell me about Mumbai.",
            "What is Dadar like?",
        ):
            with self.subTest(query=query):
                self.assert_plan(query, "general_world_knowledge", False)

    def test_ambiguous_place_request_uses_stable_model_knowledge(self):
        self.assert_plan(
            "Tell me about Dombivli.", "general_world_knowledge", False
        )

    def test_stable_public_place_details_do_not_require_web(self):
        self.assert_plan(
            "What is the history of Devrukh?",
            "general_world_knowledge",
            False,
        )
        self.assert_plan(
            "What monuments are near Dadar?",
            "general_world_knowledge",
            False,
        )

    def test_stable_general_knowledge_can_use_model_without_web(self):
        self.assert_plan(
            "What is a Pomeranian?", "general_world_knowledge", False
        )

    def test_mixed_memory_and_public_context_requests_web(self):
        self.assert_plan(
            "I lived in Dombivli; what is it like?",
            "mixed_memory_and_world",
            False,
        )
        self.assert_plan(
            "You were born in Dadar; tell me about Dadar around 1977.",
            "mixed_memory_and_world",
            True,
        )
        for query in (
            "You lived there — how is Dombivli now?",
            "You were born in Dadar; how is it today?",
            "You studied in Pune; what is Pune like now?",
            "Your family lived in Mumbai; how has Mumbai changed?",
            "You grew up there; what is the place like nowadays?",
        ):
            with self.subTest(query=query):
                self.assert_plan(query, "mixed_memory_and_world", True)

    def test_current_place_questions_request_web(self):
        for query in (
            "How is Dombivli today?",
            "How is Dombivli now?",
            "What is Mumbai like today?",
            "What changed in Dombivli recently?",
            "What is the current population of Dombivli?",
            "You lived there — how is Dombivli now?",
        ):
            with self.subTest(query=query):
                plan = ExternalKnowledgeClassifier.classify(query)
                self.assertIn(
                    plan.query_mode,
                    {"general_world_knowledge", "mixed_memory_and_world"},
                )
                self.assertTrue(plan.web_search_requested)

    def test_personal_place_questions_remain_memory_only(self):
        for query in (
            "What do you remember about Dombivli?",
            "Where did you live in Dombivli?",
            "What happened in Dombivli?",
            "Tell me about your life in Dombivli.",
            "What was your life like in Dombivli?",
            "What was your experience like in Mumbai?",
            "How was your life in Dadar?",
            "How did you feel living in Dombivli?",
        ):
            with self.subTest(query=query):
                self.assert_plan(query, "autobiographical_memory", False)

    def test_unsupported_place_feeling_is_not_general_knowledge(self):
        self.assert_plan(
            "Did you enjoy Dombivli?", "unsupported_personal_inference", False
        )

    def test_unsupported_personal_inference_never_requests_web(self):
        for query in (
            "Did you visit Shivaji Park?",
            "Did you travel by local train every day?",
            "Was Dadar crowded when you were young?",
        ):
            with self.subTest(query=query):
                self.assert_plan(
                    query, "unsupported_personal_inference", False
                )


class ExternalKnowledgeProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_service_omits_mode_for_ordinary_requests(self):
        provider = RecordingProvider()
        service = AIService(provider)
        messages = [AIMessage(role="user", content="Hello")]
        await service.generate_response(messages)
        self.assertEqual(provider.calls, [None])

    async def test_openai_receives_web_tool_only_when_requested(self):
        calls = []

        async def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_text="Public answer")

        provider = object.__new__(OpenAIProvider)
        provider._settings = SimpleNamespace(ai_model="test-model")
        provider._client = SimpleNamespace(
            responses=SimpleNamespace(create=create)
        )
        messages = [AIMessage(role="user", content="Tell me about Dadar")]
        await provider.generate_response(messages)
        await provider.generate_response(
            messages, external_knowledge_mode="web_search"
        )
        self.assertNotIn("tools", calls[0])
        self.assertEqual(calls[1]["tools"], [{"type": "web_search"}])

    async def test_web_url_annotations_become_user_visible_links(self):
        annotation = SimpleNamespace(
            type="url_citation",
            title="Dadar history",
            url="https://example.test/history",
        )

        async def create(**kwargs):
            return SimpleNamespace(
                output_text="Public answer",
                output=[SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(annotations=[annotation])],
                )],
            )

        provider = object.__new__(OpenAIProvider)
        provider._settings = SimpleNamespace(ai_model="test-model")
        provider._client = SimpleNamespace(
            responses=SimpleNamespace(create=create)
        )
        answer = await provider.generate_response(
            [AIMessage(role="user", content="Tell me about Dadar")],
            external_knowledge_mode="web_search",
        )
        self.assertIn(
            "[Dadar history](https://example.test/history)", answer
        )

    async def test_web_failure_retries_without_losing_memory_answer(self):
        ai = FailingWebAI()
        service = ChatService(
            ai,
            ContextBuilder(8),
            memory_retrieval=FakeRetrieval([memory()]),
        )
        answer = await service.generate_response(
            fake_db(),
            conversation(),
            "You were born in Dadar; tell me about Dadar around 1977.",
        )
        self.assertIn("born in Dadar", answer)
        self.assertEqual(ai.modes, ["web_search", None])

    async def test_stable_place_description_uses_one_tool_free_call(self):
        ai = CapturingWebAI()
        service = ChatService(
            ai,
            ContextBuilder(8),
            memory_retrieval=FakeRetrieval([]),
        )
        answer = await service.generate_response(
            fake_db(), conversation(), "How is Dombivli?"
        )
        self.assertEqual(answer, "Mixed synthesis")
        self.assertEqual(len(ai.calls), 1)
        messages, mode = ai.calls[0]
        self.assertIsNone(mode)
        normalized_prompt = " ".join(
            " ".join(message.content for message in messages).split()
        )
        self.assertIn(
            "begin the answer with a factual description",
            normalized_prompt,
        )
        self.assertIn("Answer the public place question", normalized_prompt)
        self.assertIn("may come from model knowledge", normalized_prompt)
        self.assertNotIn("I don't remember", answer)

    async def test_stable_mixed_place_question_keeps_memory_boundary(self):
        ai = CapturingWebAI()
        place_memory = memory().model_copy(
            update={"summary": "I lived in Dombivli with my family."}
        )
        service = ChatService(
            ai,
            ContextBuilder(8),
            memory_retrieval=FakeRetrieval([place_memory]),
        )
        await service.generate_response(
            fake_db(),
            conversation(),
            "I lived in Dombivli; what is it like?",
        )
        self.assertEqual(len(ai.calls), 1)
        messages, mode = ai.calls[0]
        synthesis_text = " ".join(
            " ".join(message.content for message in messages).split()
        )
        self.assertIsNone(mode)
        self.assertIn("I lived in Dombivli with my family", synthesis_text)
        self.assertIn("clearly distinguish", synthesis_text)
        self.assertIn(
            "supporting context, not as the primary answer", synthesis_text
        )
        self.assertIn("never phrase it as \"I remember\"", synthesis_text)

    async def test_web_lookup_receives_only_sanitized_public_topic(self):
        ai = CapturingWebAI()
        private_memory = memory().model_copy(
            update={"summary": "Private family details not for web search."}
        )
        service = ChatService(
            ai,
            ContextBuilder(8),
            memory_retrieval=FakeRetrieval([private_memory]),
        )
        await service.generate_response(
            fake_db(),
            conversation(),
            "You were born in Dadar; tell me about Dadar around 1977.",
        )
        lookup_messages, mode = ai.calls[0]
        lookup_text = " ".join(message.content for message in lookup_messages)
        self.assertEqual(mode, "web_search")
        self.assertIn("tell me about Dadar around 1977", lookup_text)
        self.assertNotIn("Private family details", lookup_text)
        self.assertNotIn("You were born", lookup_text)
        synthesis_messages, synthesis_mode = ai.calls[1]
        synthesis_text = " ".join(
            message.content for message in synthesis_messages
        )
        self.assertIsNone(synthesis_mode)
        self.assertIn("Private family details", synthesis_text)
        self.assertIn("Public Dadar history", synthesis_text)

    def test_prompt_separates_external_facts_from_personal_recollection(self):
        service = ChatService(
            FailingWebAI(),
            ContextBuilder(8),
            memory_retrieval=FakeRetrieval([memory()]),
        )
        prompt = service.prepare_ai_input(
            fake_db(),
            conversation(),
            "Did you visit Shivaji Park?",
        )[0].content
        self.assertIn("Personal first-person claims", prompt)
        self.assertIn("never phrase it as \"I remember\"", prompt)
        self.assertIn("whether you personally experienced it", prompt)
        self.assertIn("never include private names", prompt)

    def test_general_place_prompt_allows_concise_stable_public_facts(self):
        service = ChatService(
            FailingWebAI(),
            ContextBuilder(8),
            memory_retrieval=FakeRetrieval([]),
        )
        for query in (
            "How's Dombivli?",
            "What's Dombivli like?",
            "Tell me about Mumbai.",
            "Tell me about Dombivli.",
        ):
            with self.subTest(query=query):
                messages = service.prepare_ai_input(
                    fake_db(), conversation(), query
                )
                prompt = messages[0].content
                normalized_prompt = " ".join(prompt.split())
                self.assertIn(
                    "begin the answer with a factual description",
                    normalized_prompt,
                )
                self.assertIn(
                    "Answer the public place question", normalized_prompt
                )
                self.assertIn(
                    "supporting context, not as the primary answer",
                    normalized_prompt,
                )
                self.assertIn(
                    "Keep the default answer to 2-4 sentences",
                    normalized_prompt,
                )
                self.assertIn(
                    "Never end a general-place answer", normalized_prompt
                )
                self.assertIn("I don't remember enough", normalized_prompt)
                self.assertIn("I wish I remembered more", normalized_prompt)
                self.assertIn(
                    "only when the user explicitly asks", normalized_prompt
                )
                self.assertIn(
                    "never phrase it as \"I remember\"", normalized_prompt
                )
                self.assertNotIn("WEB LOOKUP STATUS", normalized_prompt)


if __name__ == "__main__":
    unittest.main()
