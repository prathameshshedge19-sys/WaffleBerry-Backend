"""Regression checks added by the Phase 6.5.8 final audit."""

import inspect
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.v1 import story_memory
from app.crud.memory import (
    LegacyCRUD,
    MemoryCRUD,
    MemoryPersistenceError,
    StorySessionCRUD,
)
from app.db import Base
from app.models.memory import Legacy, StoryMessage, StoryMessageRole, StorySession
from app.models.user import User
from app.schemas.memory import (
    LegacyCreate,
    MemoryCandidateCreate,
    MemoryParticipantCreate,
    MemoryProvenanceCreate,
    StoryMessageCreate,
    StorySessionCreate,
)
from app.models.memory import MemoryType
from app.services.memory import background_extraction


class MemoryEngineHardeningTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.user = User(
            full_name="Owner",
            email="hardening@example.test",
            password_hash="hash",
        )
        self.db.add(self.user)
        self.db.commit()
        self.legacy = LegacyCRUD.create_legacy(
            self.db,
            self.user.user_id,
            LegacyCreate(
                display_name="Mom",
                relationship="mother",
                client_correlation_id="hardening-legacy",
            ),
        )
        self.story = StorySessionCRUD.create_story_session(
            self.db,
            self.legacy.legacy_id,
            self.user.user_id,
            StorySessionCreate(chapter_key="childhood"),
        )
        self.user_message = StorySessionCRUD.append_story_message(
            self.db,
            self.story.story_session_id,
            self.legacy.legacy_id,
            StoryMessageCreate(
                role=StoryMessageRole.USER,
                content="I grew up in Pune.",
            ),
            "user-source-0001",
        )
        self.assistant_message = StorySessionCRUD.append_story_message(
            self.db,
            self.story.story_session_id,
            self.legacy.legacy_id,
            StoryMessageCreate(
                role=StoryMessageRole.ASSISTANT,
                content="Thank you for sharing.",
            ),
            "assistant-source-0001",
        )

    def tearDown(self):
        self.db.close()

    def candidate(self, message, excerpt, speaker="user"):
        return MemoryCandidateCreate(
            memory_type=MemoryType.ATOMIC,
            category="place",
            title="Childhood home",
            summary="Mom grew up in Pune.",
            participants=[
                MemoryParticipantCreate(name="Mom", role="subject")
            ],
            provenance=[
                MemoryProvenanceCreate(
                    source_type="story_session",
                    story_session_id=self.story.story_session_id,
                    story_message_id=message.story_message_id,
                    excerpt=excerpt,
                    speaker=speaker,
                )
            ],
        )

    def test_direct_crud_rejects_assistant_provenance(self):
        with self.assertRaises(MemoryPersistenceError):
            MemoryCRUD.create_memory_candidate(
                self.db,
                self.legacy.legacy_id,
                self.user.user_id,
                self.candidate(
                    self.assistant_message,
                    "Thank you for sharing.",
                    speaker="assistant",
                ),
            )

    def test_direct_crud_rejects_fabricated_excerpt(self):
        with self.assertRaises(MemoryPersistenceError):
            MemoryCRUD.create_memory_candidate(
                self.db,
                self.legacy.legacy_id,
                self.user.user_id,
                self.candidate(self.user_message, "Not in the source"),
            )

    def test_direct_crud_accepts_verified_user_excerpt(self):
        memory = MemoryCRUD.create_memory_candidate(
            self.db,
            self.legacy.legacy_id,
            self.user.user_id,
            self.candidate(self.user_message, "grew up in Pune"),
        )
        self.assertEqual(len(memory.provenance), 1)

    def test_story_stream_namespaces_user_and_assistant_correlations(self):
        source = inspect.getsource(story_memory.stream_persisted_story)
        self.assertIn('user_key = f"user:', source)
        self.assertIn('assistant_key = f"assistant:', source)

    def test_stream_failure_logging_does_not_include_raw_exception(self):
        source = inspect.getsource(story_memory.stream_persisted_story)
        self.assertNotIn("logger.exception", source)
        self.assertIn('"error_category": "story_stream_failed"', source)

    def test_partial_pipeline_failure_marks_run_failed(self):
        source = inspect.getsource(
            background_extraction.execute_story_extraction
        )
        self.assertIn("if report.errors", source)
        self.assertIn("partial_persistence_failure", source)

    def test_story_sequence_collision_has_bounded_retry(self):
        source = inspect.getsource(
            StorySessionCRUD.append_story_message
        )
        self.assertIn("if _attempt < 2", source)

    def test_companion_grounding_does_not_change_story_or_extraction(self):
        from app.services.chat_service import ChatService
        source = inspect.getsource(ChatService)
        self.assertIn("MemoryRetrievalService", source)
        companion_source = source.split("prepare_ai_input")[1].split(
            "stream_story_response"
        )[0]
        self.assertNotIn("build_story_messages", companion_source)
        self.assertNotIn("MemoryExtractionService", source)
