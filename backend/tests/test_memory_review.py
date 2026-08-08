"""Focused service and contract tests for human Memory review."""

import unittest
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.crud.memory import MemoryCRUD
from app.db import Base
from app.dependencies.ai import get_memory_storage_pipeline
from app.models.memory import (
    Legacy,
    Memory,
    MemoryLink,
    MemoryReviewStatus,
    MemoryRevision,
    MemoryType,
    StoryMessage,
    StoryMessageRole,
    StorySession,
)
from app.models.user import User
from app.schemas.memory import (
    MemoryCandidateCreate,
    MemoryParticipantCreate,
    MemoryProvenanceCreate,
    MemoryReviewEditRequest,
)
from app.services.memory.review import (
    MemoryReviewConflictError,
    MemoryReviewDuplicateError,
    MemoryReviewNotFoundError,
    MemoryReviewService,
)


class MemoryReviewTests(unittest.TestCase):
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
            email="review-owner@example.test",
            password_hash="hash",
        )
        self.other = User(
            full_name="Other",
            email="review-other@example.test",
            password_hash="hash",
        )
        self.db.add_all([self.owner, self.other])
        self.db.flush()
        self.legacy = Legacy(
            owner_user_id=self.owner.user_id,
            display_name="Mom",
            relationship="mother",
        )
        self.other_legacy = Legacy(
            owner_user_id=self.other.user_id,
            display_name="Dad",
            relationship="father",
        )
        self.db.add_all([self.legacy, self.other_legacy])
        self.db.flush()
        self.story = StorySession(
            legacy_id=self.legacy.legacy_id,
            chapter_key="childhood",
            title="Childhood",
            created_by_user_id=self.owner.user_id,
        )
        self.other_story = StorySession(
            legacy_id=self.other_legacy.legacy_id,
            chapter_key="childhood",
            title="Childhood",
            created_by_user_id=self.other.user_id,
        )
        self.db.add_all([self.story, self.other_story])
        self.db.flush()
        self.message = StoryMessage(
            story_session_id=self.story.story_session_id,
            role=StoryMessageRole.USER,
            content="I was born in Pune in 1968 and loved teaching.",
            sequence=1,
        )
        self.other_message = StoryMessage(
            story_session_id=self.other_story.story_session_id,
            role=StoryMessageRole.USER,
            content="I grew up in Berlin.",
            sequence=1,
        )
        self.db.add_all([self.message, self.other_message])
        self.db.commit()
        self.service = MemoryReviewService()

    def tearDown(self):
        self.db.close()

    def candidate(
        self,
        summary="Mom was born in Pune in 1968.",
        *,
        legacy=None,
        story=None,
        message=None,
        excerpt="I was born in Pune in 1968",
    ):
        legacy = legacy or self.legacy
        story = story or self.story
        message = message or self.message
        return MemoryCandidateCreate(
            memory_type=MemoryType.ATOMIC,
            category="personal_detail",
            title="Birthplace",
            summary=summary,
            importance=5,
            extraction_confidence=Decimal("0.910"),
            participants=[
                MemoryParticipantCreate(
                    name=legacy.display_name,
                    relationship=legacy.relationship,
                    role="subject",
                )
            ],
            tags=["family"],
            provenance=[
                MemoryProvenanceCreate(
                    source_type="story_session",
                    story_session_id=story.story_session_id,
                    story_message_id=message.story_message_id,
                    excerpt=excerpt,
                    speaker="user",
                    chapter="Childhood",
                )
            ],
        )

    def create_memory(self, candidate=None, *, other=False):
        legacy = self.other_legacy if other else self.legacy
        user = self.other if other else self.owner
        if candidate is None:
            candidate = (
                self.candidate(
                    "Dad grew up in Berlin.",
                    legacy=self.other_legacy,
                    story=self.other_story,
                    message=self.other_message,
                    excerpt="I grew up in Berlin",
                )
                if other
                else self.candidate()
            )
        return MemoryCRUD.create_memory_candidate(
            self.db,
            legacy.legacy_id,
            user.user_id,
            candidate,
        )

    def list_pending(self, user=None, legacy=None):
        return self.service.list_memories(
            self.db,
            user_id=(user or self.owner).user_id,
            legacy_id=(legacy or self.legacy).legacy_id,
        )

    def edit_request(self, memory, **changes):
        return MemoryReviewEditRequest(
            expected_updated_at=memory.updated_at,
            **changes,
        )

    def test_owner_can_list_candidate_memories(self):
        self.create_memory()
        items, total = self.list_pending()
        self.assertEqual(total, 1)
        self.assertEqual(len(items), 1)

    def test_other_legacy_memories_are_excluded(self):
        self.create_memory()
        self.create_memory(other=True)
        items, _ = self.list_pending()
        self.assertEqual([item.title for item in items], ["Birthplace"])

    def test_unauthorized_owner_cannot_access_legacy(self):
        self.create_memory()
        with self.assertRaises(MemoryReviewNotFoundError):
            self.list_pending(user=self.other)

    def test_candidate_can_be_approved(self):
        memory = self.create_memory()
        result = self.service.approve(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=memory.memory_id,
            expected_updated_at=memory.updated_at,
        )
        self.assertEqual(result.review_status, MemoryReviewStatus.APPROVED)

    def test_candidate_can_be_rejected(self):
        memory = self.create_memory()
        result = self.service.reject(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=memory.memory_id,
            expected_updated_at=memory.updated_at,
        )
        self.assertEqual(result.review_status, MemoryReviewStatus.REJECTED)

    def test_approved_is_not_in_default_pending_list(self):
        memory = self.create_memory()
        self.service.approve(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=memory.memory_id,
            expected_updated_at=memory.updated_at,
        )
        self.assertEqual(self.list_pending()[1], 0)

    def test_rejected_is_not_in_default_pending_list(self):
        memory = self.create_memory()
        self.service.reject(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=memory.memory_id,
            expected_updated_at=memory.updated_at,
        )
        self.assertEqual(self.list_pending()[1], 0)

    def test_edit_creates_revision(self):
        memory = self.create_memory()
        self.service.edit(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=memory.memory_id,
            edit=self.edit_request(memory, title="Birth in Pune"),
        )
        self.assertEqual(self.db.query(MemoryRevision).count(), 1)

    def test_original_extracted_form_remains_recoverable(self):
        memory = self.create_memory()
        original = memory.summary
        self.service.edit(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=memory.memory_id,
            edit=self.edit_request(
                memory, summary="Mom was born in Pune."
            ),
        )
        revision = self.db.query(MemoryRevision).one()
        self.assertEqual(revision.previous_content["summary"], original)

    def test_edit_contract_cannot_edit_provenance(self):
        self.assertNotIn(
            "provenance",
            MemoryReviewEditRequest.model_fields,
        )

    def test_edit_contract_cannot_edit_legacy_ownership(self):
        self.assertNotIn(
            "legacy_id",
            MemoryReviewEditRequest.model_fields,
        )

    def test_duplicate_edit_returns_controlled_conflict(self):
        first = self.create_memory()
        second = self.create_memory(
            self.candidate(
                "Mom loved teaching.",
                excerpt="loved teaching",
            )
        )
        with self.assertRaises(MemoryReviewDuplicateError):
            self.service.edit(
                self.db,
                user_id=self.owner.user_id,
                legacy_id=self.legacy.legacy_id,
                memory_id=second.memory_id,
                edit=self.edit_request(
                    second,
                    title=first.title,
                    summary=first.summary,
                    participants=[
                        MemoryParticipantCreate(
                            name="Mom",
                            relationship="mother",
                            role="subject",
                        )
                    ],
                ),
            )

    def test_fingerprint_regenerates_after_edit(self):
        memory = self.create_memory()
        old = memory.normalized_fingerprint
        self.service.edit(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=memory.memory_id,
            edit=self.edit_request(
                memory, summary="Mom was born in Pune."
            ),
        )
        self.assertNotEqual(memory.normalized_fingerprint, old)

    def test_approval_does_not_alter_provenance(self):
        memory = self.create_memory()
        ids = [item.provenance_id for item in memory.provenance]
        self.service.approve(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=memory.memory_id,
            expected_updated_at=memory.updated_at,
        )
        self.assertEqual(
            [item.provenance_id for item in memory.provenance], ids
        )

    def test_rejection_does_not_delete_provenance(self):
        memory = self.create_memory()
        self.service.reject(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=memory.memory_id,
            expected_updated_at=memory.updated_at,
        )
        self.assertEqual(len(memory.provenance), 1)

    def test_contradictory_memories_remain_independently_reviewable(self):
        first = self.create_memory()
        second = self.create_memory(
            self.candidate(
                "Mom was born in Pune in 1967.",
                excerpt="I was born in Pune",
            )
        )
        group = MemoryCRUD.get_or_create_contradiction_group_for_memories(
            self.db,
            self.legacy.legacy_id,
            [first.memory_id, second.memory_id],
            "Birth year",
        )
        self.db.commit()
        result = self.service.get_memory(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=first.memory_id,
        )
        self.assertEqual(group.contradiction_group_id, first.contradiction_group_id)
        self.assertTrue(result.has_contradiction)
        self.assertEqual(len(result.related_memories), 1)

    def test_possible_enrichment_does_not_modify_related_memory(self):
        first = self.create_memory()
        original = first.summary
        second = self.create_memory(
            self.candidate(
                "Mom loved teaching.",
                excerpt="loved teaching",
            )
        )
        MemoryCRUD.add_memory_link(
            self.db,
            self.legacy.legacy_id,
            second.memory_id,
            first.memory_id,
            "possible_enrichment",
        )
        self.db.commit()
        self.service.approve(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=second.memory_id,
            expected_updated_at=second.updated_at,
        )
        self.assertEqual(first.summary, original)

    def test_cross_legacy_link_data_is_not_exposed(self):
        memory = self.create_memory()
        other = self.create_memory(other=True)
        self.db.add(
            MemoryLink(
                legacy_id=self.legacy.legacy_id,
                source_memory_id=memory.memory_id,
                target_memory_id=other.memory_id,
                link_type="possible_enrichment",
            )
        )
        self.db.commit()
        result = self.service.get_memory(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=memory.memory_id,
        )
        self.assertEqual(result.related_memories, [])

    def test_stale_approval_is_detected(self):
        memory = self.create_memory()
        stale = memory.updated_at - timedelta(seconds=1)
        with self.assertRaises(MemoryReviewConflictError):
            self.service.approve(
                self.db,
                user_id=self.owner.user_id,
                legacy_id=self.legacy.legacy_id,
                memory_id=memory.memory_id,
                expected_updated_at=stale,
            )

    def test_stale_edit_is_detected(self):
        memory = self.create_memory()
        request = MemoryReviewEditRequest(
            expected_updated_at=memory.updated_at - timedelta(seconds=1),
            title="Changed",
        )
        with self.assertRaises(MemoryReviewConflictError):
            self.service.edit(
                self.db,
                user_id=self.owner.user_id,
                legacy_id=self.legacy.legacy_id,
                memory_id=memory.memory_id,
                edit=request,
            )

    def test_review_response_excludes_internal_fingerprint_and_json(self):
        memory = self.create_memory()
        response = self.service.get_memory(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            memory_id=memory.memory_id,
        ).model_dump()
        self.assertNotIn("normalized_fingerprint", response)
        self.assertNotIn("source_locator", str(response))

    def test_cached_pipeline_retains_no_request_state(self):
        pipeline = get_memory_storage_pipeline()
        attributes = vars(pipeline)
        self.assertNotIn("db", attributes)
        self.assertNotIn("user_id", attributes)
        self.assertNotIn("legacy_id", attributes)
        self.assertNotIn("source_id", attributes)

    def test_existing_pipeline_contract_remains_available(self):
        pipeline = get_memory_storage_pipeline()
        self.assertTrue(callable(pipeline.process_story_session))
        self.assertTrue(callable(pipeline.process_conversation))
