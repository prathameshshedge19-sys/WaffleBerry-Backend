"""Phase 6.7.2 deterministic memory relevance ranking tests."""

import inspect
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.v1.memory import search_approved_memories
from app.db import Base
from app.models.memory import Legacy, Memory, MemoryReviewStatus, MemoryType
from app.models.user import User
from app.schemas.memory import (
    ApprovedMemoryRetrievalItem,
    ApprovedMemorySearchRequest,
    MemoryDetails,
    MemorySemanticAttributes,
)
from app.services.memory.retrieval import (
    MemoryRetrievalNotFoundError,
    MemoryRetrievalService,
)
from app.services.memory.retrieval_ranking import MemoryRelevanceRanker


class MemoryRelevanceRankerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
        self.ranker = MemoryRelevanceRanker()

    def item(
        self,
        memory_id,
        *,
        title="",
        summary="",
        category="story",
        importance=3,
        updated_at=None,
        memory_type=MemoryType.ATOMIC,
        semantic_attributes=None,
        uncertainty_note=None,
        contradiction_group_id=None,
    ):
        return ApprovedMemoryRetrievalItem(
            memory_id=memory_id,
            memory_type=memory_type,
            category=category,
            title=title,
            summary=summary,
            details=(
                MemoryDetails(semantic_attributes=semantic_attributes)
                if semantic_attributes is not None
                else None
            ),
            importance=importance,
            extraction_confidence=Decimal("0.8"),
            uncertainty_note=uncertainty_note,
            contradiction_group_id=contradiction_group_id,
            created_at=self.now,
            updated_at=updated_at or self.now,
        )

    def test_open_ended_occupation_queries_retrieve_tuition_teacher(self):
        profession = self.item(
            1,
            title="Tuition teacher",
            summary="I was a tuition teacher.",
        )
        for query in (
            "What was your profession?",
            "What was your job?",
            "What did you do in your career?",
            "What work did you do?",
            "What was your occupation?",
            "Were you a tuition teacher?",
        ):
            with self.subTest(query=query):
                result = self.ranker.rank([profession], query)
                self.assertEqual([item.memory_id for item in result], [1])
                self.assertGreater(result[0].relevance_score, 0)

        self.assertEqual(
            self.ranker.rank([profession], "What flowers did you like?"), []
        )

    def test_query_intent_classification_is_small_and_controlled(self):
        cases = {
            "What was your employment?": "occupation",
            "What did you do?": "occupation",
            "Where were you born?": "birthplace",
            "What grade did you study?": "education",
            "What flowers did you like?": None,
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(
                    self.ranker.classify_query_intent(query), expected
                )

    def test_teaching_relationship_retrieves_without_implying_profession(self):
        teaching = self.item(
            2,
            title="Teaching her son",
            summary="She taught her son until grade 10.",
            category="relationship",
            semantic_attributes=MemorySemanticAttributes(
                taught_relationship="son",
                education_level="grade 10",
            ),
        )
        result = self.ranker.rank(
            [teaching], "Who taught me until grade 10?"
        )
        self.assertEqual([item.memory_id for item in result], [2])
        self.assertEqual(
            self.ranker.rank([teaching], "What was your profession?"), []
        )

    def test_birthplace_intent_variants_use_explicit_attribute(self):
        birthplace = self.item(
            3,
            title="Early life",
            summary="She was born in Pune.",
            semantic_attributes=MemorySemanticAttributes(
                birthplace="Pune"
            ),
        )
        for query in (
            "Where were you born?",
            "What was your birthplace?",
            "What was your place of birth?",
        ):
            with self.subTest(query=query):
                result = self.ranker.rank([birthplace], query)
                self.assertEqual([item.memory_id for item in result], [3])

    def test_exact_phrase_and_title_matches_rank_above_partial_summary(self):
        partial = self.item(1, summary="Jasmine grew beside the old house")
        summary = self.item(2, summary="She loved jasmine tea every morning")
        title = self.item(3, title="Jasmine tea", summary="A daily ritual")
        result = self.ranker.rank([partial, summary, title], "jasmine tea")
        self.assertEqual([item.memory_id for item in result], [3, 2, 1])
        self.assertGreater(result[0].relevance_score, result[1].relevance_score)

    def test_case_punctuation_whitespace_and_unicode_are_normalized(self):
        memory = self.item(
            1,
            title="SÃO—PAULO memories",
            summary="A family trip.",
        )
        for query in ("  são   paulo ", "SÃO, PAULO!!!"):
            with self.subTest(query=query):
                result = self.ranker.rank([memory], query)
                self.assertEqual(result[0].matched_terms, ["são", "paulo"])

    def test_no_match_returns_empty_and_empty_title_is_safe(self):
        memory = self.item(1, title="", summary="A mountain holiday")
        self.assertEqual(self.ranker.rank([memory], "jasmine"), [])

    def test_common_plural_variants_retrieve_supported_fact(self):
        memory = self.item(
            1,
            title="Favorite flowers",
            summary="She loved jasmine flowers.",
        )
        result = self.ranker.rank([memory], "What was her favorite flower?")
        self.assertEqual([item.memory_id for item in result], [1])
        self.assertIn("flower", result[0].matched_terms)

    def test_atomic_and_narrative_memories_are_ranked(self):
        items = [
            self.item(1, title="School", memory_type=MemoryType.ATOMIC),
            self.item(2, summary="School days", memory_type=MemoryType.NARRATIVE),
        ]
        result = self.ranker.rank(items, "school")
        self.assertEqual({item.memory_type for item in result}, {
            MemoryType.ATOMIC, MemoryType.NARRATIVE
        })

    def test_internal_uncertainty_and_conflict_metadata_survive_ranking(self):
        memory = self.item(
            1,
            title="Marriage year",
            summary="We may have married in 2002.",
            uncertainty_note="The source was approximate.",
            contradiction_group_id=9,
        )
        ranked = self.ranker.rank([memory], "married 2002")[0]
        self.assertEqual(ranked.uncertainty_note, "The source was approximate.")
        self.assertEqual(ranked.contradiction_group_id, 9)
        self.assertNotIn("uncertainty_note", ranked.model_dump())
        self.assertNotIn("contradiction_group_id", ranked.model_dump())

    def test_importance_is_only_a_tie_breaker(self):
        relevant_low = self.item(1, title="Jasmine tea", importance=1)
        partial_high = self.item(2, summary="Jasmine garden", importance=5)
        irrelevant_high = self.item(3, title="Sailing", importance=5)
        result = self.ranker.rank(
            [irrelevant_high, partial_high, relevant_low], "jasmine tea"
        )
        self.assertEqual([item.memory_id for item in result], [1, 2])

        tied = self.ranker.rank(
            [self.item(4, title="Jasmine", importance=1),
             self.item(5, title="Jasmine", importance=5)],
            "jasmine",
        )
        self.assertEqual([item.memory_id for item in tied], [5, 4])

    def test_recency_then_id_are_stable_final_tie_breakers(self):
        old = self.item(9, title="Jasmine", updated_at=self.now)
        new_high_id = self.item(
            8, title="Jasmine", updated_at=self.now + timedelta(hours=1)
        )
        new_low_id = self.item(
            7, title="Jasmine", updated_at=self.now + timedelta(hours=1)
        )
        inputs = [new_high_id, old, new_low_id]
        first = self.ranker.rank(inputs, "jasmine")
        second = self.ranker.rank(list(reversed(inputs)), "jasmine")
        self.assertEqual([x.memory_id for x in first], [7, 8, 9])
        self.assertEqual(
            [x.memory_id for x in first], [x.memory_id for x in second]
        )

    def test_stop_words_do_not_create_false_matches(self):
        self.assertEqual(
            self.ranker.rank([self.item(1, summary="The old house")], "the"),
            [],
        )

    def test_ranking_module_has_no_ai_or_network_integration(self):
        source = inspect.getsource(inspect.getmodule(MemoryRelevanceRanker))
        for forbidden in ("openai", "httpx", "requests", "socket"):
            self.assertNotIn(forbidden, source.casefold())

    def test_query_validation_rejects_blank_and_excessive_input(self):
        for query in ("   ", "x" * 2001):
            with self.subTest(length=len(query)):
                with self.assertRaises(ValidationError):
                    ApprovedMemorySearchRequest(query=query)


class MemoryRelevanceServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.owner = User(full_name="Owner", email="rank-owner@example.test",
                          password_hash="hash")
        self.other = User(full_name="Other", email="rank-other@example.test",
                          password_hash="hash")
        self.db.add_all([self.owner, self.other])
        self.db.flush()
        self.legacy = Legacy(owner_user_id=self.owner.user_id,
                             display_name="Mom", relationship="Mother")
        self.db.add(self.legacy)
        self.db.commit()
        self.service = MemoryRetrievalService()

    def tearDown(self):
        self.db.close()

    def add_memory(self, status, title):
        memory = Memory(
            legacy_id=self.legacy.legacy_id,
            memory_type=MemoryType.ATOMIC,
            category="story",
            title=title,
            summary=title,
            review_status=status,
        )
        self.db.add(memory)
        self.db.commit()
        return memory

    def test_service_excludes_all_non_approved_states(self):
        approved = self.add_memory(MemoryReviewStatus.APPROVED, "Jasmine")
        for status in (
            MemoryReviewStatus.CANDIDATE,
            MemoryReviewStatus.REJECTED,
        ):
            self.add_memory(status, "Jasmine")
        replacement = self.add_memory(
            MemoryReviewStatus.APPROVED, "Unrelated replacement"
        )
        superseded = Memory(
            legacy_id=self.legacy.legacy_id,
            memory_type=MemoryType.ATOMIC,
            category="story",
            title="Jasmine",
            summary="Jasmine",
            review_status=MemoryReviewStatus.SUPERSEDED,
            superseded_by_memory_id=replacement.memory_id,
        )
        self.db.add(superseded)
        self.db.commit()
        result = self.service.search_approved(
            self.db, user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id, query="jasmine"
        )
        self.assertEqual([x.memory_id for x in result.memories],
                         [approved.memory_id])
        self.assertEqual(result.matched_memory_count, 1)

    def test_empty_approved_set_returns_empty_contract(self):
        result = self.service.search_approved(
            self.db, user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id, query="jasmine"
        )
        self.assertEqual(result.memories, [])
        self.assertEqual(result.matched_memory_count, 0)

    def test_cross_owner_and_missing_legacy_use_same_not_found(self):
        for user_id, legacy_id in (
            (self.other.user_id, self.legacy.legacy_id),
            (self.owner.user_id, 999999),
        ):
            with self.subTest(user_id=user_id, legacy_id=legacy_id):
                with self.assertRaises(MemoryRetrievalNotFoundError):
                    self.service.search_approved(
                        self.db, user_id=user_id, legacy_id=legacy_id,
                        query="jasmine"
                    )

    def test_route_returns_contract_and_neutral_404(self):
        memory = self.add_memory(MemoryReviewStatus.APPROVED, "Jasmine")
        result = search_approved_memories(
            self.legacy.legacy_id,
            ApprovedMemorySearchRequest(query="jasmine"),
            current_user=self.owner, db=self.db, service=self.service,
        )
        self.assertEqual(result.memories[0].memory_id, memory.memory_id)
        self.assertGreater(result.memories[0].relevance_score, 0)
        self.assertEqual(result.memories[0].matched_terms, ["jasmine"])

        with self.assertRaises(HTTPException) as context:
            search_approved_memories(
                self.legacy.legacy_id,
                ApprovedMemorySearchRequest(query="jasmine"),
                current_user=self.other, db=self.db, service=self.service,
            )
        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "Legacy was not found.")


if __name__ == "__main__":
    unittest.main()
