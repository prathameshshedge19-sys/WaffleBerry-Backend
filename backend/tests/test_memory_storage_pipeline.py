"""Persistence integration tests for the Phase 6.5.5 storage pipeline."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register all mapped tables
from app.crud.memory import MemoryCRUD
from app.db import Base
from app.models.memory import (
    Legacy,
    LegacyIdentityFact,
    LegacyStatus,
    Memory,
    MemoryContradictionGroup,
    MemoryLink,
    MemoryProvenance,
    MemoryReviewStatus,
    MemoryRevision,
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
from app.services.memory.review import MemoryReviewService
from app.services.memory.storage_contracts import MemoryOperation
from app.services.ai.context_builder import ContextBuilder
from app.services.chat_service import ChatService
from app.services.realtime_live_call import RealtimeToolService
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

    async def extract_live_call_turn(self, legacy, **kwargs):
        del legacy, kwargs
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

    async def run_auto_chat(self, candidate, text):
        conversation = Conversation(
            user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id,
            title="Automatic learning source",
        )
        self.db.add(conversation)
        self.db.flush()
        message = Message(
            conversation_id=conversation.conversation_id,
            role=MessageRole.USER,
            content=text,
        )
        self.db.add(message)
        self.db.commit()
        candidate = candidate.model_copy(update={
            "provenance": [MemoryProvenanceCreate(
                source_type="conversation",
                conversation_id=conversation.conversation_id,
                message_id=message.message_id,
                speaker="user",
                excerpt=text,
            )]
        })
        return await MemoryStoragePipeline(
            FakeExtractionService([candidate])
        ).process_conversation(
            self.db,
            user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id,
            conversation_id=conversation.conversation_id,
            metadata={"auto_learned": True},
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

    async def test_chat_enriches_one_normal_dog_memory_in_place_for_both_modalities(self):
        self.complete_story()
        self.user_story_message.content = (
            "The family dog was remembered, but its name and breed were not given."
        )
        self.db.commit()
        original_candidate = self.candidate(
            summary="The family dog was remembered, but its name and breed were not given.",
            excerpt="The family dog was remembered, but its name and breed were not given.",
        ).model_copy(update={
            "title": "Family dog",
            "category": "relationship",
            "tags": ["family", "dog", "pet"],
        })
        first = await self.run_story([original_candidate])
        memory_id = first.created_memory_ids[0]

        enriched_candidate = self.candidate(
            summary="The family dog was a Labrador named Bruno.",
            excerpt="The family dog was a Labrador named Bruno.",
            details=MemoryDetails(pet={
                "species": "dog", "breed": "Labrador", "name": "Bruno",
            }),
        ).model_copy(update={
            "title": "Family Labrador named Bruno",
            "category": "relationship",
            "tags": ["family", "dog", "pet", "labrador", "bruno"],
        })
        report = await self.run_auto_chat(
            enriched_candidate, "The family dog was a Labrador named Bruno."
        )

        memories = self.db.query(Memory).all()
        self.assertEqual(len(memories), 1, report.validation_status_counts)
        current = self.db.get(Memory, memory_id)
        self.assertEqual(report.possible_enrichments_persisted, 1)
        self.assertEqual(report.items[0].operation, MemoryOperation.ENRICH)
        self.assertEqual(current.summary, "The family dog was a Labrador named Bruno.")
        self.assertEqual(current.title, "Family Labrador named Bruno")
        self.assertEqual(current.details["pet"]["breed"], "Labrador")
        self.assertEqual(current.details["pet"]["name"], "Bruno")
        self.assertEqual(self.db.query(MemoryRevision).filter_by(memory_id=memory_id).count(), 1)
        self.assertEqual(len(current.provenance), 2)

        chat = ChatService(SimpleNamespace(), ContextBuilder(12))
        prepared = chat.prepare_live_call_input(
            self.db, user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id, legacy_name="Mom",
            relationship="mother", user_message="What was your dog's name and breed?",
            history=(),
        )
        self.assertIn(memory_id, prepared.memory_ids)
        self.assertIn("Bruno", prepared.memory_evidence[0]["summary"])
        self.assertIn("Labrador", prepared.memory_evidence[0]["summary"])
        session = SimpleNamespace(
            session_id="dog-enrichment", user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id, legacy_name="Mom",
            relationship="mother", conversation_id=None,
        )
        tools = RealtimeToolService(chat)
        tools.route_turn(session, 1, "What was your dog's name and breed?")
        live = tools.execute(
            self.db, session, "retrieve_legacy_memory_context", {}, turn_id=1,
        )
        self.assertIn("Bruno", live["memories"][0]["summary"])
        self.assertIn("Labrador", live["memories"][0]["summary"])

        correction = self.candidate(
            summary="The family Labrador was actually named Luffy.",
            excerpt="Actually his name was Luffy.",
            details=MemoryDetails(pet={
                "species": "dog", "breed": "Labrador", "name": "Luffy",
            }),
        ).model_copy(update={
            "title": "Family Labrador named Luffy",
            "category": "relationship",
            "tags": ["family", "dog", "pet", "labrador", "luffy"],
        })
        corrected = await self.run_auto_chat(
            correction, "Actually his name was Luffy."
        )
        self.db.expire_all()
        current = self.db.get(Memory, memory_id)
        self.assertEqual(corrected.possible_enrichments_persisted, 1)
        self.assertEqual(corrected.items[0].operation, MemoryOperation.CORRECT)
        self.assertEqual(self.db.query(Memory).count(), 1)
        self.assertEqual(current.details["pet"]["name"], "Luffy")
        self.assertIn("Luffy", current.summary)
        self.assertNotIn("Bruno", current.summary)
        self.assertEqual(self.db.query(MemoryRevision).filter_by(memory_id=memory_id).count(), 2)

    async def test_multiple_dogs_are_not_collapsed_into_one_enrichment(self):
        self.complete_story()
        self.user_story_message.content = "The family had a dog named Luffy."
        self.db.commit()
        original = self.candidate(
            summary="The family had a dog named Luffy.",
            excerpt="The family had a dog named Luffy.",
        ).model_copy(update={
            "title": "Dog named Luffy", "category": "relationship",
            "tags": ["family", "dog", "pet", "luffy"],
        })
        await self.run_story([original])
        candidate = self.candidate(
            summary="The family had two dogs, Luffy and Bruno.",
            excerpt="The family had two dogs, Luffy and Bruno.",
        ).model_copy(update={
            "title": "Dogs Luffy and Bruno", "category": "relationship",
            "tags": ["family", "dog", "pet", "luffy", "bruno"],
        })
        await self.run_auto_chat(
            candidate, "The family had two dogs, Luffy and Bruno."
        )
        self.assertEqual(self.db.query(Memory).count(), 2)

    async def test_another_named_entity_is_added_and_broadly_grounded(self):
        self.complete_story()
        self.user_story_message.content = "Bruno is our Labrador."
        self.db.commit()
        bruno = self.candidate(
            summary="Bruno is our Labrador.", excerpt="Bruno is our Labrador.",
            details=MemoryDetails(entity={
                "type": "dog", "name": "Bruno", "breed": "Labrador",
            }),
        ).model_copy(update={
            "title": "Dog named Bruno", "category": "relationship",
            "tags": ["family", "dog", "pet", "labrador", "bruno"],
        })
        await self.run_story([bruno])
        luffy = self.candidate(
            summary="The family has another dog named Luffy, who is also a Labrador.",
            excerpt="We have another dog named Luffy. He is also a Labrador.",
            details=MemoryDetails(entity={
                "type": "dog", "name": "Luffy", "breed": "Labrador",
            }),
        ).model_copy(update={
            "title": "Dog named Luffy", "category": "relationship",
            "tags": ["family", "dog", "pet", "labrador", "luffy"],
        })
        report = await self.run_auto_chat(
            luffy, "We have another dog named Luffy. He is also a Labrador."
        )
        self.assertEqual(report.items[0].operation, MemoryOperation.ADD_ENTITY)
        self.assertEqual(report.operation_counts, {"add_entity": 1})
        self.assertEqual(self.db.query(Memory).count(), 2)
        approved, total = MemoryReviewService().list_memories(
            self.db, user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id,
            review_status=MemoryReviewStatus.APPROVED,
        )
        self.assertEqual(total, 2)
        dashboard_text = " ".join(f"{item.title} {item.summary}" for item in approved)
        self.assertIn("Bruno", dashboard_text)
        self.assertIn("Luffy", dashboard_text)

        chat = ChatService(SimpleNamespace(), ContextBuilder(12))
        prepared = chat.prepare_live_call_input(
            self.db, user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id, legacy_name="Mom",
            relationship="mother", user_message="Tell me about our dogs.", history=(),
        )
        chat_text = " ".join(item["summary"] for item in prepared.memory_evidence)
        self.assertIn("Bruno", chat_text)
        self.assertIn("Luffy", chat_text)
        speech_prepared = chat.prepare_live_call_input(
            self.db, user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id, legacy_name="Mom",
            relationship="mother",
            user_message="Can you tell me about our docs.", history=(),
        )
        self.assertEqual(set(speech_prepared.memory_ids), set(prepared.memory_ids))
        session = SimpleNamespace(
            session_id="add-entity", user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id, legacy_name="Mom",
            relationship="mother", conversation_id=None,
        )
        tools = RealtimeToolService(chat)
        tools.route_turn(session, 1, "Can you tell me about our docs.")
        live = tools.execute(
            self.db, session, "retrieve_legacy_memory_context", {}, turn_id=1,
        )
        live_text = " ".join(item["summary"] for item in live["memories"])
        self.assertIn("Bruno", live_text)
        self.assertIn("Luffy", live_text)
        self.assertEqual(live["selected_memory_ids"], list(prepared.memory_ids))
        self.assertEqual(
            {name.casefold() for name in live["resolved_entities"]},
            {"bruno", "luffy"},
        )
        self.assertFalse(live["uncertain"])

        for turn_id, query in enumerate((
            "Our dogs", "Their names", "Their breeds", "One more, remember",
        ), start=2):
            self.assertIn(
                tools.route_turn(session, turn_id, query)["route"],
                {"memory", "followup"},
            )
            followup = tools.execute(
                self.db, session, "retrieve_legacy_memory_context", {}, turn_id=turn_id,
            )
            followup_text = " ".join(item["summary"] for item in followup["memories"])
            self.assertIn("Bruno", followup_text)
            self.assertIn("Luffy", followup_text)
            self.assertEqual(set(followup["selected_memory_ids"]), set(prepared.memory_ids))
            self.assertFalse(followup["uncertain"])

    async def test_partial_subject_fact_is_supported_for_broad_specific_and_followup_queries(self):
        self.complete_story()
        self.user_story_message.content = "Our TV at home is 85 inches."
        self.db.commit()
        television = self.candidate(
            summary="Our TV at home is 85 inches.",
            excerpt="Our TV at home is 85 inches.",
            participants=False,
        ).model_copy(update={
            "title": "85-inch TV at home",
            "tags": ["household", "home", "tv"],
        })
        await self.run_story([television])

        chat = ChatService(SimpleNamespace(), ContextBuilder(12))
        prepared = chat.prepare_live_call_input(
            self.db, user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id, legacy_name="Mom",
            relationship="mother", user_message="Tell me about our TV.", history=(),
        )
        self.assertEqual(len(prepared.memory_ids), 1)
        self.assertIn("85 inches", prepared.memory_evidence[0]["summary"])
        self.assertEqual(prepared.fact_confidence, "supported")
        self.assertEqual(prepared.coverage, "partial")
        self.assertFalse(prepared.has_uncertainty)

        session = SimpleNamespace(
            session_id="partial-tv", user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id, legacy_name="Mom",
            relationship="mother", conversation_id=None,
        )
        tools = RealtimeToolService(chat)
        expected_ids = list(prepared.memory_ids)
        for turn_id, query in enumerate((
            "Tell me about our TV.",
            "What size is our TV?",
            "You remember the size.",
        ), start=1):
            route = tools.route_turn(session, turn_id, query)
            self.assertIn(route["route"], {"memory", "followup"})
            result = tools.execute(
                self.db, session, route["tool_name"], {}, turn_id=turn_id,
            )
            self.assertEqual(result["selected_memory_ids"], expected_ids)
            self.assertEqual(result["status"], "supported")
            self.assertEqual(result["fact_confidence"], "supported")
            self.assertFalse(result["uncertain"])
            self.assertIn("85 inches", result["memories"][0]["summary"])
        self.assertEqual(result["coverage"], "focused")

    async def test_chat_cannot_replace_protected_spouse_identity(self):
        self.complete_story()
        self.user_story_message.content = "My husband is Mohan Deshmukh."
        self.db.commit()
        canonical = self.candidate(
            summary="My husband is Mohan Deshmukh.",
            excerpt="My husband is Mohan Deshmukh.",
            details=MemoryDetails(identity_facts=[{
                "fact_type": "spouse_name", "value": "Mohan Deshmukh",
                "relationship": "husband", "confidence": 1,
            }]),
        ).model_copy(update={
            "title": "Husband Mohan Deshmukh", "category": "relationship",
            "tags": ["family", "husband", "Mohan Deshmukh"],
        })
        await self.run_story([canonical])
        conversational_claim = self.candidate(
            summary="Your husband's name is Sawan Deshmukh.",
            excerpt="Your husband's name is Sawan Deshmukh.",
            details=MemoryDetails(identity_facts=[{
                "fact_type": "spouse_name", "value": "Sawan Deshmukh",
                "relationship": "husband", "confidence": 1,
            }]),
        ).model_copy(update={
            "title": "Husband Sawan Deshmukh", "category": "relationship",
            "tags": ["family", "husband", "Sawan Deshmukh"],
        })
        report = await self.run_auto_chat(
            conversational_claim, "Your husband's name is Sawan Deshmukh."
        )
        self.assertEqual(report.items[0].error_code, "protected_identity_mutation")
        self.assertEqual(self.db.query(Memory).count(), 1)
        unstructured_claim = conversational_claim.model_copy(update={
            "details": MemoryDetails(),
        })
        unstructured_report = await self.run_auto_chat(
            unstructured_claim, "Your husband's name is Sawan Deshmukh."
        )
        self.assertEqual(
            unstructured_report.items[0].error_code,
            "protected_identity_mutation",
        )
        self.assertEqual(self.db.query(Memory).count(), 1)
        live_candidate = conversational_claim.model_copy(update={
            "provenance": [MemoryProvenanceCreate(
                source_type="live_call",
                source_locator={"session_safe_id": "identity-firewall", "turn_id": 2},
                speaker="user",
                excerpt="Your husband's name is Sawan Deshmukh.",
            )]
        })
        live_report = await MemoryStoragePipeline(
            FakeExtractionService([live_candidate])
        ).process_live_call_turn(
            self.db, user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id,
            session_safe_id="identity-firewall", turn_id=2,
            user_text="Your husband's name is Sawan Deshmukh.",
        )
        self.assertEqual(
            live_report.items[0].error_code, "protected_identity_mutation"
        )
        self.assertEqual(self.db.query(Memory).count(), 1)
        facts = self.db.query(LegacyIdentityFact).filter_by(
            legacy_id=self.legacy.legacy_id,
            fact_type="spouse_name",
            status="active",
        ).all()
        self.assertEqual([fact.value for fact in facts], ["Mohan Deshmukh"])

        chat = ChatService(SimpleNamespace(), ContextBuilder(12))
        identity, _ = chat.retrieve_live_call_identity(
            self.db, user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id, query="Who is your husband?",
        )
        self.assertEqual([record["value"] for record in identity.records], ["Mohan Deshmukh"])
        session = SimpleNamespace(
            session_id="protected-spouse", user_id=self.user.user_id,
            legacy_id=self.legacy.legacy_id, legacy_name="Mom", relationship="mother",
        )
        tools = RealtimeToolService(chat)
        tools.route_turn(session, 1, "Who is your husband?")
        live = tools.execute(
            self.db, session, "get_legacy_identity_context", {}, turn_id=1,
        )
        self.assertEqual(
            [record["value"] for record in live["identity"]], ["Mohan Deshmukh"],
        )

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
