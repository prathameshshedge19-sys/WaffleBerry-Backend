"""Persistence integration tests for the Phase 6.5.5 storage pipeline."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register all mapped tables
from app.crud.memory import MemoryCRUD
from app.db import Base
from app.models.memory import (
    Legacy,
    LegacyStatus,
    Memory,
    MemoryContradictionGroup,
    MemoryLink,
    MemoryProvenance,
    MemoryReviewStatus,
    MemoryType,
    StoryMessage,
    StoryMessageRole,
    StorySession,
    StorySessionStatus,
)
from app.models.user import Conversation, Message, MessageRole, User
from app.schemas.memory import (
    MemoryCandidateCreate,
    MemoryDetails,
    MemoryParticipantCreate,
    MemoryProvenanceCreate,
    TemporalReference,
)
from app.services.memory.storage_exceptions import MemorySourceError
from app.services.memory.storage_pipeline import MemoryStoragePipeline
from app.services.memory.validation_contracts import (
    MemoryValidationAction,
    MemoryValidationResult,
    MemoryValidationStatus,
)


class FakeExtractionService:
    def __init__(self, candidates):
        self.candidates = candidates

    async def extract_story_session(self, legacy, story_session, messages):
        return list(self.candidates)

    async def extract_conversation(self, legacy, conversation, messages):
        return list(self.candidates)


class PossibleDuplicateValidationService:
    def validate_candidate(self, candidate, **kwargs):
        del kwargs
        return MemoryValidationResult(
            status=MemoryValidationStatus.POSSIBLE_DUPLICATE,
            recommended_action=MemoryValidationAction.REVIEW_LINK,
            explanation="Similar but not an exact duplicate.",
            validation_confidence=Decimal("0.700"),
            normalized_candidate=candidate,
        )

class MemoryStoragePipelineTests(unittest.IsolatedAsyncioTestCase):
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
            email="owner@example.test",
            password_hash="not-a-real-password",
        )
        self.other_user = User(
            full_name="Other",
            email="other@example.test",
            password_hash="not-a-real-password",
        )
        self.db.add_all([self.user, self.other_user])
        self.db.flush()
        self.legacy = Legacy(
            owner_user_id=self.user.user_id,
            display_name="Mom",
            relationship="mother",
        )
        self.other_legacy = Legacy(
            owner_user_id=self.other_user.user_id,
            display_name="Dad",
            relationship="father",
        )
        self.db.add_all([self.legacy, self.other_legacy])
        self.db.flush()
        self.story = StorySession(
            legacy_id=self.legacy.legacy_id,
            chapter_key="childhood",
            title="Childhood",
            created_by_user_id=self.user.user_id,
        )
        self.db.add(self.story)
        self.db.flush()
        self.user_story_message = StoryMessage(
            story_session_id=self.story.story_session_id,
            role=StoryMessageRole.USER,
            content="I was born in Pune in 1968 and taught mathematics.",
            sequence=1,
        )
        self.assistant_story_message = StoryMessage(
            story_session_id=self.story.story_session_id,
            role=StoryMessageRole.ASSISTANT,
            content="Thank you for sharing that.",
            sequence=2,
        )
        self.db.add_all(
            [self.user_story_message, self.assistant_story_message]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def candidate(
        self,
        *,
        summary="Mom was born in Pune in 1968.",
        excerpt="I was born in Pune in 1968",
        message=None,
        details=None,
        participants=True,
        confidence="0.910",
        uncertainty_note=None,
    ):
        source = message or self.user_story_message
        return MemoryCandidateCreate(
            memory_type=MemoryType.ATOMIC,
            category="personal_detail",
            title="A preserved memory",
            summary=summary,
            details=details or MemoryDetails(),
            importance=5,
            extraction_confidence=Decimal(confidence),
            uncertainty_note=uncertainty_note,
            participants=(
                [
                    MemoryParticipantCreate(
                        name="Mom",
                        relationship="mother",
                        role="subject",
                    )
                ]
                if participants
                else []
            ),
            tags=["family", "Family"],
            provenance=[
                MemoryProvenanceCreate(
                    source_type="story_session",
                    story_session_id=self.story.story_session_id,
                    story_message_id=source.story_message_id,
                    speaker=(
                        source.role.value
                        if hasattr(source.role, "value")
                        else str(source.role)
                    ),
                    excerpt=excerpt,
                    chapter="Childhood",
                )
            ],
        )

    async def run_story(self, candidates):
        return await MemoryStoragePipeline(
            FakeExtractionService(candidates)
        ).process_story_session(
            self.db,
            user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id,
            story_session_id=self.story.story_session_id,
        )

    def complete_story(self):
        self.story.status = StorySessionStatus.COMPLETED
        self.story.completed_at = datetime.now(timezone.utc)
        self.db.commit()

    async def test_accepted_candidate_is_persisted_as_candidate(self):
        report = await self.run_story([self.candidate()])
        memory = self.db.get(Memory, report.created_memory_ids[0])
        self.assertEqual(memory.review_status, MemoryReviewStatus.CANDIDATE)

    async def test_completed_story_threshold_candidate_is_approved(self):
        self.complete_story()
        report = await self.run_story([self.candidate(confidence="0.400")])
        memory = self.db.get(Memory, report.created_memory_ids[0])
        self.assertEqual(memory.review_status, MemoryReviewStatus.APPROVED)
        self.assertIsNotNone(memory.reviewed_at)
        self.assertEqual(memory.reviewed_by_user_id, self.user.user_id)

    async def test_completed_story_low_confidence_candidate_is_approved(self):
        self.complete_story()
        report = await self.run_story([self.candidate(confidence="0.010")])
        memory = self.db.get(Memory, report.created_memory_ids[0])
        self.assertEqual(memory.review_status, MemoryReviewStatus.APPROVED)
        self.assertEqual(memory.extraction_confidence, Decimal("0.010"))
        self.assertIsNotNone(memory.reviewed_at)

    async def test_completed_story_uncertain_candidate_is_approved_and_preserved(self):
        self.complete_story()
        report = await self.run_story([
            self.candidate(uncertainty_note="The year may be approximate.")
        ])
        memory = self.db.get(Memory, report.created_memory_ids[0])
        self.assertEqual(memory.review_status, MemoryReviewStatus.APPROVED)
        self.assertEqual(memory.uncertainty_note, "The year may be approximate.")
        self.assertEqual(memory.reviewed_by_user_id, self.user.user_id)

    async def test_completed_story_high_confidence_candidate_is_approved(self):
        self.complete_story()
        report = await self.run_story([self.candidate(confidence="0.950")])
        memory = self.db.get(Memory, report.created_memory_ids[0])
        self.assertEqual(memory.review_status, MemoryReviewStatus.APPROVED)
        self.assertEqual(memory.extraction_confidence, Decimal("0.950"))

    async def test_pronoun_resolved_memory_is_approved(self):
        self.complete_story()
        report = await self.run_story([
            self.candidate(
                summary="Makarand used to teach Mom maths.",
                excerpt="taught mathematics",
                confidence="0.930",
                uncertainty_note=(
                    "Makarand is resolved from the immediately preceding "
                    "Story context."
                ),
            )
        ])
        memory = self.db.get(Memory, report.created_memory_ids[0])
        self.assertEqual(memory.review_status, MemoryReviewStatus.APPROVED)
        self.assertIn("resolved", memory.uncertainty_note)

    async def test_story_created_by_another_user_stays_pending(self):
        self.complete_story()
        self.story.created_by_user_id = self.other_user.user_id
        self.db.commit()
        report = await self.run_story([self.candidate()])
        memory = self.db.get(Memory, report.created_memory_ids[0])
        self.assertEqual(memory.review_status, MemoryReviewStatus.CANDIDATE)

    async def test_archived_legacy_story_stays_pending(self):
        self.complete_story()
        self.legacy.status = LegacyStatus.ARCHIVED
        self.db.commit()
        report = await self.run_story([self.candidate()])
        memory = self.db.get(Memory, report.created_memory_ids[0])
        self.assertEqual(memory.review_status, MemoryReviewStatus.CANDIDATE)

    async def test_conversation_candidate_stays_pending(self):
        conversation = Conversation(
            user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id,
            title="Conversation source",
        )
        self.db.add(conversation)
        self.db.flush()
        message = Message(
            conversation_id=conversation.conversation_id,
            role=MessageRole.USER,
            content=self.user_story_message.content,
        )
        self.db.add(message)
        self.db.commit()
        candidate = self.candidate().model_copy(update={
            "provenance": [MemoryProvenanceCreate(
                source_type="conversation",
                conversation_id=conversation.conversation_id,
                message_id=message.message_id,
                speaker="user",
                excerpt="I was born in Pune in 1968",
            )]
        })
        report = await MemoryStoragePipeline(
            FakeExtractionService([candidate])
        ).process_conversation(
            self.db,
            user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id,
            conversation_id=conversation.conversation_id,
        )
        memory = self.db.get(Memory, report.created_memory_ids[0])
        self.assertEqual(memory.review_status, MemoryReviewStatus.CANDIDATE)

    async def test_retry_does_not_repeat_auto_approval(self):
        self.complete_story()
        first = await self.run_story([self.candidate()])
        memory = self.db.get(Memory, first.created_memory_ids[0])
        reviewed_at = memory.reviewed_at
        second = await self.run_story([self.candidate()])
        self.db.refresh(memory)
        self.assertEqual(second.memories_created, 0)
        self.assertEqual(self.db.query(Memory).count(), 1)
        self.assertEqual(memory.reviewed_at, reviewed_at)

    async def test_memory_and_provenance_are_persisted_together(self):
        report = await self.run_story([self.candidate()])
        self.assertEqual(report.memories_created, 1)
        self.assertEqual(
            self.db.query(MemoryProvenance).count(), 1
        )

    async def test_exact_duplicate_is_not_persisted(self):
        await self.run_story([self.candidate()])
        report = await self.run_story([self.candidate()])
        self.assertEqual(report.duplicates_skipped, 1)
        self.assertEqual(self.db.query(Memory).count(), 1)

    async def test_completed_story_possible_duplicate_is_persisted(self):
        self.complete_story()
        report = await MemoryStoragePipeline(
            FakeExtractionService([self.candidate()]),
            validation_service=PossibleDuplicateValidationService(),
        ).process_story_session(
            self.db,
            user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id,
            story_session_id=self.story.story_session_id,
        )
        memory = self.db.get(Memory, report.created_memory_ids[0])
        self.assertEqual(report.memories_created, 1)
        self.assertEqual(memory.review_status, MemoryReviewStatus.APPROVED)

    async def test_reprocessing_source_is_idempotent(self):
        first = await self.run_story([self.candidate()])
        second = await self.run_story([self.candidate()])
        self.assertEqual(first.memories_created, 1)
        self.assertEqual(second.memories_created, 0)

    async def test_invalid_assistant_provenance_is_skipped(self):
        candidate = self.candidate(
            summary="Berry thanked Mom.",
            excerpt="Thank you for sharing that.",
            message=self.assistant_story_message,
        )
        report = await self.run_story([candidate])
        self.assertEqual(report.invalid_candidates_skipped, 1)
        self.assertEqual(self.db.query(Memory).count(), 0)

    async def test_insufficient_information_is_skipped(self):
        candidate = self.candidate(
            summary="It was nice.",
            excerpt="I was born",
            participants=False,
        )
        report = await self.run_story([candidate])
        self.assertEqual(report.insufficient_candidates_skipped, 1)

    async def test_contradiction_preserves_both_claims(self):
        self.complete_story()
        details_1968 = MemoryDetails(
            temporal_references=[
                TemporalReference(
                    text="1968",
                    start_date="1968-01-01",
                    end_date="1968-12-31",
                    precision="year",
                )
            ]
        )
        details_1967 = MemoryDetails(
            temporal_references=[
                TemporalReference(
                    text="1967",
                    start_date="1967-01-01",
                    end_date="1967-12-31",
                    precision="year",
                )
            ]
        )
        await self.run_story(
            [self.candidate(details=details_1968)]
        )
        report = await self.run_story(
            [
                self.candidate(
                    summary="Mom was born in Pune in 1967.",
                    details=details_1967,
                )
            ]
        )
        memories = self.db.query(Memory).order_by(Memory.memory_id).all()
        self.assertEqual(report.contradictions_persisted, 1)
        self.assertEqual(len(memories), 2)
        self.assertEqual(memories[0].summary, "Mom was born in Pune in 1968.")
        self.assertEqual(
            memories[0].contradiction_group_id,
            memories[1].contradiction_group_id,
        )
        self.assertEqual(memories[0].review_status, MemoryReviewStatus.APPROVED)
        self.assertEqual(memories[1].review_status, MemoryReviewStatus.APPROVED)

    async def test_contradiction_group_is_reused(self):
        await self.test_contradiction_preserves_both_claims()
        groups_before = self.db.query(MemoryContradictionGroup).count()
        details = MemoryDetails(
            temporal_references=[
                TemporalReference(text="1966", precision="year")
            ]
        )
        await self.run_story(
            [
                self.candidate(
                    summary="Mom was born in Pune in 1966.",
                    details=details,
                )
            ]
        )
        self.assertEqual(
            self.db.query(MemoryContradictionGroup).count(),
            groups_before,
        )

    async def test_possible_enrichment_does_not_modify_existing(self):
        await self.run_story(
            [
                self.candidate(
                    summary="Mom taught mathematics.",
                    excerpt="taught mathematics",
                )
            ]
        )
        original = self.db.query(Memory).one()
        original_summary = original.summary
        report = await self.run_story(
            [
                self.candidate(
                    summary="Mom taught mathematics in Pune.",
                    excerpt="taught mathematics",
                )
            ]
        )
        self.assertEqual(original.summary, original_summary)
        self.assertEqual(report.possible_enrichments_persisted, 1)
        self.assertEqual(self.db.query(MemoryLink).count(), 1)

    async def test_cross_legacy_story_source_is_rejected(self):
        with self.assertRaises(MemorySourceError):
            await MemoryStoragePipeline(
                FakeExtractionService([])
            ).process_story_session(
                self.db,
                user_id=self.other_user.user_id,
                legacy_id=self.other_legacy.legacy_id,
                story_session_id=self.story.story_session_id,
            )

    async def test_cross_legacy_related_memory_is_rejected_by_crud(self):
        other = Memory(
            legacy_id=self.other_legacy.legacy_id,
            memory_type=MemoryType.ATOMIC,
            category="personal_detail",
            title="Other",
            summary="Other legacy memory.",
            review_status=MemoryReviewStatus.CANDIDATE,
        )
        self.db.add(other)
        self.db.commit()
        with self.assertRaises(Exception):
            with self.db.begin_nested():
                MemoryCRUD.add_memory_link(
                    self.db,
                    self.legacy.legacy_id,
                    other.memory_id,
                    other.memory_id,
                    "possible_enrichment",
                )

    async def test_assistant_messages_cannot_be_factual_provenance(self):
        await self.test_invalid_assistant_provenance_is_skipped()

    async def test_provenance_failure_rolls_back_only_candidate(self):
        with patch.object(
            MemoryCRUD,
            "_attach_tags",
            side_effect=RuntimeError("simulated"),
        ):
            report = await self.run_story([self.candidate()])
        self.assertEqual(report.memories_created, 0)
        self.assertEqual(self.db.query(Memory).count(), 0)
        self.assertEqual(self.db.query(MemoryProvenance).count(), 0)

    async def test_multiple_valid_candidates_are_stored(self):
        second = self.candidate(
            summary="Mom taught mathematics.",
            excerpt="taught mathematics",
        )
        report = await self.run_story([self.candidate(), second])
        self.assertEqual(report.memories_created, 2)

    async def test_zero_candidates_returns_successful_empty_report(self):
        report = await self.run_story([])
        self.assertEqual(report.candidates_extracted, 0)
        self.assertEqual(report.errors, [])

    async def test_unassociated_conversation_cannot_be_processed(self):
        conversation = Conversation(
            user_id=self.user.user_id,
            legacy_id=None,
            title="Old chat",
        )
        self.db.add(conversation)
        self.db.commit()
        with self.assertRaises(MemorySourceError):
            await MemoryStoragePipeline(
                FakeExtractionService([])
            ).process_conversation(
                self.db,
                user_id=self.user.user_id,
                legacy_id=self.legacy.legacy_id,
                conversation_id=conversation.conversation_id,
            )

    async def test_extraction_and_validation_confidence_are_distinct(self):
        report = await self.run_story(
            [self.candidate(confidence="0.610")]
        )
        self.assertEqual(
            report.items[0].extraction_confidence, Decimal("0.610")
        )
        self.assertEqual(
            report.items[0].validation_confidence, Decimal("0.800")
        )

    async def test_no_memory_is_automatically_approved(self):
        await self.run_story(
            [
                self.candidate(),
                self.candidate(
                    summary="Mom taught mathematics.",
                    excerpt="taught mathematics",
                ),
            ]
        )
        self.assertEqual(
            self.db.query(Memory)
            .filter(Memory.review_status == MemoryReviewStatus.APPROVED)
            .count(),
            0,
        )
