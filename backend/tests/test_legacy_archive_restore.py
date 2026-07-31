"""Phase 6.8.2 owner-scoped Legacy archive and restore tests."""

import unittest
import asyncio
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.v1.story_memory import (
    archive_legacy,
    create_or_resume_story_session,
    list_legacies,
    restore_legacy,
)
from app.api.v1.user import (
    _require_active_conversation_legacy,
    create_conversation,
)
from app.crud.memory import LegacyCRUD
from app.db import Base
from app.dependencies.auth import get_current_user
from app.models.memory import (
    CompanionMemoryProvenance,
    Legacy,
    LegacyStatus,
    Memory,
    MemoryReviewStatus,
    MemoryType,
    StoryMessage,
    StoryMessageRole,
    StorySession,
)
from app.models.user import Conversation, Message, MessageRole, User
from app.schemas.memory import LegacySettingsUpdate, StorySessionCreate
from app.schemas.user import ConversationCreate
from app.services.legacy_dashboard import LegacyDashboardService
from app.services.legacy_lifecycle import (
    LegacyLifecycleNotFoundError,
    LegacyLifecycleService,
)
from app.services.legacy_settings import (
    LegacySettingsArchivedError,
    LegacySettingsService,
)
from app.services.memory.retrieval import (
    MemoryRetrievalArchivedError,
    MemoryRetrievalService,
)


