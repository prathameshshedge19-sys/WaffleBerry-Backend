"""Phase 6.6.1 My Legacy dashboard contract tests."""

import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.v1.story_memory import get_legacy_dashboard
from app.crud.memory import LegacyCRUD
from app.db import Base
from app.models.memory import (
    Memory,
    MemoryExtractionRun,
    MemoryExtractionRunStatus,
    MemoryReviewStatus,
    MemoryType,
    StoryMessage,
    StoryMessageRole,
    StorySession,
    StorySessionStatus,
)
from app.models.user import Conversation, User
from app.schemas.memory import LegacyCreate, LegacyDashboardResponse
from app.services.legacy_dashboard import (
    LegacyDashboardNotFoundError,
    LegacyDashboardService,
)


class LegacyDashboardTests(unittest.TestCase):
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
            email="dashboard-owner@example.test",
            password_hash="hash",
        )
        self.other = User(
            full_name="Other",
            email="dashboard-other@example.test",
            password_hash="hash",
        )
        self.db.add_all([self.owner, self.other])
        self.db.commit()
        self.legacy = LegacyCRUD.create_legacy(
            self.db,
            self.owner.user_id,
            LegacyCreate(
                display_name="Mom",
                relationship="mother",
            ),
        )

    def tearDown(self):
        self.db.close()

    def seed_dashboard_facts(self):
        sessions = [
            StorySession(
                legacy_id=self.legacy.legacy_id,
                chapter_key="childhood",
                title="Childhood",
                status=StorySessionStatus.COMPLETED,
                created_by_user_id=self.owner.user_id,
            ),
            StorySession(
                legacy_id=self.legacy.legacy_id,
                chapter_key="career",
                title="Career",
                status=StorySessionStatus.IN_PROGRESS,
                created_by_user_id=self.owner.user_id,
            ),
            StorySession(
                legacy_id=self.legacy.legacy_id,
                chapter_key="career",
                title="Career follow-up",
                status=StorySessionStatus.PAUSED,
                created_by_user_id=self.owner.user_id,
            ),
        ]
        self.db.add_all(sessions)
        self.db.flush()
        self.db.add_all(
            [
                StoryMessage(
                    story_session_id=sessions[0].story_session_id,
                    role=StoryMessageRole.ASSISTANT,
                    content="What was home like?",
                    sequence=1,
                ),
                StoryMessage(
                    story_session_id=sessions[0].story_session_id,
                    role=StoryMessageRole.USER,
                    content="It had a jasmine garden.",
                    sequence=2,
                ),
                StoryMessage(
                    story_session_id=sessions[1].story_session_id,
                    role=StoryMessageRole.USER,
                    content="I started work in the city.",
                    sequence=1,
                ),
            ]
        )
        self.db.add_all(
            [
                Memory(
                    legacy_id=self.legacy.legacy_id,
                    memory_type=MemoryType.ATOMIC,
                    category="preference",
                    title="Jasmine",
                    summary="Mom loved jasmine.",
                    review_status=MemoryReviewStatus.APPROVED,
                ),
                Memory(
                    legacy_id=self.legacy.legacy_id,
                    memory_type=MemoryType.NARRATIVE,
                    category="story",
                    title="First job",
                    summary="Mom described her first job.",
                    review_status=MemoryReviewStatus.CANDIDATE,
                ),
                Memory(
                    legacy_id=self.legacy.legacy_id,
                    memory_type=MemoryType.ATOMIC,
                    category="place",
                    title="Old address",
                    summary="An unsupported old address.",
                    review_status=MemoryReviewStatus.REJECTED,
                ),
            ]
        )
        self.db.add_all(
            [
                MemoryExtractionRun(
                    legacy_id=self.legacy.legacy_id,
                    story_session_id=sessions[0].story_session_id,
                    message_boundary=2,
                    trigger_type="session_completed",
                    status=MemoryExtractionRunStatus.COMPLETED,
                ),
                MemoryExtractionRun(
                    legacy_id=self.legacy.legacy_id,
                    story_session_id=sessions[1].story_session_id,
                    message_boundary=1,
                    trigger_type="session_completed",
                    status=MemoryExtractionRunStatus.FAILED,
                ),
            ]
        )
        self.db.add(
            Conversation(
                user_id=self.owner.user_id,
                legacy_id=self.legacy.legacy_id,
                title="Companion chat",
            )
        )
        self.db.commit()

    def test_service_aggregates_existing_legacy_facts(self):
        self.seed_dashboard_facts()

        result = LegacyDashboardService().get_summary(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
        )

        self.assertEqual(result.title, "Mom")
        self.assertEqual(result.stories.total_sessions, 3)
        self.assertEqual(result.stories.distinct_chapters, 2)
        self.assertEqual(result.stories.completed_sessions, 1)
        self.assertEqual(result.stories.in_progress_sessions, 1)
        self.assertEqual(result.stories.paused_sessions, 1)
        self.assertEqual(result.stories.total_messages, 3)
        self.assertEqual(result.stories.contributed_messages, 2)
        self.assertEqual(
            [category.id for category in result.story_session_categories],
            ["career", "childhood"],
        )
        self.assertEqual(
            result.story_session_categories[0].title,
            "Career",
        )
        self.assertEqual(
            result.story_session_categories[0].session_completion_percentage,
            0,
        )
        self.assertEqual(
            result.story_session_categories[0].completed_sessions,
            0,
        )
        self.assertEqual(
            result.story_session_categories[0].total_sessions,
            2,
        )
        self.assertEqual(
            result.story_session_categories[1].session_completion_percentage,
            100,
        )
        self.assertEqual(result.memories.total, 3)
        self.assertEqual(result.memories.approved, 1)
        self.assertEqual(result.memories.candidate, 1)
        self.assertEqual(result.memories.rejected, 1)
        self.assertEqual(result.memories.atomic, 2)
        self.assertEqual(result.memories.narrative, 1)
        self.assertEqual(result.extraction.total_runs, 2)
        self.assertEqual(result.extraction.completed_runs, 1)
        self.assertEqual(result.extraction.failed_runs, 1)
        self.assertEqual(result.linked_conversations, 1)
        self.assertTrue(result.has_approved_memories)

    def test_empty_legacy_returns_zeroed_summary(self):
        result = LegacyDashboardService().get_summary(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
        )

        self.assertEqual(result.stories.total_sessions, 0)
        self.assertEqual(result.memories.total, 0)
        self.assertEqual(result.extraction.total_runs, 0)
        self.assertEqual(result.story_session_categories, [])
        self.assertEqual(result.linked_conversations, 0)
        self.assertFalse(result.has_approved_memories)

    def test_service_enforces_legacy_ownership(self):
        with self.assertRaises(LegacyDashboardNotFoundError):
            LegacyDashboardService().get_summary(
                self.db,
                user_id=self.other.user_id,
                legacy_id=self.legacy.legacy_id,
            )

    def test_route_returns_404_for_another_users_legacy(self):
        with self.assertRaises(HTTPException) as context:
            get_legacy_dashboard(
                self.legacy.legacy_id,
                current_user=self.other,
                db=self.db,
            )

        self.assertEqual(context.exception.status_code, 404)

    def test_story_session_percentage_uses_completed_over_total(self):
        self.seed_dashboard_facts()
        career = (
            self.db.query(StorySession)
            .filter(
                StorySession.legacy_id == self.legacy.legacy_id,
                StorySession.chapter_key == "career",
                StorySession.status == StorySessionStatus.PAUSED,
            )
            .one()
        )
        career.status = StorySessionStatus.COMPLETED
        self.db.commit()

        result = LegacyDashboardService().get_summary(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
        )
        category = next(
            item
            for item in result.story_session_categories
            if item.id == "career"
        )

        self.assertEqual(category.completed_sessions, 1)
        self.assertEqual(category.total_sessions, 2)
        self.assertEqual(category.session_completion_percentage, 50)

    def test_repeated_sessions_remain_explicit_in_session_counts(self):
        self.seed_dashboard_facts()

        result = LegacyDashboardService().get_summary(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
        )
        career = next(
            item
            for item in result.story_session_categories
            if item.id == "career"
        )

        self.assertEqual(career.total_sessions, 2)
        self.assertEqual(career.completed_sessions, 0)

    def test_category_key_case_variations_are_grouped(self):
        self.seed_dashboard_facts()
        self.db.add(
            StorySession(
                legacy_id=self.legacy.legacy_id,
                chapter_key=" Career ",
                title="A conflicting session title",
                status=StorySessionStatus.COMPLETED,
                created_by_user_id=self.owner.user_id,
            )
        )
        self.db.commit()

        result = LegacyDashboardService().get_summary(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
        )
        career = next(
            item
            for item in result.story_session_categories
            if item.id == "career"
        )

        self.assertEqual(career.total_sessions, 3)
        self.assertEqual(career.completed_sessions, 1)
        self.assertEqual(career.title, "Career")

    def test_category_title_does_not_depend_on_session_titles(self):
        self.seed_dashboard_facts()
        for story in self.db.query(StorySession).filter(
            StorySession.chapter_key == "career"
        ):
            story.title = "Different title"
        self.db.commit()

        result = LegacyDashboardService().get_summary(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
        )
        career = next(
            item
            for item in result.story_session_categories
            if item.id == "career"
        )

        self.assertEqual(career.title, "Career")

    def test_story_session_categories_defaults_when_missing(self):
        self.seed_dashboard_facts()
        result = LegacyDashboardService().get_summary(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
        )
        payload = result.model_dump()
        payload.pop("story_session_categories")

        restored = LegacyDashboardResponse.model_validate(payload)

        self.assertEqual(restored.story_session_categories, [])


if __name__ == "__main__":
    unittest.main()
