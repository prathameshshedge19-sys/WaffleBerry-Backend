"""Regression coverage for the Guided Story candidate data backfill."""

import importlib.util
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base
from app.models.memory import (
    Legacy,
    Memory,
    MemoryProvenance,
    MemoryReviewStatus,
    MemoryType,
    StoryMessage,
    StoryMessageRole,
    StorySession,
    StorySessionStatus,
)
from app.models.user import User


def _migration_module():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0006_auto_approve_guided_story_memories.py"
    )
    spec = importlib.util.spec_from_file_location("story_backfill_0006", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GuidedStoryCandidateBackfillTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        owner = User(
            full_name="Owner",
            email="owner@example.test",
            password_hash="test-only",
        )
        other = User(
            full_name="Other",
            email="other@example.test",
            password_hash="test-only",
        )
        self.db.add_all([owner, other])
        self.db.flush()
        legacy = Legacy(
            owner_user_id=owner.user_id,
            display_name="Mom",
            relationship="mother",
        )
        other_legacy = Legacy(
            owner_user_id=other.user_id,
            display_name="Other",
            relationship="other",
        )
        self.db.add_all([legacy, other_legacy])
        self.db.flush()
        story = StorySession(
            legacy_id=legacy.legacy_id,
            chapter_key="family",
            title="Family",
            status=StorySessionStatus.COMPLETED,
            created_by_user_id=owner.user_id,
        )
        other_story = StorySession(
            legacy_id=other_legacy.legacy_id,
            chapter_key="family",
            title="Other family",
            status=StorySessionStatus.COMPLETED,
            created_by_user_id=other.user_id,
        )
        self.db.add_all([story, other_story])
        self.db.flush()
        source = StoryMessage(
            story_session_id=story.story_session_id,
            role=StoryMessageRole.USER,
            content=(
                "Makarand used to teach Mom maths and play hide and seek."
            ),
            sequence=1,
        )
        assistant = StoryMessage(
            story_session_id=story.story_session_id,
            role=StoryMessageRole.ASSISTANT,
            content="Thank you for sharing that.",
            sequence=2,
        )
        other_source = StoryMessage(
            story_session_id=other_story.story_session_id,
            role=StoryMessageRole.USER,
            content="Unrelated other Legacy evidence.",
            sequence=1,
        )
        self.db.add_all([source, assistant, other_source])
        self.db.flush()

        self._memory(
            25,
            legacy.legacy_id,
            "Makarand used to teach Mom maths.",
            Decimal("0.930"),
            story,
            source,
            "Makarand used to teach Mom maths",
            uncertainty="Pronoun resolved from nearby Story context.",
        )
        self._memory(
            26,
            legacy.legacy_id,
            "Makarand used to play hide and seek with Mom.",
            Decimal("0.930"),
            story,
            source,
            "play hide and seek",
            uncertainty="Pronoun resolved from nearby Story context.",
        )
        self._memory(
            27,
            legacy.legacy_id,
            "Below threshold.",
            Decimal("0.399"),
            story,
            source,
            "teach Mom maths",
        )
        self._memory(
            28,
            legacy.legacy_id,
            "Assistant-only evidence.",
            Decimal("0.990"),
            story,
            assistant,
            "Thank you for sharing that",
        )
        self._memory(
            29,
            legacy.legacy_id,
            "Cross-Legacy evidence.",
            Decimal("0.990"),
            other_story,
            other_source,
            "Unrelated other Legacy evidence",
        )
        rejected = self._memory(
            30,
            legacy.legacy_id,
            "Already rejected.",
            Decimal("0.990"),
            story,
            source,
            "teach Mom maths",
        )
        rejected.review_status = MemoryReviewStatus.REJECTED
        self.db.commit()

    def _memory(
        self,
        memory_id,
        legacy_id,
        summary,
        confidence,
        story,
        message,
        excerpt,
        *,
        uncertainty=None,
    ):
        memory = Memory(
            memory_id=memory_id,
            legacy_id=legacy_id,
            memory_type=MemoryType.ATOMIC,
            category="relationship",
            title=f"Memory {memory_id}",
            summary=summary,
            extraction_confidence=confidence,
            review_status=MemoryReviewStatus.CANDIDATE,
            uncertainty_note=uncertainty,
        )
        self.db.add(memory)
        self.db.flush()
        self.db.add(
            MemoryProvenance(
                memory_id=memory.memory_id,
                source_type="story_session",
                story_session_id=story.story_session_id,
                story_message_id=message.story_message_id,
                excerpt=excerpt,
                speaker=(
                    message.role.value
                    if hasattr(message.role, "value")
                    else str(message.role)
                ),
                chapter=story.chapter_key,
            )
        )
        return memory

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_backfill_approves_25_and_26_safely_and_is_idempotent(self):
        migration = _migration_module()
        with self.engine.begin() as connection:
            with patch.object(migration.op, "get_bind", return_value=connection):
                migration.upgrade()
        self.db.expire_all()
        first_reviewed_at = self.db.get(Memory, 25).reviewed_at

        with self.engine.begin() as connection:
            with patch.object(migration.op, "get_bind", return_value=connection):
                migration.upgrade()
        self.db.expire_all()

        for memory_id in (25, 26):
            memory = self.db.get(Memory, memory_id)
            self.assertEqual(memory.review_status, MemoryReviewStatus.APPROVED)
            self.assertIsNotNone(memory.reviewed_at)
            self.assertIsNotNone(memory.reviewed_by_user_id)
            self.assertIn("Pronoun resolved", memory.uncertainty_note)
        self.assertEqual(self.db.get(Memory, 25).reviewed_at, first_reviewed_at)
        for memory_id in (27, 28, 29):
            self.assertEqual(
                self.db.get(Memory, memory_id).review_status,
                MemoryReviewStatus.CANDIDATE,
            )
        self.assertEqual(
            self.db.get(Memory, 30).review_status,
            MemoryReviewStatus.REJECTED,
        )


if __name__ == "__main__":
    unittest.main()
