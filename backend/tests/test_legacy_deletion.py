"""Phase 6.8.3 secure, atomic Legacy deletion tests."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.v1.story_memory import delete_legacy, list_legacies
from app.db import Base
from app.dependencies.auth import get_current_user
from app.models.memory import (
    CompanionMemoryProvenance,
    Legacy,
    LegacyStatus,
    Memory,
    MemoryExtractionRun,
    MemoryExtractionRunStatus,
    MemoryProvenance,
    MemoryReviewStatus,
    MemoryType,
    StoryMessage,
    StoryMessageRole,
    StorySession,
)
from app.models.user import Conversation, Message, MessageRole, User
from app.schemas.memory import LegacyDeletionRequest
from app.services.legacy_dashboard import (
    LegacyDashboardNotFoundError,
    LegacyDashboardService,
)
from app.services.legacy_lifecycle import (
    LegacyDeletionConfirmationError,
    LegacyLifecycleNotFoundError,
    LegacyLifecycleService,
)


class LegacyDeletionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        event.listen(
            self.engine,
            "connect",
            lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.owner = User(
            full_name="Owner",
            email="delete-owner@example.test",
            password_hash="hash",
        )
        self.other = User(
            full_name="Other",
            email="delete-other@example.test",
            password_hash="hash",
        )
        self.db.add_all([self.owner, self.other])
        self.db.flush()
        self.legacy = self._make_graph(self.owner, "Mamá 🌼")
        self.other_legacy = self._make_graph(self.other, "Other")
        self.db.commit()
        self.owner_id = self.owner.user_id
        self.other_id = self.other.user_id
        self.legacy_id = self.legacy.legacy_id
        self.other_legacy_id = self.other_legacy.legacy_id
        self.service = LegacyLifecycleService()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _make_graph(self, owner, name):
        legacy = Legacy(
            owner_user_id=owner.user_id,
            display_name=name,
            relationship="Mother",
        )
        self.db.add(legacy)
        self.db.flush()
        story = StorySession(
            legacy_id=legacy.legacy_id,
            chapter_key="childhood",
            title="Childhood",
            created_by_user_id=owner.user_id,
        )
        conversation = Conversation(
            user_id=owner.user_id,
            legacy_id=legacy.legacy_id,
            title=f"{name} chat",
        )
        memory = Memory(
            legacy_id=legacy.legacy_id,
            memory_type=MemoryType.ATOMIC,
            category="story",
            title="A memory",
            summary="A grounded memory.",
            review_status=MemoryReviewStatus.APPROVED,
        )
        self.db.add_all([story, conversation, memory])
        self.db.flush()
        story_message = StoryMessage(
            story_session_id=story.story_session_id,
            sequence=1,
            role=StoryMessageRole.USER,
            content="A story.",
        )
        message = Message(
            conversation_id=conversation.conversation_id,
            role=MessageRole.ASSISTANT,
            content="I remember this.",
        )
        provenance = MemoryProvenance(
            memory_id=memory.memory_id,
            source_type="story_session",
            story_session_id=story.story_session_id,
            excerpt="A story.",
        )
        extraction = MemoryExtractionRun(
            legacy_id=legacy.legacy_id,
            story_session_id=story.story_session_id,
            message_boundary=1,
            trigger_type="completion",
            status=MemoryExtractionRunStatus.COMPLETED,
        )
        self.db.add_all([story_message, message, provenance, extraction])
        self.db.flush()
        self.db.add(
            CompanionMemoryProvenance(
                assistant_message_id=message.message_id,
                memory_id=memory.memory_id,
                retrieval_order=0,
                retrieved_at=datetime.now(timezone.utc),
            )
        )
        return legacy

    def _delete(self, confirmation="Mamá 🌼"):
        return self.service.delete(
            self.db,
            user_id=self.owner_id,
            legacy_id=self.legacy_id,
            confirmation_text=confirmation,
        )

    def test_owner_deletes_active_graph_without_orphans(self):
        self._delete()
        for model in (
            Legacy,
            StorySession,
            StoryMessage,
            Conversation,
            Message,
            Memory,
            MemoryProvenance,
            CompanionMemoryProvenance,
            MemoryExtractionRun,
        ):
            with self.subTest(model=model.__name__):
                self.assertEqual(self.db.query(model).count(), 1)
        self.assertIsNotNone(self.db.get(Legacy, self.other_legacy_id))

    def test_owner_deletes_archived_legacy(self):
        self.service.archive(
            self.db, user_id=self.owner_id, legacy_id=self.legacy_id
        )
        self._delete()
        self.assertIsNone(self.db.get(Legacy, self.legacy_id))

    def test_confirmation_is_required_and_case_sensitive(self):
        with self.assertRaises(ValidationError):
            LegacyDeletionRequest()
        for value in ("Wrong", "mamá 🌼"):
            with self.subTest(value=value):
                with self.assertRaises(LegacyDeletionConfirmationError):
                    self._delete(value)
        self.assertIsNotNone(self.db.get(Legacy, self.legacy_id))

    def test_unicode_confirmation_trims_outer_whitespace(self):
        self._delete("  Mamá 🌼 \n")
        self.assertIsNone(self.db.get(Legacy, self.legacy_id))

    def test_missing_foreign_and_repeated_deletes_are_neutral_not_found(self):
        with self.assertRaises(LegacyLifecycleNotFoundError):
            self.service.delete(
                self.db,
                user_id=self.other_id,
                legacy_id=self.legacy_id,
                confirmation_text="Mamá 🌼",
            )
        self._delete()
        with self.assertRaises(LegacyLifecycleNotFoundError):
            self._delete()

    def test_route_maps_confirmation_and_lookup_failures_safely(self):
        with self.assertRaises(HTTPException) as mismatch:
            delete_legacy(
                self.legacy_id,
                LegacyDeletionRequest(confirmation_text="Wrong"),
                current_user=self.owner,
                db=self.db,
            )
        self.assertEqual(mismatch.exception.status_code, 400)
        with self.assertRaises(HTTPException) as foreign:
            delete_legacy(
                self.legacy_id,
                LegacyDeletionRequest(confirmation_text="Mamá 🌼"),
                current_user=self.other,
                db=self.db,
            )
        self.assertEqual(foreign.exception.status_code, 404)
        with self.assertRaises(HTTPException) as unauthorized:
            get_current_user(credentials=None, db=self.db)
        self.assertEqual(unauthorized.exception.status_code, 401)

    def test_commit_failure_rolls_back_complete_graph(self):
        with patch.object(self.db, "commit", side_effect=RuntimeError("fail")):
            with self.assertRaises(RuntimeError):
                self._delete()
        self.assertIsNotNone(self.db.get(Legacy, self.legacy_id))
        self.assertEqual(
            self.db.query(StorySession)
            .filter(StorySession.legacy_id == self.legacy_id)
            .count(),
            1,
        )
        self.assertEqual(
            self.db.query(Memory).filter(Memory.legacy_id == self.legacy_id).count(),
            1,
        )

    def test_lists_and_companion_backing_lookup_reflect_deletion(self):
        self._delete()
        active = list_legacies(
            LegacyStatus.ACTIVE,
            current_user=self.owner,
            db=self.db,
        )
        archived = list_legacies(
            LegacyStatus.ARCHIVED,
            current_user=self.owner,
            db=self.db,
        )
        self.assertEqual(active, [])
        self.assertEqual(archived, [])
        with self.assertRaises(LegacyDashboardNotFoundError):
            LegacyDashboardService().get_summary(
                self.db, user_id=self.owner_id, legacy_id=self.legacy_id
            )

    def test_large_legacy_deletes_without_ai_or_network_calls(self):
        for index in range(100):
            self.db.add(
                Memory(
                    legacy_id=self.legacy_id,
                    memory_type=MemoryType.ATOMIC,
                    category="story",
                    title=f"Memory {index}",
                    summary=f"Summary {index}",
                    review_status=MemoryReviewStatus.CANDIDATE,
                )
            )
        self.db.commit()
        self._delete()
        self.assertEqual(
            self.db.query(Memory).filter(Memory.legacy_id == self.legacy_id).count(),
            0,
        )


if __name__ == "__main__":
    unittest.main()