class LegacyArchiveRestoreTests(unittest.TestCase):
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
            email="archive-owner@example.test",
            password_hash="hash",
        )
        self.other = User(
            full_name="Other",
            email="archive-other@example.test",
            password_hash="hash",
        )
        self.db.add_all([self.owner, self.other])
        self.db.flush()
        self.legacy = Legacy(
            owner_user_id=self.owner.user_id,
            display_name="Mom",
            relationship="Mother",
            client_correlation_id="legacy-browser-correlation",
        )
        self.other_legacy = Legacy(
            owner_user_id=self.other.user_id,
            display_name="Other",
            relationship="Relative",
        )
        self.db.add_all([self.legacy, self.other_legacy])
        self.db.flush()
        self.story = StorySession(
            legacy_id=self.legacy.legacy_id,
            chapter_key="childhood",
            title="Childhood",
            created_by_user_id=self.owner.user_id,
        )
        self.conversation = Conversation(
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            title="Mom chat",
        )
        self.memory = Memory(
            legacy_id=self.legacy.legacy_id,
            memory_type=MemoryType.ATOMIC,
            category="personal_detail",
            title="Mother's name",
            summary="Mother's name is Anita.",
            review_status=MemoryReviewStatus.APPROVED,
        )
        self.db.add_all([self.story, self.conversation, self.memory])
        self.db.flush()
        self.story_message = StoryMessage(
            story_session_id=self.story.story_session_id,
            sequence=1,
            role=StoryMessageRole.USER,
            content="A childhood memory.",
        )
        self.user_message = Message(
            conversation_id=self.conversation.conversation_id,
            role=MessageRole.USER,
            content="What was Mom's name?",
        )
        self.assistant_message = Message(
            conversation_id=self.conversation.conversation_id,
            role=MessageRole.ASSISTANT,
            content="You shared that her name was Anita.",
        )
        self.db.add_all(
            [self.story_message, self.user_message, self.assistant_message]
        )
        self.db.flush()
        self.provenance = CompanionMemoryProvenance(
            assistant_message_id=self.assistant_message.message_id,
            memory_id=self.memory.memory_id,
            retrieval_order=0,
            retrieved_at=datetime.now(timezone.utc),
        )
        self.db.add(self.provenance)
        self.db.commit()
        self.service = LegacyLifecycleService()

    def tearDown(self):
        self.db.close()

    def archive(self):
        return self.service.archive(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
        )

    def restore(self):
        return self.service.restore(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
        )

    def test_owner_archives_and_restores_with_idempotent_repeats(self):
        before = self.legacy.updated_at
        archived = self.archive()
        repeated_archive = self.archive()
        self.assertEqual(archived.status, LegacyStatus.ARCHIVED)
        self.assertEqual(repeated_archive.status, LegacyStatus.ARCHIVED)
        self.assertNotEqual(archived.updated_at, before)
        restored = self.restore()
        repeated_restore = self.restore()
        self.assertEqual(restored.status, LegacyStatus.ACTIVE)
        self.assertEqual(repeated_restore.status, LegacyStatus.ACTIVE)
        self.assertEqual(
            set(restored.model_dump()),
            {"legacy_id", "status", "display_name", "relationship", "updated_at"},
        )

    def test_conversation_creation_links_only_owned_active_legacy(self):
        created = asyncio.run(
            create_conversation(
                ConversationCreate(legacy_id=self.legacy.legacy_id),
                self.owner,
                self.db,
            )
        )
        self.assertEqual(created.user_id, self.owner.user_id)
        self.assertEqual(created.legacy_id, self.legacy.legacy_id)

        with self.assertRaises(HTTPException) as foreign:
            asyncio.run(
                create_conversation(
                    ConversationCreate(legacy_id=self.other_legacy.legacy_id),
                    self.owner,
                    self.db,
                )
            )
        self.assertEqual(foreign.exception.status_code, 404)

        self.archive()
        with self.assertRaises(HTTPException) as archived:
            asyncio.run(
                create_conversation(
                    ConversationCreate(legacy_id=self.legacy.legacy_id),
                    self.owner,
                    self.db,
                )
            )
        self.assertEqual(archived.exception.status_code, 409)

    def test_foreign_and_missing_transitions_use_neutral_not_found(self):
        for action in (self.service.archive, self.service.restore):
            for user_id, legacy_id in (
                (self.other.user_id, self.legacy.legacy_id),
                (self.owner.user_id, 999999),
            ):
                with self.subTest(action=action.__name__, legacy_id=legacy_id):
                    with self.assertRaises(LegacyLifecycleNotFoundError):
                        action(
                            self.db,
                            user_id=user_id,
                            legacy_id=legacy_id,
                        )

    def test_route_neutral_404_and_authentication_dependency(self):
        for route in (archive_legacy, restore_legacy):
            with self.subTest(route=route.__name__):
                with self.assertRaises(HTTPException) as context:
                    route(
                        self.legacy.legacy_id,
                        current_user=self.other,
                        db=self.db,
                    )
                self.assertEqual(context.exception.status_code, 404)
        with self.assertRaises(HTTPException) as context:
            get_current_user(credentials=None, db=self.db)
        self.assertEqual(context.exception.status_code, 401)

    def test_archive_preserves_all_related_data_and_identity(self):
        self.archive()
        self.assertEqual(self.db.query(Legacy).count(), 2)
        self.assertEqual(self.db.query(StorySession).count(), 1)
        self.assertEqual(self.db.query(StoryMessage).count(), 1)
        self.assertEqual(self.db.query(Memory).count(), 1)
        self.assertEqual(self.db.query(Conversation).count(), 1)
        self.assertEqual(self.db.query(Message).count(), 2)
        self.assertEqual(self.db.query(CompanionMemoryProvenance).count(), 1)
        self.db.refresh(self.legacy)
        self.assertEqual(
            self.legacy.client_correlation_id,
            "legacy-browser-correlation",
        )

    def test_active_and_archived_listings_are_explicit(self):
        self.archive()
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
        self.assertEqual([item.legacy_id for item in archived], [self.legacy.legacy_id])

    def test_dashboard_and_history_remain_readable(self):
        self.archive()
        dashboard = LegacyDashboardService().get_summary(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
        )
        self.assertEqual(dashboard.status, LegacyStatus.ARCHIVED)
        history = (
            self.db.query(Message)
            .filter(Message.conversation_id == self.conversation.conversation_id)
            .all()
        )
        self.assertEqual(len(history), 2)

    def test_archived_settings_story_and_companion_are_blocked(self):
        self.archive()
        with self.assertRaises(LegacySettingsArchivedError):
            LegacySettingsService().update(
                self.db,
                user_id=self.owner.user_id,
                legacy_id=self.legacy.legacy_id,
                changes=LegacySettingsUpdate(
                    expected_updated_at=self.legacy.updated_at,
                    display_name="New name",
                ),
            )
        for operation in (
            lambda: create_or_resume_story_session(
                self.legacy.legacy_id,
                StorySessionCreate(chapter_key="career"),
                current_user=self.owner,
                db=self.db,
            ),
            lambda: _require_active_conversation_legacy(
                self.db,
                self.conversation,
                self.owner.user_id,
            ),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(HTTPException) as context:
                    operation()
                self.assertEqual(context.exception.status_code, 409)

    def test_archived_grounding_blocked_but_direct_read_allowed(self):
        self.archive()
        retrieval = MemoryRetrievalService()
        with self.assertRaises(MemoryRetrievalArchivedError):
            retrieval.search_approved(
                self.db,
                user_id=self.owner.user_id,
                legacy_id=self.legacy.legacy_id,
                query="Anita",
            )
        direct = retrieval.search_approved(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            query="Anita",
            allow_archived=True,
        )
        self.assertEqual(direct.matched_memory_count, 1)

    def test_restore_reenables_active_behavior_without_duplicates(self):
        self.archive()
        self.restore()
        story = create_or_resume_story_session(
            self.legacy.legacy_id,
            StorySessionCreate(chapter_key="career"),
            current_user=self.owner,
            db=self.db,
        )
        self.assertEqual(story.legacy_id, self.legacy.legacy_id)
        self.assertEqual(self.db.query(Legacy).count(), 2)
        _require_active_conversation_legacy(
            self.db,
            self.conversation,
            self.owner.user_id,
        )


if __name__ == "__main__":
    unittest.main()
