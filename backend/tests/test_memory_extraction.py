"""Unit tests for provider-neutral AI memory extraction."""

import json
import logging
import unittest

from app.models.memory import (
    Legacy,
    MemoryType,
    StoryMessage,
    StoryMessageRole,
    StorySession,
)
from app.models.user import Conversation, Message, MessageRole
from app.services.memory.exceptions import (
    MemoryExtractionResponseError,
    MemoryExtractionSourceError,
)
from app.services.memory.extractor import MemoryExtractionService


class FakeAIService:
    """Return deterministic text without creating a provider client."""

    def __init__(self, response_text: str):
        self.response_text = response_text
        self.messages = None
        self.call_count = 0
        self.structured_response_schema = None

    async def generate_response(
        self,
        messages,
        *,
        structured_response_schema=None,
    ):
        self.call_count += 1
        self.messages = messages
        self.structured_response_schema = structured_response_schema
        return self.response_text


def story_fixture():
    legacy = Legacy(
        legacy_id=42,
        owner_user_id=7,
        display_name="Mom",
        relationship="Mother",
    )
    session = StorySession(
        story_session_id=310,
        legacy_id=42,
        chapter_key="Childhood",
        created_by_user_id=7,
    )
    messages = [
        StoryMessage(
            story_message_id=880,
            story_session_id=310,
            role=StoryMessageRole.ASSISTANT,
            content="What do you remember about home?",
            sequence=1,
        ),
        StoryMessage(
            story_message_id=881,
            story_session_id=310,
            role=StoryMessageRole.USER,
            content=(
                "I was born in Pune in 1968, and our family celebrated "
                "Diwali at Grandma's house every year."
            ),
            sequence=2,
        ),
    ]
    return legacy, session, messages


def atomic_output(**overrides):
    memory = {
        "memory_type": "atomic",
        "category": "personal_detail",
        "title": "Birthplace and year",
        "summary": "Mom was born in Pune in 1968.",
        "details": {
            "places": [
                {
                    "name": "Pune",
                    "region": "Maharashtra",
                    "country": "India",
                    "certainty": "stated",
                }
            ],
            "temporal_references": [
                {
                    "text": "1968",
                    "start_date": "1968-01-01",
                    "end_date": "1968-12-31",
                    "precision": "year",
                    "is_approximate": False,
                    "certainty": "stated",
                }
            ],
        },
        "emotional_significance": None,
        "importance": 5,
        "extraction_confidence": 0.96,
        "uncertainty_note": None,
        "participants": [
            {
                "name": "Mom",
                "relationship": "mother",
                "role": "subject",
            }
        ],
        "tags": ["early-life"],
        "evidence": [
            {
                "source_message_id": 881,
                "excerpt": "I was born in Pune in 1968",
            }
        ],
    }
    memory.update(overrides)
    return memory


class MemoryExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_compound_statement_produces_atomic_semantic_memories(self):
        legacy, session, messages = story_fixture()
        messages[1].content = (
            "I am a tuition teacher and taught my son until grade 10."
        )
        profession = atomic_output(
            title="Profession as a tuition teacher",
            summary="Mom was a tuition teacher.",
            details={
                "places": [],
                "temporal_references": [],
                "semantic_attributes": {
                    "profession": "tuition teacher",
                    "occupation_category": "education",
                    "taught_relationship": None,
                    "education_level": None,
                    "birthplace": None,
                },
            },
            evidence=[{
                "source_message_id": 881,
                "excerpt": "I am a tuition teacher",
            }],
        )
        teaching = atomic_output(
            category="relationship",
            title="Teaching her son",
            summary="Mom taught her son until grade 10.",
            details={
                "places": [],
                "temporal_references": [],
                "semantic_attributes": {
                    "profession": None,
                    "occupation_category": None,
                    "taught_relationship": "son",
                    "education_level": "grade 10",
                    "birthplace": None,
                },
            },
            evidence=[{
                "source_message_id": 881,
                "excerpt": "taught my son until grade 10",
            }],
        )
        ai_service = FakeAIService(
            json.dumps({"memories": [profession, teaching]})
        )
        service = MemoryExtractionService(ai_service)

        memories = await service.extract_story_session(
            legacy, session, messages
        )

        self.assertEqual(len(memories), 2)
        self.assertTrue(
            all(item.memory_type == MemoryType.ATOMIC for item in memories)
        )
        self.assertIn(
            "multiple atomic memories", ai_service.messages[0].content
        )
        attributes = memories[0].details.semantic_attributes
        self.assertEqual(attributes.profession, "tuition teacher")
        self.assertEqual(attributes.occupation_category, "education")
        self.assertEqual(
            memories[0].provenance[0].excerpt,
            "I am a tuition teacher",
        )
        self.assertEqual(
            memories[1].details.semantic_attributes.taught_relationship,
            "son",
        )
        self.assertEqual(
            memories[1].details.semantic_attributes.education_level,
            "grade 10",
        )

    async def test_unsupported_optional_semantics_are_nulled(self):
        legacy, session, messages = story_fixture()
        messages[1].content = "I taught my son until grade 10."
        unsupported = atomic_output(
            summary="Mom taught her son until grade 10.",
            details={
                "places": [],
                "temporal_references": [],
                "semantic_attributes": {
                    "profession": "teacher",
                    "occupation_category": "education",
                    "taught_relationship": "son",
                    "education_level": "grade 10",
                    "birthplace": None,
                },
            },
            evidence=[{
                "source_message_id": 881,
                "excerpt": "taught my son until grade 10",
            }],
        )
        service = MemoryExtractionService(
            FakeAIService(json.dumps({"memories": [unsupported]}))
        )

        candidates = await service.extract_story_session(
            legacy, session, messages
        )
        attributes = candidates[0].details.semantic_attributes
        self.assertIsNone(attributes.profession)
        self.assertIsNone(attributes.occupation_category)
        self.assertEqual(attributes.taught_relationship, "son")
        self.assertEqual(attributes.education_level, "grade 10")

    async def test_invalid_evidence_does_not_block_valid_candidate(self):
        legacy, session, messages = story_fixture()
        invalid = atomic_output(evidence=[{
            "source_message_id": 880,
            "excerpt": "What do you remember about home?",
        }])
        valid = atomic_output(title="Valid source-backed memory")
        service = MemoryExtractionService(FakeAIService(json.dumps({
            "memories": [invalid, valid],
        })))

        with self.assertLogs(
            "app.services.memory.extractor", logging.WARNING
        ) as captured:
            candidates = await service.extract_story_session(
                legacy, session, messages
            )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].title, "Valid source-backed memory")
        self.assertEqual(
            captured.records[0].exception_type,
            "MemoryExtractionResponseError",
        )

    async def test_zero_memories_is_a_valid_result(self):
        legacy, session, messages = story_fixture()
        ai_service = FakeAIService('{"memories":[]}')
        service = MemoryExtractionService(ai_service)

        result = await service.extract_story_session(
            legacy,
            session,
            messages,
        )

        self.assertEqual(result, [])
        self.assertEqual(ai_service.call_count, 1)
        self.assertEqual(
            ai_service.structured_response_schema["required"],
            ["memories"],
        )
        self.assertFalse(
            ai_service.structured_response_schema["additionalProperties"]
        )
        self._assert_strict_objects(ai_service.structured_response_schema)

    def _assert_strict_objects(self, node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                self.assertIs(node.get("additionalProperties"), False)
                self.assertEqual(
                    set(node.get("required", [])),
                    set(node.get("properties", {})),
                )
            for value in node.values():
                self._assert_strict_objects(value)
        elif isinstance(node, list):
            for value in node:
                self._assert_strict_objects(value)

    async def test_multiple_candidates_reuse_existing_schema(self):
        legacy, session, messages = story_fixture()
        narrative = {
            "memory_type": "narrative",
            "category": "tradition",
            "title": "Diwali at Grandma's house",
            "summary": (
                "Mom's family celebrated Diwali at Grandma's house "
                "every year."
            ),
            "details": {
                "places": [],
                "temporal_references": [],
            },
            "emotional_significance": (
                "It was a recurring family gathering."
            ),
            "importance": 5,
            "extraction_confidence": 0.93,
            "uncertainty_note": None,
            "participants": [
                {
                    "name": "Grandma",
                    "relationship": "grandmother",
                    "role": "mentioned_person",
                }
            ],
            "tags": ["Diwali", "family-tradition"],
            "evidence": [
                {
                    "source_message_id": 881,
                    "excerpt": (
                        "our family celebrated Diwali at Grandma's house "
                        "every year"
                    ),
                }
            ],
        }
        ai_service = FakeAIService(
            json.dumps(
                {"memories": [atomic_output(), narrative]},
                ensure_ascii=False,
            )
        )
        service = MemoryExtractionService(ai_service)

        candidates = await service.extract_story_session(
            legacy,
            session,
            messages,
        )

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].memory_type, MemoryType.ATOMIC)
        self.assertEqual(candidates[1].memory_type, MemoryType.NARRATIVE)
        self.assertEqual(candidates[0].importance, 5)
        self.assertEqual(
            candidates[1].provenance[0].story_session_id,
            session.story_session_id,
        )
        self.assertEqual(
            candidates[1].provenance[0].story_message_id,
            881,
        )
        self.assertEqual(
            candidates[1].provenance[0].chapter,
            "Childhood",
        )
        self.assertFalse(
            hasattr(candidates[0], "review_status")
        )

    async def test_uncertainty_and_approximate_dates_are_preserved(self):
        legacy, session, messages = story_fixture()
        messages[1].content = "I think we moved around 1985."
        uncertain = atomic_output(
            category="life_event",
            title="Approximate move",
            summary="Mom may have moved around 1985.",
            details={
                "places": [],
                "temporal_references": [
                    {
                        "text": "around 1985",
                        "start_date": None,
                        "end_date": None,
                        "precision": "year",
                        "is_approximate": True,
                        "certainty": "uncertain",
                    }
                ],
            },
            uncertainty_note="The speaker was unsure of the exact year.",
            extraction_confidence=0.81,
            evidence=[
                {
                    "source_message_id": 881,
                    "excerpt": "I think we moved around 1985",
                }
            ],
        )
        ai_service = FakeAIService(
            json.dumps({"memories": [uncertain]})
        )

        candidates = await MemoryExtractionService(
            ai_service
        ).extract_story_session(legacy, session, messages)

        temporal = candidates[0].details.temporal_references[0]
        self.assertTrue(temporal.is_approximate)
        self.assertEqual(temporal.certainty, "uncertain")
        self.assertEqual(
            candidates[0].uncertainty_note,
            "The speaker was unsure of the exact year.",
        )

    async def test_conversation_builds_conversation_provenance(self):
        legacy = Legacy(
            legacy_id=42,
            owner_user_id=7,
            display_name="Mom",
            relationship="Mother",
        )
        conversation = Conversation(
            conversation_id=88,
            user_id=7,
            legacy_id=42,
            title="Family traditions",
        )
        messages = [
            Message(
                message_id=1204,
                conversation_id=88,
                role=MessageRole.USER,
                content="We always made sweets together for Diwali.",
            ),
            Message(
                message_id=1205,
                conversation_id=88,
                role=MessageRole.ASSISTANT,
                content="That sounds meaningful.",
            ),
        ]
        extracted = atomic_output(
            category="tradition",
            title="Making Diwali sweets",
            summary="Mom's family made sweets together for Diwali.",
            evidence=[
                {
                    "source_message_id": 1204,
                    "excerpt": (
                        "We always made sweets together for Diwali"
                    ),
                }
            ],
        )
        service = MemoryExtractionService(
            FakeAIService(json.dumps({"memories": [extracted]}))
        )

        candidates = await service.extract_conversation(
            legacy,
            conversation,
            messages,
        )

        source = candidates[0].provenance[0]
        self.assertEqual(source.source_type, "conversation")
        self.assertEqual(source.conversation_id, 88)
        self.assertEqual(source.message_id, 1204)
        self.assertIsNone(source.story_session_id)
        self.assertIsNone(source.chapter)

    async def test_prompt_separates_extraction_from_chat(self):
        legacy, session, messages = story_fixture()
        ai_service = FakeAIService('{"memories":[]}')

        await MemoryExtractionService(
            ai_service
        ).extract_story_session(legacy, session, messages)

        self.assertEqual(
            [message.role for message in ai_service.messages],
            ["system", "user"],
        )
        prompt = " ".join(
            ai_service.messages[0].content.lower().split()
        )
        self.assertIn("not chatting", prompt)
        self.assertIn("not a conversation summary", prompt)
        self.assertIn("never cite assistant messages", prompt)
        payload = json.loads(ai_service.messages[1].content)
        self.assertEqual(
            payload["legacy_context"]["relationship"],
            "Mother",
        )
        self.assertFalse(
            payload["source"]["messages"][0]["eligible_as_evidence"]
        )
        self.assertTrue(
            payload["source"]["messages"][1]["eligible_as_evidence"]
        )

    async def test_invalid_json_is_translated_to_safe_error(self):
        legacy, session, messages = story_fixture()
        service = MemoryExtractionService(
            FakeAIService("not valid JSON")
        )

        with self.assertRaises(MemoryExtractionResponseError):
            await service.extract_story_session(legacy, session, messages)

    async def test_whole_response_json_fence_is_supported(self):
        legacy, session, messages = story_fixture()
        service = MemoryExtractionService(
            FakeAIService('  ```json\n{"memories":[]}\n```  ')
        )

        self.assertEqual(
            await service.extract_story_session(legacy, session, messages),
            [],
        )

    async def test_invalid_top_level_shapes_are_rejected(self):
        legacy, session, messages = story_fixture()
        for payload in ([], {"result": {"memories": []}}):
            with self.subTest(payload=payload):
                service = MemoryExtractionService(
                    FakeAIService(json.dumps(payload))
                )
                with self.assertRaises(MemoryExtractionResponseError):
                    await service.extract_story_session(
                        legacy, session, messages
                    )

    async def test_extra_fields_are_rejected_at_every_strict_level(self):
        legacy, session, messages = story_fixture()
        payloads = [
            {"memories": [], "extra": True},
            {"memories": [atomic_output(extra=True)]},
            {"memories": [atomic_output(evidence=[{
                "source_message_id": 881,
                "excerpt": "I was born in Pune in 1968",
                "extra": True,
            }])]},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(MemoryExtractionResponseError):
                    await MemoryExtractionService(
                        FakeAIService(json.dumps(payload))
                    ).extract_story_session(legacy, session, messages)

    async def test_missing_evidence_is_rejected(self):
        legacy, session, messages = story_fixture()
        memory = atomic_output()
        del memory["evidence"]
        with self.assertRaises(MemoryExtractionResponseError):
            await MemoryExtractionService(
                FakeAIService(json.dumps({"memories": [memory]}))
            ).extract_story_session(legacy, session, messages)

    async def test_invalid_enums_and_numeric_bounds_are_rejected(self):
        legacy, session, messages = story_fixture()
        invalid_values = [
            {"memory_type": "episode"},
            {"category": "weather"},
            {"importance": 0},
            {"importance": 6},
            {"extraction_confidence": -0.01},
            {"extraction_confidence": 1.01},
        ]
        for overrides in invalid_values:
            with self.subTest(overrides=overrides):
                payload = {"memories": [atomic_output(**overrides)]}
                with self.assertRaises(MemoryExtractionResponseError):
                    await MemoryExtractionService(
                        FakeAIService(json.dumps(payload))
                    ).extract_story_session(legacy, session, messages)

    async def test_diagnostics_exclude_response_and_story_content(self):
        legacy, session, messages = story_fixture()
        secret_story = messages[1].content
        raw_response = '{"memories":[{"summary":"private output"}]}'
        service = MemoryExtractionService(FakeAIService(raw_response))

        with self.assertLogs(
            "app.services.memory.extractor", logging.WARNING
        ) as captured:
            with self.assertRaises(MemoryExtractionResponseError):
                await service.extract_story_session(
                    legacy, session, messages
                )

        diagnostics = " ".join(captured.output)
        record = captured.records[0]
        self.assertNotIn(raw_response, diagnostics)
        self.assertNotIn(secret_story, diagnostics)
        self.assertNotIn("private output", diagnostics)
        self.assertEqual(record.exception_type, "ValidationError")
        self.assertIn("memories.0.memory_type", record.validation_field_paths)
        self.assertTrue(record.validation_error_types)
        self.assertEqual(record.response_character_count, len(raw_response))
        self.assertFalse(record.response_had_code_fence)

    async def test_invalid_candidate_contract_is_rejected(self):
        legacy, session, messages = story_fixture()
        invalid = atomic_output(
            category="weather",
            importance=8,
        )
        service = MemoryExtractionService(
            FakeAIService(json.dumps({"memories": [invalid]}))
        )

        with self.assertRaises(MemoryExtractionResponseError):
            await service.extract_story_session(legacy, session, messages)

    async def test_unknown_or_assistant_evidence_is_rejected(self):
        legacy, session, messages = story_fixture()
        unknown = atomic_output(
            evidence=[
                {
                    "source_message_id": 880,
                    "excerpt": "What do you remember about home?",
                }
            ]
        )
        service = MemoryExtractionService(
            FakeAIService(json.dumps({"memories": [unknown]}))
        )

        self.assertEqual(
            await service.extract_story_session(legacy, session, messages),
            [],
        )

    async def test_non_verbatim_evidence_is_rejected(self):
        legacy, session, messages = story_fixture()
        paraphrased = atomic_output(
            evidence=[
                {
                    "source_message_id": 881,
                    "excerpt": "Mom said that she was born in Pune.",
                }
            ]
        )
        service = MemoryExtractionService(
            FakeAIService(json.dumps({"memories": [paraphrased]}))
        )

        self.assertEqual(
            await service.extract_story_session(legacy, session, messages),
            [],
        )

    async def test_cross_legacy_source_is_rejected_before_ai_call(self):
        legacy, session, messages = story_fixture()
        session.legacy_id = 99
        ai_service = FakeAIService('{"memories":[]}')

        with self.assertRaises(MemoryExtractionSourceError):
            await MemoryExtractionService(
                ai_service
            ).extract_story_session(legacy, session, messages)

        self.assertEqual(ai_service.call_count, 0)

    async def test_no_user_messages_skips_provider(self):
        legacy, session, messages = story_fixture()
        ai_service = FakeAIService('{"memories":[]}')

        result = await MemoryExtractionService(
            ai_service
        ).extract_story_session(legacy, session, messages[:1])

        self.assertEqual(result, [])
        self.assertEqual(ai_service.call_count, 0)


if __name__ == "__main__":
    unittest.main()
