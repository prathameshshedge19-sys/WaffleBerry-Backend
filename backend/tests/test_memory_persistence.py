"""Persistence tests for the Phase 6.5.2 Memory Engine schema."""

import unittest
from decimal import Decimal

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register all mapped classes
from app.crud.memory import (
    LegacyCRUD,
    MemoryCRUD,
    MemoryPersistenceError,
    StorySessionCRUD,
)
from app.db import Base
from app.models.memory import (
    MemoryReviewStatus,
    MemoryType,
    StoryMessageRole,
)
from app.models.user import Conversation, Message, MessageRole, User
from app.schemas.memory import (
    LegacyCreate,
    MemoryCandidateCreate,
    MemoryDetails,
    MemoryProvenanceCreate,
    StoryMessageCreate,
    StorySessionCreate,
    TemporalReference,
)


class MemoryPersistenceTests(unittest.TestCase):
    """Exercise ownership, provenance, lifecycle, and ordering invariants."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(cls.engine, "connect")
        def enable_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        cls.SessionLocal = sessionmaker(bind=cls.engine)

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()
        self.owner = User(
            full_name="Legacy Owner",
            email="owner@example.com",
            password_hash="hash",
        )
        self.other_user = User(
            full_name="Other User",
            email="other@example.com",
            password_hash="hash",
        )
        self.db.add_all([self.owner, self.other_user])
        self.db.commit()
        self.db.refresh(self.owner)
        self.db.refresh(self.other_user)

    def tearDown(self):
        self.db.close()

    def create_legacy(self, name="Mom", owner_id=None):
        return LegacyCRUD.create_legacy(
            self.db,
            owner_id or self.owner.user_id,
            LegacyCreate(
                display_name=name,
                relationship="Mother",
            ),
        )

    @staticmethod
    def manual_candidate(
        *,
        memory_type=MemoryType.ATOMIC,
        summary="Mom loved jasmine flowers.",
        **overrides,
    ):
        values = {
            "memory_type": memory_type,
            "category": (
                "story"
                if memory_type == MemoryType.NARRATIVE
                else "preference"
            ),
            "title": "A preserved memory",
            "summary": summary,
            "provenance": [
                MemoryProvenanceCreate(
                    source_type="manual",
                    excerpt=summary,
                    speaker="user",
                )
            ],
        }
        values.update(overrides)
        return MemoryCandidateCreate(**values)

    def test_one_user_can_own_multiple_legacies(self):
        first = self.create_legacy("Mom")
        second = self.create_legacy("Dad")

        owned = LegacyCRUD.get_user_legacies(
            self.db,
            self.owner.user_id,
        )

        self.assertEqual(
            [legacy.legacy_id for legacy in owned],
            [first.legacy_id, second.legacy_id],
        )
        self.assertIsNone(
            LegacyCRUD.get_user_legacy(
                self.db,
                first.legacy_id,
                self.other_user.user_id,
            )
        )

    def test_story_session_belongs_to_legacy_and_messages_are_ordered(self):
        legacy = self.create_legacy()
        session = StorySessionCRUD.create_story_session(
            self.db,
            legacy.legacy_id,
            self.owner.user_id,
            StorySessionCreate(chapter_key="childhood"),
        )
        second = StorySessionCRUD.append_story_message(
            self.db,
            session.story_session_id,
            legacy.legacy_id,
            StoryMessageCreate(
                role=StoryMessageRole.ASSISTANT,
                content="What felt most like home?",
            ),
        )
        first = StorySessionCRUD.append_story_message(
            self.db,
            session.story_session_id,
            legacy.legacy_id,
            StoryMessageCreate(
                role=StoryMessageRole.USER,
                content="The jasmine garden.",
            ),
        )

        ordered = StorySessionCRUD.get_story_messages(
            self.db,
            session.story_session_id,
            legacy.legacy_id,
        )

        self.assertEqual(session.legacy_id, legacy.legacy_id)
        self.assertEqual(
            [message.sequence for message in ordered],
            [second.sequence, first.sequence],
        )
        self.assertEqual([message.sequence for message in ordered], [1, 2])

    def test_atomic_and_narrative_memories_belong_to_one_legacy(self):
        legacy = self.create_legacy()
        atomic = MemoryCRUD.create_memory_candidate(
            self.db,
            legacy.legacy_id,
            self.owner.user_id,
            self.manual_candidate(),
        )
        narrative = MemoryCRUD.create_memory_candidate(
            self.db,
            legacy.legacy_id,
            self.owner.user_id,
            self.manual_candidate(
                memory_type=MemoryType.NARRATIVE,
                summary="Mom told a story about tending jasmine with Grandma.",
            ),
        )

        self.assertEqual(atomic.legacy_id, legacy.legacy_id)
        self.assertEqual(narrative.legacy_id, legacy.legacy_id)
        self.assertEqual(atomic.memory_type, MemoryType.ATOMIC)
        self.assertEqual(narrative.memory_type, MemoryType.NARRATIVE)

    def test_memory_supports_multiple_provenance_sources(self):
        legacy = self.create_legacy()
        memory = MemoryCRUD.create_memory_candidate(
            self.db,
            legacy.legacy_id,
            self.owner.user_id,
            self.manual_candidate(),
        )
        MemoryCRUD.attach_provenance(
            self.db,
            memory.memory_id,
            legacy.legacy_id,
            self.owner.user_id,
            MemoryProvenanceCreate(
                source_type="document",
                source_locator={"document_id": 10, "page": 2},
                excerpt="She grew jasmine every summer.",
                speaker="source_document",
            ),
        )
        self.db.refresh(memory)

        self.assertEqual(len(memory.provenance), 2)
        self.assertEqual(
            {source.source_type for source in memory.provenance},
            {"manual", "document"},
        )

    def test_uncertain_and_approximate_dates_are_stored_separately(self):
        legacy = self.create_legacy()
        candidate = self.manual_candidate(
            summary="Mom may have moved around 1985.",
            category="life_event",
            uncertainty_note="The speaker was unsure of the year.",
            extraction_confidence=Decimal("0.720"),
            details=MemoryDetails(
                temporal_references=[
                    TemporalReference(
                        text="around 1985",
                        start_date="1984-01-01",
                        end_date="1986-12-31",
                        precision="year",
                        is_approximate=True,
                        certainty="uncertain",
                    )
                ]
            ),
        )

        memory = MemoryCRUD.create_memory_candidate(
            self.db,
            legacy.legacy_id,
            self.owner.user_id,
            candidate,
        )

        temporal = memory.details["temporal_references"][0]
        self.assertTrue(temporal["is_approximate"])
        self.assertEqual(temporal["certainty"], "uncertain")
        self.assertEqual(
            memory.uncertainty_note,
            "The speaker was unsure of the year.",
        )
        self.assertEqual(memory.extraction_confidence, Decimal("0.720"))

    def test_review_states_and_revision_history_are_representable(self):
        legacy = self.create_legacy()
        approved = MemoryCRUD.create_memory_candidate(
            self.db,
            legacy.legacy_id,
            self.owner.user_id,
            self.manual_candidate(),
        )
        rejected = MemoryCRUD.create_memory_candidate(
            self.db,
            legacy.legacy_id,
            self.owner.user_id,
            self.manual_candidate(summary="A rejected interpretation."),
        )
        self.assertEqual(
            approved.review_status,
            MemoryReviewStatus.CANDIDATE,
        )

        approved = MemoryCRUD.update_review_status(
            self.db,
            approved.memory_id,
            legacy.legacy_id,
            self.owner.user_id,
            MemoryReviewStatus.APPROVED,
        )
        rejected = MemoryCRUD.update_review_status(
            self.db,
            rejected.memory_id,
            legacy.legacy_id,
            self.owner.user_id,
            MemoryReviewStatus.REJECTED,
        )
        revision = MemoryCRUD.add_revision(
            self.db,
            approved.memory_id,
            legacy.legacy_id,
            self.owner.user_id,
            previous_content={"summary": approved.summary},
            edit_reason="Clarified by the owner.",
        )

        self.assertEqual(
            approved.review_status,
            MemoryReviewStatus.APPROVED,
        )
        self.assertEqual(
            rejected.review_status,
            MemoryReviewStatus.REJECTED,
        )
        self.assertEqual(revision.revision_number, 1)
        self.assertEqual(
            revision.previous_content["summary"],
            "Mom loved jasmine flowers.",
        )

    def test_contradictions_coexist_and_supersession_is_explicit(self):
        legacy = self.create_legacy()
        group = MemoryCRUD.create_contradiction_group(
            self.db,
            legacy.legacy_id,
            self.owner.user_id,
            "Mom's birth year",
        )
        first = MemoryCRUD.create_memory_candidate(
            self.db,
            legacy.legacy_id,
            self.owner.user_id,
            self.manual_candidate(
                summary="Mom was born in 1968.",
                category="personal_detail",
                contradiction_group_id=group.contradiction_group_id,
            ),
        )
        second = MemoryCRUD.create_memory_candidate(
            self.db,
            legacy.legacy_id,
            self.owner.user_id,
            self.manual_candidate(
                summary="Mom was born in 1967.",
                category="personal_detail",
                contradiction_group_id=group.contradiction_group_id,
            ),
        )

        first = MemoryCRUD.supersede_memory(
            self.db,
            first.memory_id,
            second.memory_id,
            legacy.legacy_id,
            self.owner.user_id,
        )

        self.assertNotEqual(first.memory_id, second.memory_id)
        self.assertEqual(
            first.contradiction_group_id,
            second.contradiction_group_id,
        )
        self.assertEqual(
            first.review_status,
            MemoryReviewStatus.SUPERSEDED,
        )
        self.assertEqual(first.superseded_by_memory_id, second.memory_id)

    def test_cross_legacy_story_provenance_is_rejected(self):
        first_legacy = self.create_legacy("Mom")
        second_legacy = self.create_legacy("Dad")
        story_session = StorySessionCRUD.create_story_session(
            self.db,
            first_legacy.legacy_id,
            self.owner.user_id,
            StorySessionCreate(chapter_key="childhood"),
        )
        story_message = StorySessionCRUD.append_story_message(
            self.db,
            story_session.story_session_id,
            first_legacy.legacy_id,
            StoryMessageCreate(
                role=StoryMessageRole.USER,
                content="We lived near the mountains.",
            ),
        )
        candidate = self.manual_candidate(
            provenance=[
                MemoryProvenanceCreate(
                    source_type="story_session",
                    story_session_id=story_session.story_session_id,
                    story_message_id=story_message.story_message_id,
                    excerpt=story_message.content,
                    speaker="user",
                    chapter="Childhood",
                )
            ]
        )

        with self.assertRaises(MemoryPersistenceError):
            MemoryCRUD.create_memory_candidate(
                self.db,
                second_legacy.legacy_id,
                self.owner.user_id,
                candidate,
            )

        self.assertEqual(
            MemoryCRUD.list_legacy_memories(
                self.db,
                second_legacy.legacy_id,
                self.owner.user_id,
            ),
            [],
        )

    def test_existing_conversations_remain_valid_without_legacy(self):
        conversation = Conversation(
            user_id=self.owner.user_id,
            title="Existing conversation",
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        message = Message(
            conversation_id=conversation.conversation_id,
            role=MessageRole.USER,
            content="Existing message",
        )
        self.db.add(message)
        self.db.commit()

        self.assertIsNone(conversation.legacy_id)
        self.assertEqual(message.conversation_id, conversation.conversation_id)


if __name__ == "__main__":
    unittest.main()
