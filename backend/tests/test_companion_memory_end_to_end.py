"""Database-backed production-path regression for Companion memory grounding."""

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.crud.memory import LegacyCRUD, StorySessionCRUD
from app.crud.user import ConversationCRUD
from app.db import Base
from app.models.memory import Memory, MemoryReviewStatus, StoryMessageRole
from app.models.user import User
from app.schemas.memory import LegacyCreate, StoryMessageCreate, StorySessionCreate
from app.services.ai.ai_service import AIService
from app.services.ai.context_builder import ContextBuilder
from app.services.ai.provider import AIProvider
from app.services.chat_service import ChatService
from app.services.memory.background_extraction import StoryExtractionService
from app.services.memory.extractor import MemoryExtractionService
from app.services.memory.grounding import CompanionMemoryGrounding
from app.services.memory.retrieval import MemoryRetrievalService
from app.services.memory.review import MemoryReviewService
from app.services.memory.storage_pipeline import MemoryStoragePipeline


class CapturingProvider(AIProvider):
    """Replace only the external provider while retaining app orchestration."""

    def __init__(self, extraction_response):
        self.extraction_response = extraction_response
        self.calls = []

    async def generate_response(self, messages):
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            return json.dumps(self.extraction_response)
        return "She worked at Tata Motors in Pune."


class CapturingGrounding(CompanionMemoryGrounding):
    def __init__(self):
        super().__init__()
        self.last_context = None

    def select(self, memories):
        selection = super().select(memories)
        self.last_context = selection.context
        return selection


class CompanionMemoryEndToEndTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    async def test_extracted_approved_memory_reaches_final_provider_prompt(self):
        owner = User(full_name="Owner", email="owner@e2e.test", password_hash="hash")
        outsider = User(full_name="Outsider", email="outsider@e2e.test", password_hash="hash")
        self.db.add_all([owner, outsider])
        self.db.commit()
        self.db.refresh(owner)
        self.db.refresh(outsider)

        legacy = LegacyCRUD.create_legacy(
            self.db,
            owner.user_id,
            LegacyCreate(display_name="Asha", relationship="Mother"),
        )
        other_legacy = LegacyCRUD.create_legacy(
            self.db,
            outsider.user_id,
            LegacyCreate(display_name="Other", relationship="Relative"),
        )
        story = StorySessionCRUD.create_story_session(
            self.db,
            legacy.legacy_id,
            owner.user_id,
            StorySessionCreate(chapter_key="career", title="Career"),
        )
        source = StorySessionCRUD.append_story_message(
            self.db,
            story.story_session_id,
            legacy.legacy_id,
            StoryMessageCreate(
                role=StoryMessageRole.USER,
                content="She worked at Tata Motors in Pune for twenty-five years.",
            ),
        )
        _, run = StoryExtractionService().complete(
            self.db,
            user_id=owner.user_id,
            legacy_id=legacy.legacy_id,
            story_session_id=story.story_session_id,
        )

        provider = CapturingProvider({
            "memories": [{
                "memory_type": "atomic",
                "category": "achievement",
                "title": "Work at Tata Motors",
                "summary": "She worked at Tata Motors in Pune for twenty-five years.",
                "details": {"places": [], "temporal_references": []},
                "emotional_significance": None,
                "importance": 5,
                "extraction_confidence": 0.99,
                "uncertainty_note": None,
                "participants": [{
                    "name": "Asha",
                    "relationship": "mother",
                    "role": "subject",
                }],
                "tags": ["career", "Pune"],
                "evidence": [{
                    "source_message_id": source.story_message_id,
                    "excerpt": "worked at Tata Motors in Pune for twenty-five years",
                }],
            }]
        })
        ai = AIService(provider)
        report = await MemoryStoragePipeline(
            MemoryExtractionService(ai)
        ).process_story_session(
            self.db,
            user_id=owner.user_id,
            legacy_id=legacy.legacy_id,
            story_session_id=story.story_session_id,
            metadata={"message_boundary": run.message_boundary},
        )
        self.assertEqual(report.memories_created, 1)
        memory = self.db.get(Memory, report.created_memory_ids[0])
        self.assertEqual(memory.legacy_id, legacy.legacy_id)
        self.assertEqual(memory.review_status, MemoryReviewStatus.CANDIDATE)

        reviewed = MemoryReviewService().approve(
            self.db,
            user_id=owner.user_id,
            legacy_id=legacy.legacy_id,
            memory_id=memory.memory_id,
            expected_updated_at=memory.updated_at,
        )
        self.db.expire_all()
        persisted = self.db.get(Memory, memory.memory_id)
        self.assertEqual(reviewed.review_status, MemoryReviewStatus.APPROVED)
        self.assertEqual(persisted.review_status, MemoryReviewStatus.APPROVED)

        other = Memory(
            legacy_id=other_legacy.legacy_id,
            memory_type="atomic",
            category="achievement",
            title="Secret unrelated employer",
            summary="The other Legacy worked at Contoso in Delhi.",
            review_status=MemoryReviewStatus.APPROVED,
        )
        self.db.add(other)
        self.db.commit()

        conversation = ConversationCRUD.create_conversation(
            self.db,
            owner.user_id,
            "Career question",
            legacy.legacy_id,
        )
        self.assertEqual(conversation.legacy_id, legacy.legacy_id)

        retrieval = MemoryRetrievalService().search_approved(
            self.db,
            user_id=owner.user_id,
            legacy_id=legacy.legacy_id,
            query="Where did she work?",
        )
        self.assertEqual([item.memory_id for item in retrieval.memories], [memory.memory_id])

        grounding = CapturingGrounding()
        with self.assertLogs("app.services.chat_service", level="INFO") as logs:
            generation = await ChatService(
                ai,
                ContextBuilder(12),
                MemoryRetrievalService(),
                grounding,
            ).generate_response_with_provenance(
                self.db,
                conversation,
                "Where did she work?",
            )

        self.assertEqual(generation.memory_ids, (memory.memory_id,))
        self.assertIn("Tata Motors in Pune", grounding.last_context)
        final_prompt = provider.calls[-1][0].content
        self.assertIn("Tata Motors in Pune", final_prompt)
        self.assertNotIn("Contoso", final_prompt)
        self.assertNotIn("Delhi", final_prompt)
        self.assertEqual(generation.content, "She worked at Tata Motors in Pune.")
        events = [json.loads(record.getMessage()) for record in logs.records]
        retrieval_log = next(
            event for event in events
            if event["event"] == "companion_memory_retrieval"
        )
        provider_log = next(
            event for event in events
            if event["event"] == "companion_provider_call"
        )
        self.assertEqual(retrieval_log["approved_candidate_count"], 1)
        self.assertEqual(retrieval_log["retrieved_memory_count"], 1)
        self.assertEqual(retrieval_log["selected_memory_ids"], [memory.memory_id])
        self.assertTrue(retrieval_log["grounding_context_created"])
        self.assertTrue(provider_log["provider_call_attempted"])
        self.assertEqual(retrieval_log["request_id"], provider_log["request_id"])
        for record in logs.records:
            self.assertNotIn("Tata Motors", record.getMessage())
            self.assertNotIn("Pune", record.getMessage())


if __name__ == "__main__":
    unittest.main()
