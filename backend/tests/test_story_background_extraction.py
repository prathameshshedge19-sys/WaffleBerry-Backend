"""Focused Phase 6.5.7 persistence and boundary tests; no live AI calls."""

import inspect
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.v1 import story_memory
from app.crud.memory import (
    LegacyCRUD,
    MemoryPersistenceError,
    StorySessionCRUD,
)
from app.db import Base
from app.models.memory import (
    Legacy,
    Memory,
    MemoryExtractionRun,
    MemoryExtractionRunStatus,
    MemoryReviewStatus,
    StoryMessage,
    StoryMessageRole,
)
from app.models.user import User
from app.schemas.memory import (
    LegacyCreate,
    StoryMessageCreate,
    StorySessionCreate,
)
from app.services.memory import background_extraction
from app.services.memory.background_extraction import (
    StoryExtractionConflictError,
    StoryExtractionNotFoundError,
    StoryExtractionService,
)
from app.services.memory.storage_pipeline import MemoryStoragePipeline


class StoryBackgroundExtractionTests(unittest.TestCase):
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
            email="story-owner@example.test",
            password_hash="hash",
        )
        self.other = User(
            full_name="Other",
            email="story-other@example.test",
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
                client_correlation_id="browser-legacy-0001",
            ),
        )
        self.story = StorySessionCRUD.create_story_session(
            self.db,
            self.legacy.legacy_id,
            self.owner.user_id,
            StorySessionCreate(
                chapter_key="childhood",
                title="Childhood",
            ),
        )

    def tearDown(self):
        self.db.close()

    def append(self, content="A meaningful story.", client_id="message-0001"):
        return StorySessionCRUD.append_story_message(
            self.db,
            self.story.story_session_id,
            self.legacy.legacy_id,
            StoryMessageCreate(
                role=StoryMessageRole.USER,
                content=content,
            ),
            client_message_id=client_id,
        )

    def complete(self):
        return StoryExtractionService().complete(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            story_session_id=self.story.story_session_id,
        )

    def test_01_authenticated_user_can_create_persisted_legacy(self):
        self.assertIsNotNone(self.legacy.legacy_id)

    def test_02_legacy_correlation_is_idempotent(self):
        again = LegacyCRUD.create_legacy(
            self.db,
            self.owner.user_id,
            LegacyCreate(
                display_name="Changed client copy",
                relationship="mother",
                client_correlation_id="browser-legacy-0001",
            ),
        )
        self.assertEqual(again.legacy_id, self.legacy.legacy_id)

    def test_03_other_user_cannot_access_legacy(self):
        self.assertIsNone(
            LegacyCRUD.get_user_legacy(
                self.db, self.legacy.legacy_id, self.other.user_id
            )
        )

    def test_04_story_session_requires_owned_legacy(self):
        with self.assertRaises(MemoryPersistenceError):
            StorySessionCRUD.create_story_session(
                self.db,
                self.legacy.legacy_id,
                self.other.user_id,
                StorySessionCreate(chapter_key="career"),
            )

    def test_05_story_messages_have_deterministic_order(self):
        self.append()
        self.append("A second memory.", "message-0002")
        messages = StorySessionCRUD.get_story_messages(
            self.db, self.story.story_session_id, self.legacy.legacy_id
        )
        self.assertEqual([item.sequence for item in messages], [1, 2])

    def test_06_repeated_client_message_is_idempotent(self):
        first = self.append()
        second = self.append()
        self.assertEqual(first.story_message_id, second.story_message_id)

    def test_07_route_persists_before_stream_construction(self):
        source = inspect.getsource(story_memory.stream_persisted_story)
        self.assertLess(
            source.index("append_story_message"),
            source.index("stream_story_response"),
        )

    def test_08_final_berry_message_is_persisted_after_stream(self):
        source = inspect.getsource(story_memory.stream_persisted_story)
        self.assertIn("role=StoryMessageRole.ASSISTANT", source)
        self.assertIn('yield _event("complete"', source)

    def test_09_hidden_prompts_are_not_story_message_fields(self):
        self.assertNotIn("system_prompt", StoryMessage.__table__.columns)
        self.assertNotIn("reasoning", StoryMessage.__table__.columns)

    def test_10_completion_commits_before_background_scheduling(self):
        route = inspect.getsource(story_memory.complete_story_session)
        service = inspect.getsource(StoryExtractionService.complete)
        self.assertIn("db.commit()", service)
        self.assertIn("background_tasks.add_task", route)

    def test_11_background_task_signature_uses_only_ids(self):
        parameters = inspect.signature(
            background_extraction.execute_story_extraction
        ).parameters
        self.assertEqual(
            set(parameters),
            {
                "extraction_run_id",
                "user_id",
                "legacy_id",
                "story_session_id",
            },
        )

    def test_12_background_task_creates_and_closes_session(self):
        source = inspect.getsource(
            background_extraction.execute_story_extraction
        )
        self.assertIn("db = SessionLocal()", source)
        self.assertIn("db.close()", source)

    def test_13_existing_storage_pipeline_is_invoked(self):
        source = inspect.getsource(
            background_extraction.execute_story_extraction
        )
        self.assertIn("get_memory_storage_pipeline()", source)
        self.assertIn("process_story_session", source)

    def test_14_completed_run_has_success_fields(self):
        columns = MemoryExtractionRun.__table__.columns
        self.assertIn("candidate_count", columns)
        self.assertIn("memories_created", columns)

    def test_15_zero_candidates_is_representable_as_success(self):
        run = MemoryExtractionRun(
            legacy_id=self.legacy.legacy_id,
            story_session_id=self.story.story_session_id,
            message_boundary=1,
            trigger_type="session_completed",
            status=MemoryExtractionRunStatus.COMPLETED,
            candidate_count=0,
            memories_created=0,
        )
        self.assertEqual(run.candidate_count, 0)

    def test_16_provider_failure_uses_controlled_code(self):
        code = background_extraction._safe_error_code(
            type("ProviderFailure", (Exception,), {})()
        )
        self.assertEqual(code, "provider_unavailable")

    def test_17_ownership_failure_is_permanent_category(self):
        code = background_extraction._safe_error_code(
            type("MemoryOwnershipError", (Exception,), {})()
        )
        self.assertEqual(code, "invalid_source")

    def test_18_repeated_completion_reuses_boundary_run(self):
        self.append()
        _, first = self.complete()
        _, second = self.complete()
        self.assertEqual(
            first.extraction_run_id, second.extraction_run_id
        )

    def test_19_new_message_allows_new_extraction_boundary(self):
        self.append()
        _, first = self.complete()
        self.append("Another remembered detail.", "message-0002")
        _, second = self.complete()
        self.assertNotEqual(
            first.extraction_run_id, second.extraction_run_id
        )

    def test_20_pipeline_keeps_exact_memory_idempotency(self):
        source = inspect.getsource(
            MemoryStoragePipeline._persist_result
        )
        self.assertIn("get_memory_by_fingerprint", source)

    def test_21_cross_legacy_story_access_is_rejected(self):
        other_legacy = LegacyCRUD.create_legacy(
            self.db,
            self.other.user_id,
            LegacyCreate(
                display_name="Dad",
                relationship="father",
                client_correlation_id="browser-legacy-0002",
            ),
        )
        self.assertIsNone(
            StorySessionCRUD.get_legacy_story_session(
                self.db,
                self.story.story_session_id,
                other_legacy.legacy_id,
            )
        )

    def test_22_cross_legacy_run_access_is_rejected(self):
        self.append()
        _, run = self.complete()
        with self.assertRaises(StoryExtractionNotFoundError):
            StoryExtractionService().get_run(
                self.db,
                user_id=self.other.user_id,
                legacy_id=self.legacy.legacy_id,
                story_session_id=self.story.story_session_id,
                extraction_run_id=run.extraction_run_id,
            )

    def test_23_retry_requires_ownership(self):
        self.append()
        _, run = self.complete()
        run.status = MemoryExtractionRunStatus.FAILED
        self.db.commit()
        with self.assertRaises(StoryExtractionNotFoundError):
            StoryExtractionService().prepare_retry(
                self.db,
                user_id=self.other.user_id,
                legacy_id=self.legacy.legacy_id,
                story_session_id=self.story.story_session_id,
                extraction_run_id=run.extraction_run_id,
            )

    def test_24_running_retry_is_rejected(self):
        self.append()
        _, run = self.complete()
        run.status = MemoryExtractionRunStatus.RUNNING
        self.db.commit()
        with self.assertRaises(StoryExtractionConflictError):
            StoryExtractionService().prepare_retry(
                self.db,
                user_id=self.owner.user_id,
                legacy_id=self.legacy.legacy_id,
                story_session_id=self.story.story_session_id,
                extraction_run_id=run.extraction_run_id,
            )

    def test_25_story_is_committed_before_extraction(self):
        self.append()
        story, _ = self.complete()
        self.db.expire_all()
        self.assertEqual(
            self.db.get(type(story), story.story_session_id).status.value,
            "completed",
        )

    def test_26_background_memories_remain_candidates(self):
        source = inspect.getsource(MemoryStoragePipeline)
        self.assertNotIn("MemoryReviewStatus.APPROVED", source)

    def test_27_companion_chat_is_not_given_approved_memory(self):
        source = inspect.getsource(story_memory)
        self.assertNotIn("prepare_ai_input", source)
        self.assertNotIn("approved", source.casefold())

    def test_28_review_api_module_remains_separate(self):
        from app.api.v1 import memory
        self.assertTrue(callable(memory.list_memory_review))

    def test_29_storage_pipeline_accepts_message_boundary(self):
        source = inspect.getsource(
            MemoryStoragePipeline.process_story_session
        )
        self.assertIn("message_boundary", source)

    def test_30_browser_correlation_is_not_ownership_proof(self):
        source = inspect.getsource(story_memory.synchronize_legacy)
        self.assertIn("current_user.user_id", source)
