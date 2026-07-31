"""Phase 6.7.6 end-to-end retrieval pipeline validation."""

import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.crud.user import MessageCRUD
from app.db import Base, _enable_sqlite_foreign_keys
from app.models.memory import (
    CompanionMemoryProvenance,
    Legacy,
    Memory,
    MemoryReviewStatus,
    MemoryType,
)
from app.models.user import Conversation, Message, User
from app.services.ai.context_builder import ContextBuilder
from app.services.chat_service import ChatService
from app.services.memory.grounding import (
    CompanionMemoryGrounding,
    MemoryGroundingBudget,
)
from app.services.memory.retrieval import MemoryRetrievalService


class CapturingAI:
    def __init__(self):
        self.generated_messages = None
        self.streamed_messages = None

    async def generate_response(self, messages):
        self.generated_messages = messages
        return "You shared that jasmine tea was a favorite."

    async def stream_response(self, messages):
        self.streamed_messages = messages
        yield "You shared that "
        yield "jasmine tea was a favorite."


class MemoryRetrievalPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.owner = User(
            full_name="Owner",
            email="pipeline-owner@example.test",
            password_hash="hash",
        )
        self.other = User(
            full_name="Other",
            email="pipeline-other@example.test",
            password_hash="hash",
        )
        self.db.add_all([self.owner, self.other])
        self.db.flush()
        self.legacy = Legacy(
            owner_user_id=self.owner.user_id,
            display_name="Mom",
            relationship="Mother",
        )
        self.other_legacy = Legacy(
            owner_user_id=self.other.user_id,
            display_name="Other",
            relationship="Relative",
        )
        self.db.add_all([self.legacy, self.other_legacy])
        self.db.flush()
        self.conversation = Conversation(
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            title="Companion",
        )
        self.db.add(self.conversation)
        self.db.commit()
        self.ai = CapturingAI()
        self.service = ChatService(
            self.ai,
            ContextBuilder(12),
            MemoryRetrievalService(),
            CompanionMemoryGrounding(
                MemoryGroundingBudget(
                    max_memories=2,
                    max_estimated_tokens=1500,
                    max_characters=6000,
                )
            ),
        )

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def add_memory(
        self,
        title,
        summary,
        *,
        legacy=None,
        status=MemoryReviewStatus.APPROVED,
        importance=3,
    ):
        item = Memory(
            legacy_id=(legacy or self.legacy).legacy_id,
            memory_type=MemoryType.ATOMIC,
            category="preference",
            title=title,
            summary=summary,
            importance=importance,
            review_status=status,
        )
        self.db.add(item)
        self.db.commit()
        return item

    async def test_nonstreaming_pipeline_is_grounded_bounded_and_atomic(self):
        direct = self.add_memory(
            "Jasmine tea",
            "Mom loved jasmine tea every morning.",
            importance=2,
        )
        second = self.add_memory(
            "Tea garden",
            "Jasmine grew beside the house.",
            importance=5,
        )
        self.add_memory("Unrelated", "She enjoyed sailing.", importance=5)
        self.add_memory(
            "Candidate jasmine",
            "Candidate jasmine detail.",
            status=MemoryReviewStatus.CANDIDATE,
        )
        self.add_memory(
            "Other jasmine",
            "Another user's jasmine memory.",
            legacy=self.other_legacy,
        )

        generated = await self.service.generate_response_with_provenance(
            self.db,
            self.conversation,
            "Tell me about jasmine tea",
        )
        prompt = self.ai.generated_messages[0].content
        self.assertEqual(generated.memory_ids, (direct.memory_id, second.memory_id))
        self.assertIn("UNTRUSTED DATA", prompt)
        self.assertIn("Mom loved jasmine tea", prompt)
        self.assertNotIn("sailing", prompt)
        self.assertNotIn("Candidate jasmine detail", prompt)
        self.assertNotIn("Another user's jasmine", prompt)
        self.assertNotIn("relevance_score", prompt)

        _, assistant, _ = MessageCRUD.create_message_pair(
            self.db,
            self.conversation,
            "Tell me about jasmine tea",
            generated.content,
            grounded_memory_ids=generated.memory_ids,
            memories_retrieved_at=generated.retrieved_at,
        )
        provenance = (
            self.db.query(CompanionMemoryProvenance)
            .order_by(CompanionMemoryProvenance.retrieval_order)
            .all()
        )
        self.assertEqual(
            [(row.assistant_message_id, row.memory_id) for row in provenance],
            [
                (assistant.message_id, direct.memory_id),
                (assistant.message_id, second.memory_id),
            ],
        )

    async def test_streaming_uses_same_context_and_persists_after_completion(self):
        grounded = self.add_memory(
            "Jasmine tea",
            "Mom loved jasmine tea every morning.",
        )
        generated = await self.service.generate_response_with_provenance(
            self.db, self.conversation, "jasmine tea"
        )
        plan = self.service.stream_response_with_provenance(
            self.db, self.conversation, "jasmine tea"
        )
        self.assertEqual(generated.memory_ids, plan.memory_ids)

        MessageCRUD.create_user_message(
            self.db, self.conversation, "jasmine tea"
        )
        self.assertEqual(
            self.db.query(CompanionMemoryProvenance).count(), 0
        )
        content = "".join([chunk async for chunk in plan.stream])
        assistant, _ = MessageCRUD.create_assistant_message(
            self.db,
            self.conversation,
            content,
            grounded_memory_ids=plan.memory_ids,
            memories_retrieved_at=plan.retrieved_at,
        )
        record = self.db.query(CompanionMemoryProvenance).one()
        self.assertEqual(record.assistant_message_id, assistant.message_id)
        self.assertEqual(record.memory_id, grounded.memory_id)
        self.assertEqual(
            self.ai.generated_messages,
            self.ai.streamed_messages,
        )

    async def test_generation_failure_leaves_no_messages_or_provenance(self):
        self.add_memory("Jasmine", "Jasmine tea")

        class FailingAI(CapturingAI):
            async def generate_response(self, messages):
                raise RuntimeError("provider failed")

        self.service._ai_service = FailingAI()
        with self.assertRaises(RuntimeError):
            await self.service.generate_response_with_provenance(
                self.db, self.conversation, "jasmine"
            )
        self.assertEqual(self.db.query(Message).count(), 0)
        self.assertEqual(
            self.db.query(CompanionMemoryProvenance).count(), 0
        )

    def test_sqlite_foreign_keys_remove_orphan_provenance(self):
        enabled = self.db.execute(text("PRAGMA foreign_keys")).scalar()
        self.assertEqual(enabled, 1)
        grounded = self.add_memory("Jasmine", "Jasmine tea")
        _, assistant, _ = MessageCRUD.create_message_pair(
            self.db,
            self.conversation,
            "Jasmine?",
            "A grounded answer",
            grounded_memory_ids=(grounded.memory_id,),
            memories_retrieved_at=datetime.now(timezone.utc),
        )
        self.assertEqual(
            self.db.query(CompanionMemoryProvenance).count(), 1
        )
        self.db.delete(assistant)
        self.db.commit()
        self.assertEqual(
            self.db.query(CompanionMemoryProvenance).count(), 0
        )
