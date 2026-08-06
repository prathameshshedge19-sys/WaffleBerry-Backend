"""Feedback F2.2 identity-first retrieval tests."""

import unittest
from decimal import Decimal

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base
from app.models.memory import (
    IdentityFactType,
    Legacy,
    Memory,
    MemoryProvenance,
    MemoryReviewStatus,
    MemoryType,
)
from app.models.user import User
from app.services.memory.identity_facts import IdentityFactProjectionService
from app.services.memory.identity_retrieval import (
    detect_identity_intent,
    IdentityFactRetrievalService,
)


class IdentityIntentTests(unittest.TestCase):
    def test_multilingual_identity_intents(self):
        cases = {
            "What is your real full name?": IdentityFactType.FULL_NAME,
            "तुझं खरं पूर्ण नाव काय आहे?": IdentityFactType.FULL_NAME,
            "Tumhara pura naam kya hai?": IdentityFactType.FULL_NAME,
            "What is your husband's name?": IdentityFactType.SPOUSE_NAME,
            "तुझ्या नवऱ्याचं नाव काय आहे?": IdentityFactType.SPOUSE_NAME,
            "तुम्हारे पति का नाम क्या है?": IdentityFactType.SPOUSE_NAME,
            "Who is your brother?": IdentityFactType.SIBLING_NAME,
            "तुझी आई कोण आहे?": IdentityFactType.PARENT_NAME,
            "What is your daughter's name?": IdentityFactType.CHILD_NAME,
            "Where were you born?": IdentityFactType.BIRTHPLACE,
            "What is your hometown?": IdentityFactType.HOMETOWN,
            "What work did you do?": IdentityFactType.OCCUPATION,
            "तुम्हारी पढ़ाई कहाँ हुई?": IdentityFactType.EDUCATION,
            "What is your birth date?": IdentityFactType.BIRTH_DATE,
            "What is your preferred name?": IdentityFactType.PREFERRED_NAME,
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(detect_identity_intent(query), expected)


class IdentityRetrievalTests(unittest.TestCase):
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
        self.owner = User(
            full_name="Owner",
            email="retrieval@example.test",
            password_hash="hash",
        )
        self.other_owner = User(
            full_name="Other",
            email="other-retrieval@example.test",
            password_hash="hash",
        )
        self.db.add_all([self.owner, self.other_owner])
        self.db.flush()
        self.legacy = Legacy(
            owner_user_id=self.owner.user_id,
            display_name="Mom",
            relationship="Mother",
        )
        self.other_legacy = Legacy(
            owner_user_id=self.other_owner.user_id,
            display_name="Other",
            relationship="Mother",
        )
        self.db.add_all([self.legacy, self.other_legacy])
        self.db.commit()
        self.service = IdentityFactRetrievalService()

    def tearDown(self):
        self.db.close()

    def add_fact(self, fact_type, value, *, legacy=None, relationship=None):
        target = legacy or self.legacy
        memory = Memory(
            legacy_id=target.legacy_id,
            memory_type=MemoryType.ATOMIC,
            category="personal_detail",
            title="Identity",
            summary="Approved identity evidence.",
            details={
                "identity_facts": [{
                    "fact_type": fact_type,
                    "value": value,
                    "relationship": relationship,
                    "confidence": 1,
                }]
            },
            review_status=MemoryReviewStatus.APPROVED,
            extraction_confidence=Decimal("1"),
        )
        self.db.add(memory)
        self.db.flush()
        self.db.add(MemoryProvenance(
            memory_id=memory.memory_id,
            source_type="story_session",
            excerpt="User-authored identity evidence.",
            speaker="user",
        ))
        self.db.flush()
        IdentityFactProjectionService().project_memory(self.db, memory)
        self.db.commit()
        return memory

    def retrieve(self, query, *, legacy=None, user=None):
        return self.service.retrieve(
            self.db,
            user_id=(user or self.owner).user_id,
            legacy_id=(legacy or self.legacy).legacy_id,
            query=query,
        )

    def test_full_name_is_cross_language_and_exactly_preserved(self):
        self.add_fact("full_name", "Pallavi Shedge")
        for query in (
            "What is your real full name?",
            "तुझं खरं नाव काय आहे?",
            "तुम्हारा पूरा नाम क्या है?",
        ):
            with self.subTest(query=query):
                result = self.retrieve(query)
                self.assertEqual(result.candidate_count, 1)
                self.assertIn('"value": "Pallavi Shedge"', result.context)
                self.assertNotIn("Mom", result.context)

    def test_spouse_is_cross_language(self):
        self.add_fact("spouse_name", "Kiran Shedge")
        for query in (
            "What is your husband's name?",
            "तुझ्या नवऱ्याचं नाव काय आहे?",
            "तुम्हारे पति का नाम क्या है?",
        ):
            with self.subTest(query=query):
                self.assertIn("Kiran Shedge", self.retrieve(query).context)

    def test_conflicts_are_preserved_and_other_legacy_is_isolated(self):
        self.add_fact("full_name", "Pallavi Shedge")
        self.add_fact("full_name", "Pallavi Patil")
        self.add_fact("full_name", "Private Other", legacy=self.other_legacy)
        result = self.retrieve("What is your full name?")
        self.assertTrue(result.conflict_present)
        self.assertEqual(result.candidate_count, 2)
        self.assertIn("Pallavi Shedge", result.context)
        self.assertIn("Pallavi Patil", result.context)
        self.assertNotIn("Private Other", result.context)
        unauthorized = self.retrieve(
            "What is your full name?",
            legacy=self.other_legacy,
        )
        self.assertIsNone(unauthorized.context)

    def test_missing_fact_returns_empty_context_for_memory_fallback(self):
        result = self.retrieve("What is your brother's name?")
        self.assertEqual(result.fact_type, IdentityFactType.SIBLING_NAME)
        self.assertIsNone(result.context)
        self.assertEqual(result.candidate_count, 0)

    def test_identity_generation_contract_is_first_person_and_language_aware(self):
        self.add_fact("full_name", "Pallavi Shedge")
        for query in (
            "What is your real full name?",
            "तुझं खरं पूर्ण नाव काय आहे?",
            "तुम्हारा पूरा नाम क्या है?",
        ):
            with self.subTest(query=query):
                context = self.retrieve(query).context
                self.assertIn("natural first person", context)
                self.assertIn("language and script of the current user question", context)
                self.assertIn('"value": "Pallavi Shedge"', context)
                self.assertIn("Never attribute an identity fact to the user", context)
                self.assertIn("never translate or transliterate a name", context)

    def test_spouse_contract_uses_only_explicit_relationship_metadata(self):
        self.add_fact(
            "spouse_name",
            "Kiran Shedge",
            relationship="husband",
        )
        context = self.retrieve("What is your husband's name?").context
        self.assertIn('"relationship": "husband"', context)
        self.assertIn("relationship field explicitly supports it", context)
        self.assertIn("Do not add uncertainty", context)

    def test_preferred_name_does_not_replace_full_name(self):
        self.add_fact("full_name", "Pallavi Shedge")
        self.add_fact("preferred_name", "Mom")
        full_name = self.retrieve("What is your real full name?")
        preferred = self.retrieve("What should I call you?")
        self.assertIn("Pallavi Shedge", full_name.context)
        self.assertNotIn('"value": "Mom"', full_name.context)
        self.assertIn('"value": "Mom"', preferred.context)
        self.assertNotIn("Pallavi Shedge", preferred.context)

    def test_conflict_contract_requires_all_values_without_user_attribution(self):
        self.add_fact("full_name", "Pallavi Shedge")
        self.add_fact("full_name", "Pallavi Patil")
        context = self.retrieve("What is your real name?").context
        self.assertIn("Pallavi Shedge", context)
        self.assertIn("Pallavi Patil", context)
        self.assertIn("acknowledge the conflict naturally", context)
        self.assertIn("do not select one arbitrarily", context)
        self.assertIn("Never attribute an identity fact to the user", context)


if __name__ == "__main__":
    unittest.main()
