"""Phase 6.7.4 internal Companion grounding provenance tests."""

import inspect
import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.crud.user import MessageCRUD
from app.db import Base
from app.models.memory import (
    CompanionMemoryProvenance,
    Legacy,
    Memory,
    MemoryReviewStatus,
    MemoryType,
)
from app.models.user import Conversation, Message, MessageRole, User
from app.schemas.user import MessagePairResponse, MessageResponse
from app.api.v1 import user as user_api


class MemoryProvenanceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.owner = User(
            full_name="Owner",
            email="provenance-owner@example.test",
            password_hash="hash",
        )
        self.other = User(
            full_name="Other",
            email="provenance-other@example.test",
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
            display_name="Dad",
            relationship="Father",
        )
        self.db.add_all([self.legacy, self.other_legacy])
        self.db.flush()
        self.conversation = Conversation(
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            title="Legacy chat",
        )
        self.db.add(self.conversation)
        self.db.commit()
        self.retrieved_at = datetime(2026, 7, 31, 15, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.db.close()

    def memory(self, *, legacy=None, status=MemoryReviewStatus.APPROVED):
        memory = Memory(
            legacy_id=(legacy or self.legacy).legacy_id,
            memory_type=MemoryType.ATOMIC,
            category="personal_detail",
            title="A memory",
            summary="A grounded detail.",
            review_status=status,
        )
        self.db.add(memory)
        self.db.commit()
        return memory

    def provenance(self):
        return (
            self.db.query(CompanionMemoryProvenance)
            .order_by(CompanionMemoryProvenance.retrieval_order)
            .all()
        )

    def test_successful_grounded_pair_records_multiple_memories_in_order(self):
        first = self.memory()
        second = self.memory()
        _, assistant, _ = MessageCRUD.create_message_pair(
            self.db,
            self.conversation,
            "Tell me",
            "A grounded reply",
            grounded_memory_ids=(second.memory_id, first.memory_id),
            memories_retrieved_at=self.retrieved_at,
        )

        records = self.provenance()
        self.assertEqual(
            [(row.memory_id, row.retrieval_order) for row in records],
            [(second.memory_id, 0), (first.memory_id, 1)],
        )
        self.assertTrue(
            all(row.assistant_message_id == assistant.message_id for row in records)
        )

    def test_ungrounded_reply_creates_no_provenance(self):
        MessageCRUD.create_message_pair(
            self.db, self.conversation, "Hello", "Hello back"
        )
        self.assertEqual(self.provenance(), [])

    def test_streaming_assistant_persists_provenance_after_message(self):
        memory = self.memory()
        MessageCRUD.create_user_message(
            self.db, self.conversation, "Remember this"
        )
        assistant, _ = MessageCRUD.create_assistant_message(
            self.db,
            self.conversation,
            "I remember.",
            grounded_memory_ids=(memory.memory_id,),
            memories_retrieved_at=self.retrieved_at,
        )
        record = self.provenance()[0]
        self.assertEqual(record.assistant_message_id, assistant.message_id)

    def test_duplicate_memory_ids_fail_atomically(self):
        memory = self.memory()
        with self.assertRaises(ValueError):
            MessageCRUD.create_message_pair(
                self.db,
                self.conversation,
                "Question",
                "Answer",
                grounded_memory_ids=(memory.memory_id, memory.memory_id),
                memories_retrieved_at=self.retrieved_at,
            )
        self.assertEqual(self.provenance(), [])
        self.assertEqual(self.db.query(Message).count(), 0)

    def test_cross_legacy_and_nonapproved_memories_are_rejected(self):
        invalid = [
            self.memory(legacy=self.other_legacy),
            self.memory(status=MemoryReviewStatus.CANDIDATE),
            self.memory(status=MemoryReviewStatus.REJECTED),
        ]
        for memory in invalid:
            with self.subTest(memory_id=memory.memory_id):
                with self.assertRaises(ValueError):
                    MessageCRUD.create_assistant_message(
                        self.db,
                        self.conversation,
                        "Unsafe reply",
                        grounded_memory_ids=(memory.memory_id,),
                        memories_retrieved_at=self.retrieved_at,
                    )
                self.assertEqual(self.provenance(), [])

    def test_legacy_owner_mismatch_is_rejected(self):
        memory = self.memory()
        self.conversation.user_id = self.other.user_id
        self.db.commit()
        with self.assertRaises(ValueError):
            MessageCRUD.create_assistant_message(
                self.db,
                self.conversation,
                "Unsafe reply",
                grounded_memory_ids=(memory.memory_id,),
                memories_retrieved_at=self.retrieved_at,
            )
        self.assertEqual(self.provenance(), [])

    def test_failed_provenance_rolls_back_assistant_but_keeps_stream_user(self):
        MessageCRUD.create_user_message(
            self.db, self.conversation, "Persisted user message"
        )
        missing_memory_id = 999999
        with self.assertRaises(ValueError):
            MessageCRUD.create_assistant_message(
                self.db,
                self.conversation,
                "Must roll back",
                grounded_memory_ids=(missing_memory_id,),
                memories_retrieved_at=self.retrieved_at,
            )
        messages = self.db.query(Message).all()
        self.assertEqual([message.role for message in messages], [MessageRole.USER])
        self.assertEqual(self.provenance(), [])

    def test_no_public_message_schema_leakage(self):
        public_fields = set(MessageResponse.model_fields)
        pair_fields = set(MessagePairResponse.model_fields)
        for hidden in (
            "memory_ids",
            "retrieval_order",
            "retrieved_at",
            "grounding_provenance",
        ):
            self.assertNotIn(hidden, public_fields)
            self.assertNotIn(hidden, pair_fields)

    def test_generation_must_finish_before_any_provenance_persistence(self):
        source = inspect.getsource(user_api.create_message)
        self.assertLess(
            source.index("generate_response_with_provenance"),
            source.index("MessageCRUD.create_message_pair"),
        )

    def test_story_and_extraction_tables_have_no_companion_provenance(self):
        table = CompanionMemoryProvenance.__table__
        self.assertNotIn("story_session_id", table.c)
        self.assertNotIn("extraction_run_id", table.c)


if __name__ == "__main__":
    unittest.main()
