"""Feedback F3 conservative cross-script proper-name resolution tests."""

import unittest
from decimal import Decimal

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base
from app.models.memory import (
    Legacy,
    LegacyIdentityFact,
    Memory,
    MemoryParticipant,
    MemoryProvenance,
    MemoryReviewStatus,
    MemoryType,
)
from app.models.user import User
from app.services.memory.identity_facts import IdentityFactProjectionService
from app.services.memory.identity_retrieval import IdentityFactRetrievalService
from app.services.memory.name_resolution import (
    comparable_name,
    ProperNameResolver,
    transliterate_devanagari,
)
from app.services.memory.retrieval import MemoryRetrievalService


class ProperNameResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        event.listen(
            cls.engine,
            "connect",
            lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
        )
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.db = self.Session()
        self.owner = User(full_name="Owner", email="f3@example.test", password_hash="hash")
        self.other_owner = User(full_name="Other", email="f3-other@example.test", password_hash="hash")
        self.db.add_all([self.owner, self.other_owner])
        self.db.flush()
        self.legacy = Legacy(owner_user_id=self.owner.user_id, display_name="Granny", relationship="Grandmother")
        self.other_legacy = Legacy(owner_user_id=self.other_owner.user_id, display_name="Other", relationship="Grandmother")
        self.db.add_all([self.legacy, self.other_legacy])
        self.db.commit()
        self.resolver = ProperNameResolver()

    def tearDown(self):
        self.db.close()

    def add_fact(self, value, *, fact_type="sibling_name", relationship="younger sister", legacy=None):
        target = legacy or self.legacy
        memory = Memory(
            legacy_id=target.legacy_id,
            memory_type=MemoryType.ATOMIC,
            category="family",
            title="Family identity",
            summary=f"{value} is a supported family identity.",
            details={"identity_facts": [{
                "fact_type": fact_type,
                "value": value,
                "relationship": relationship,
                "confidence": 1,
            }]},
            review_status=MemoryReviewStatus.APPROVED,
            extraction_confidence=Decimal("1"),
        )
        self.db.add(memory)
        self.db.flush()
        self.db.add(MemoryProvenance(
            memory_id=memory.memory_id,
            source_type="story_session",
            excerpt="Explicit user evidence.",
            speaker="user",
        ))
        self.db.flush()
        IdentityFactProjectionService().project_memory(self.db, memory)
        self.db.commit()
        return memory

    def resolve(self, query, *, legacy=None, user=None):
        return self.resolver.resolve(
            self.db,
            user_id=(user or self.owner).user_id,
            legacy_id=(legacy or self.legacy).legacy_id,
            query=query,
        )

    def test_granny_regression_resolves_script_and_spelling_variants(self):
        self.add_fact("मीनाक्षी")
        initial_count = self.db.query(LegacyIdentityFact).count()
        for query in (
            "Who is मीनाक्षी?",
            "Who is Meenakshi?",
            "Who is Minakshi?",
            "Tell me about Meenakshi.",
            "What did Minakshi and you do as children?",
        ):
            with self.subTest(query=query):
                result = self.resolve(query)
                self.assertEqual(result.canonical_value, "मीनाक्षी")
                self.assertEqual(result.relationship, "younger sister")
                self.assertGreaterEqual(result.confidence, 0.90)
        self.assertEqual(self.db.query(LegacyIdentityFact).count(), initial_count)

    def test_reverse_script_case_whitespace_and_combining_marks(self):
        self.add_fact("Meenakshi")
        result = self.resolve("  मीनाक्षी   कोण आहे? ")
        self.assertEqual(result.canonical_value, "Meenakshi")
        self.assertEqual(comparable_name("मीनाक्षी"), comparable_name("MEENAKSHI"))
        self.assertEqual(comparable_name("  Meenakshi "), comparable_name("meenakshi"))
        self.assertEqual(transliterate_devanagari("मीनाक्षी"), "miinaakshii")

    def test_spouse_full_and_first_name_resolution_reaches_identity_grounding(self):
        self.add_fact("किरण शेडगे", fact_type="spouse_name", relationship="spouse")
        for query in ("Who is Kiran Shedge?", "Who is Kiran?", "किरण शेडगे कोण आहे?"):
            with self.subTest(query=query):
                resolution = self.resolve(query)
                self.assertEqual(resolution.canonical_value, "किरण शेडगे")
                grounded = IdentityFactRetrievalService().retrieve(
                    self.db,
                    user_id=self.owner.user_id,
                    legacy_id=self.legacy.legacy_id,
                    query=query,
                    fact_type_override=resolution.fact_type,
                )
                self.assertIn("किरण शेडगे", grounded.context)

    def test_named_identity_grounding_is_scoped_to_resolved_canonical_value(self):
        self.add_fact("मीनाक्षी", relationship="younger sister")
        self.add_fact("Pushkar", relationship="younger brother")
        resolution = self.resolve("Who is Meenakshi?")
        grounded = IdentityFactRetrievalService().retrieve(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            query="Who is Meenakshi?",
            fact_type_override=resolution.fact_type,
            canonical_value_override=resolution.canonical_value,
        )
        self.assertIn("मीनाक्षी", grounded.context)
        self.assertNotIn("Pushkar", grounded.context)

    def test_query_expansion_retrieves_the_canonical_sister_story(self):
        self.add_fact("मीनाक्षी")
        story = Memory(
            legacy_id=self.legacy.legacy_id,
            memory_type=MemoryType.NARRATIVE,
            category="childhood",
            title="मीनाक्षीसोबत बालपण",
            summary="मीनाक्षी आणि मी नाशिकमध्ये राहत होतो, शाळेत चालत जात होतो, मंदिरात जात होतो आणि शेंगदाणे खात होतो.",
            details={},
            review_status=MemoryReviewStatus.APPROVED,
            extraction_confidence=Decimal("1"),
        )
        self.db.add(story)
        self.db.commit()
        resolution = self.resolve("Tell me about Meenakshi")
        expanded = resolution.expand_query("Tell me about Meenakshi")
        ranked = MemoryRetrievalService().search_approved(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            query=expanded,
        )
        self.assertIn(story.memory_id, [memory.memory_id for memory in ranked.memories])
        self.assertIn("Meenakshi", expanded)
        self.assertIn("मीनाक्षी", expanded)

    def test_unsafe_spelling_pairs_do_not_merge(self):
        for stored, query in (
            ("Rohan", "Who is Rohit?"),
            ("Meena", "Who is Meenakshi?"),
            ("Amit", "Who is Amita?"),
        ):
            with self.subTest(stored=stored, query=query):
                self.add_fact(stored)
                self.assertIsNone(self.resolve(query).canonical_value)
                self.db.query(LegacyIdentityFact).delete()
                self.db.query(Memory).delete()
                self.db.commit()

    def test_incompatible_relationships_are_ambiguous(self):
        self.add_fact("Meenakshi", fact_type="sibling_name", relationship="sister")
        self.add_fact("Meenakshi", fact_type="spouse_name", relationship="spouse")
        result = self.resolve("Who is Meenakshi?")
        self.assertTrue(result.ambiguous)
        self.assertIsNone(result.canonical_value)

    def test_matching_coworker_evidence_blocks_spouse_resolution(self):
        self.add_fact("Kiran", fact_type="spouse_name", relationship="spouse")
        coworker_memory = Memory(
            legacy_id=self.legacy.legacy_id,
            memory_type=MemoryType.ATOMIC,
            category="work",
            title="Coworker",
            summary="Kiran was a coworker.",
            details={},
            review_status=MemoryReviewStatus.APPROVED,
            extraction_confidence=Decimal("1"),
        )
        self.db.add(coworker_memory)
        self.db.flush()
        self.db.add(MemoryParticipant(
            memory_id=coworker_memory.memory_id,
            name="Kiran",
            relationship="coworker",
            role="mentioned_person",
        ))
        self.db.commit()
        result = self.resolve("Who is Kiran?")
        self.assertTrue(result.ambiguous)
        self.assertIsNone(result.canonical_value)

    def test_legacy_and_ownership_isolation(self):
        self.add_fact("मीनाक्षी", legacy=self.other_legacy)
        self.assertIsNone(self.resolve("Who is Meenakshi?").canonical_value)
        unauthorized = self.resolve(
            "Who is Meenakshi?",
            legacy=self.other_legacy,
            user=self.owner,
        )
        self.assertIsNone(unauthorized.canonical_value)


if __name__ == "__main__":
    unittest.main()
