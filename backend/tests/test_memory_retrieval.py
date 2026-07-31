"""Phase 6.7.1 approved-memory retrieval foundation tests."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.v1.memory import retrieve_approved_memories
from app.db import Base
from app.dependencies.auth import get_current_user
from app.models.memory import (
    Legacy,
    Memory,
    MemoryReviewStatus,
    MemoryType,
)
from app.models.user import User
from app.services.memory.retrieval import (
    MemoryRetrievalNotFoundError,
    MemoryRetrievalService,
)


class MemoryRetrievalTests(unittest.TestCase):
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
            email="retrieval-owner@example.test",
            password_hash="hash",
        )
        self.other = User(
            full_name="Other",
            email="retrieval-other@example.test",
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
        self.db.commit()
        self.service = MemoryRetrievalService()
        self.now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.db.close()

    def memory(
        self,
        *,
        legacy=None,
        status=MemoryReviewStatus.APPROVED,
        memory_type=MemoryType.ATOMIC,
        importance=3,
        summary="Mom loved jasmine.",
        updated_at=None,
    ):
        item = Memory(
            legacy_id=(legacy or self.legacy).legacy_id,
            memory_type=memory_type,
            category="preference",
            title=summary[:80],
            summary=summary,
            importance=importance,
            extraction_confidence=Decimal("0.875"),
            review_status=status,
            created_at=self.now - timedelta(days=1),
            updated_at=updated_at or self.now,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def retrieve(self):
        self.db.commit()
        return self.service.retrieve_approved(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
        )

    def test_only_approved_memories_are_returned(self):
        approved = self.memory(summary="Approved")
        self.memory(
            status=MemoryReviewStatus.CANDIDATE,
            summary="Candidate",
        )
        self.memory(
            status=MemoryReviewStatus.REJECTED,
            summary="Rejected",
        )
        replacement = self.memory(summary="Replacement")
        superseded = self.memory(
            status=MemoryReviewStatus.CANDIDATE,
            summary="Superseded",
        )
        superseded.review_status = MemoryReviewStatus.SUPERSEDED
        superseded.superseded_by_memory_id = replacement.memory_id

        result = self.retrieve()

        self.assertEqual(
            [item.memory_id for item in result.memories],
            [approved.memory_id, replacement.memory_id],
        )
        self.assertEqual(result.approved_memory_count, 2)

    def test_atomic_and_narrative_memories_are_normalized(self):
        atomic = self.memory(
            memory_type=MemoryType.ATOMIC,
            summary="Atomic fact",
        )
        narrative = self.memory(
            memory_type=MemoryType.NARRATIVE,
            summary="Narrative memory",
        )

        result = self.retrieve()

        by_id = {item.memory_id: item for item in result.memories}
        self.assertEqual(by_id[atomic.memory_id].memory_type, MemoryType.ATOMIC)
        self.assertEqual(
            by_id[narrative.memory_id].memory_type,
            MemoryType.NARRATIVE,
        )
        self.assertEqual(by_id[atomic.memory_id].category, "preference")
        self.assertEqual(by_id[atomic.memory_id].importance, 3)
        self.assertEqual(
            by_id[atomic.memory_id].extraction_confidence,
            Decimal("0.875"),
        )
        self.assertIsNotNone(by_id[atomic.memory_id].created_at)
        self.assertIsNotNone(by_id[atomic.memory_id].updated_at)

    def test_ordering_is_importance_then_updated_time_then_id(self):
        lower = self.memory(
            importance=2,
            summary="Lower importance",
            updated_at=self.now + timedelta(days=2),
        )
        older_high = self.memory(
            importance=5,
            summary="Older high importance",
            updated_at=self.now,
        )
        newer_high_first = self.memory(
            importance=5,
            summary="Newer high one",
            updated_at=self.now + timedelta(days=1),
        )
        newer_high_second = self.memory(
            importance=5,
            summary="Newer high two",
            updated_at=self.now + timedelta(days=1),
        )

        result = self.retrieve()

        self.assertEqual(
            [item.memory_id for item in result.memories],
            [
                newer_high_first.memory_id,
                newer_high_second.memory_id,
                older_high.memory_id,
                lower.memory_id,
            ],
        )

    def test_empty_legacy_returns_empty_contract(self):
        result = self.retrieve()
        self.assertEqual(result.legacy_id, self.legacy.legacy_id)
        self.assertEqual(result.approved_memory_count, 0)
        self.assertEqual(result.memories, [])

    def test_cross_owner_and_missing_legacy_are_not_found(self):
        for user_id, legacy_id in (
            (self.other.user_id, self.legacy.legacy_id),
            (self.owner.user_id, 999999),
        ):
            with self.subTest(user_id=user_id, legacy_id=legacy_id):
                with self.assertRaises(MemoryRetrievalNotFoundError):
                    self.service.retrieve_approved(
                        self.db,
                        user_id=user_id,
                        legacy_id=legacy_id,
                    )

    def test_route_uses_same_neutral_404(self):
        for current_user, legacy_id in (
            (self.other, self.legacy.legacy_id),
            (self.owner, 999999),
        ):
            with self.subTest(legacy_id=legacy_id):
                with self.assertRaises(HTTPException) as context:
                    retrieve_approved_memories(
                        legacy_id,
                        current_user=current_user,
                        db=self.db,
                        service=self.service,
                    )
                self.assertEqual(context.exception.status_code, 404)
                self.assertEqual(
                    context.exception.detail,
                    "Legacy was not found.",
                )

    def test_unauthenticated_dependency_is_rejected(self):
        with self.assertRaises(HTTPException) as context:
            get_current_user(credentials=None, db=self.db)
        self.assertEqual(context.exception.status_code, 401)

    def test_response_excludes_review_and_internal_metadata(self):
        self.memory(summary="Safe projection")
        result = self.retrieve().model_dump()

        self.assertEqual(
            set(result),
            {"legacy_id", "approved_memory_count", "memories"},
        )
        self.assertEqual(
            set(result["memories"][0]),
            {
                "memory_id",
                "memory_type",
                "category",
                "title",
                "summary",
                "importance",
                "extraction_confidence",
                "created_at",
                "updated_at",
            },
        )
        for hidden in (
            "review_status",
            "reviewed_at",
            "reviewed_by_user_id",
            "normalized_fingerprint",
            "provenance",
        ):
            self.assertNotIn(hidden, result["memories"][0])


if __name__ == "__main__":
    unittest.main()
