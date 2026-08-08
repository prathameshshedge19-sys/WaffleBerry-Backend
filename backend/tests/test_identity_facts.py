"""Feedback F2.1 structured identity projection tests."""

import unittest
from decimal import Decimal

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base
from app.models.memory import (
    IdentityFactStatus,
    Legacy,
    LegacyIdentityFact,
    Memory,
    MemoryProvenance,
    MemoryReviewStatus,
    MemoryType,
)
from app.models.user import User
from app.services.ai.prompt_builder import PromptBuilder
from app.services.memory.identity_facts import IdentityFactProjectionService
from scripts.backfill_legacy_identity_facts import run_backfill


class IdentityFactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        event.listen(cls.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.db = self.Session()
        owner = User(full_name="Owner", email="identity@example.test", password_hash="hash")
        self.db.add(owner)
        self.db.flush()
        self.legacy = Legacy(owner_user_id=owner.user_id, display_name="Mom", relationship="Mother")
        self.other = Legacy(owner_user_id=owner.user_id, display_name="Dad", relationship="Father")
        self.db.add_all([self.legacy, self.other])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def memory(self, claims, *, legacy=None, status=MemoryReviewStatus.APPROVED):
        memory = Memory(
            legacy_id=(legacy or self.legacy).legacy_id,
            memory_type=MemoryType.ATOMIC,
            category="personal_detail",
            title="Identity",
            summary="Explicit identity statement.",
            details={"identity_facts": claims},
            review_status=status,
            extraction_confidence=Decimal("0.95"),
        )
        self.db.add(memory)
        self.db.flush()
        self.db.add(MemoryProvenance(
            memory_id=memory.memory_id,
            source_type="story_session",
            excerpt="Explicit user-authored identity statement.",
            speaker="user",
        ))
        self.db.flush()
        self.db.refresh(memory)
        return memory

    def test_names_relationships_deduplicate_and_preserve_unicode(self):
        claims = [
            {"fact_type": "full_name", "value": "Pallavi Shedge", "confidence": 1},
            {"fact_type": "spouse_name", "value": "Kiran Shedge", "relationship": "husband", "confidence": 1},
            {"fact_type": "sibling_name", "value": "Pushkar", "relationship": "younger brother", "confidence": 1},
        ]
        service = IdentityFactProjectionService()
        self.assertEqual(service.project_memory(self.db, self.memory(claims)), 3)
        self.assertEqual(service.project_memory(self.db, self.memory(claims)), 0)
        facts = self.db.query(LegacyIdentityFact).all()
        self.assertEqual({fact.value for fact in facts}, {"Pallavi Shedge", "Kiran Shedge", "Pushkar"})

    def test_unapproved_and_assistant_evidence_cannot_project(self):
        claim = [{"fact_type": "full_name", "value": "Guess", "confidence": 1}]
        self.assertEqual(
            IdentityFactProjectionService().project_memory(
                self.db, self.memory(claim, status=MemoryReviewStatus.CANDIDATE)
            ),
            0,
        )
        approved = self.memory(claim)
        approved.provenance[0].speaker = "assistant"
        self.assertEqual(IdentityFactProjectionService().project_memory(self.db, approved), 0)

    def test_conflicting_singleton_names_are_both_preserved(self):
        service = IdentityFactProjectionService()
        service.project_memory(self.db, self.memory([{"fact_type": "full_name", "value": "Pallavi Shedge", "confidence": 1}]))
        service.project_memory(self.db, self.memory([{"fact_type": "full_name", "value": "Pallavi Patil", "confidence": 1}]))
        facts = self.db.query(LegacyIdentityFact).all()
        self.assertEqual(len(facts), 2)
        self.assertTrue(all(fact.status == IdentityFactStatus.CONFLICTING for fact in facts))

    def test_legacy_scope_and_evidence_deletion(self):
        service = IdentityFactProjectionService()
        first = self.memory([{"fact_type": "occupation", "value": "Teacher", "confidence": 1}])
        second = self.memory([{"fact_type": "occupation", "value": "Teacher", "confidence": 1}], legacy=self.other)
        service.project_memory(self.db, first)
        service.project_memory(self.db, second)
        self.assertEqual(self.db.query(LegacyIdentityFact).count(), 2)
        self.db.delete(first)
        self.db.commit()
        self.assertEqual(self.db.query(LegacyIdentityFact).count(), 1)

    def test_prompt_rejects_mom_as_real_name_and_unsupported_relationships(self):
        prompt = " ".join(PromptBuilder.build_memory_extraction_system_prompt().split())
        self.assertIn('"You can call me Mom" is not a full_name', prompt)
        self.assertIn("Never infer a relationship from proximity", prompt)

    def test_backfill_failure_isolated_resume_retry_and_idempotency(self):
        memories = [
            self.memory([{
                "fact_type": "full_name",
                "value": value,
                "confidence": 1,
            }])
            for value in ("First Private", "Failed Private", "Third Private")
        ]
        self.db.commit()
        failed_id = memories[1].memory_id

        class FailOnceProjector:
            def __init__(self):
                self.failed = False
                self.delegate = IdentityFactProjectionService()

            def project_memory(self, db, memory):
                if memory.memory_id == failed_id and not self.failed:
                    self.failed = True
                    raise RuntimeError("deterministic injected failure")
                return self.delegate.project_memory(db, memory)

        output = []
        first = run_backfill(
            self.db,
            batch_size=3,
            projector=FailOnceProjector(),
            emit=output.append,
        )
        self.assertEqual((first.scanned, first.created, first.skipped, first.failed), (3, 2, 0, 1))
        self.assertEqual(first.resume_after_memory_id, memories[0].memory_id)
        self.assertEqual(
            {
                fact.source_memory_id
                for fact in self.db.query(LegacyIdentityFact).all()
            },
            {memories[0].memory_id, memories[2].memory_id},
        )
        self.assertFalse(any("Private" in line for line in output))

        retry = run_backfill(
            self.db,
            batch_size=3,
            after_memory_id=first.resume_after_memory_id,
            emit=output.append,
        )
        self.assertEqual((retry.created, retry.skipped, retry.failed), (1, 1, 0))
        self.assertEqual(self.db.query(LegacyIdentityFact).count(), 3)

        repeated = run_backfill(self.db, batch_size=3, emit=output.append)
        self.assertEqual((repeated.created, repeated.skipped, repeated.failed), (0, 3, 0))
        self.assertEqual(self.db.query(LegacyIdentityFact).count(), 3)

    def test_backfill_dry_run_counts_without_writes(self):
        self.memory([{
            "fact_type": "full_name",
            "value": "Dry Run Private",
            "confidence": 1,
        }])
        self.memory([{
            "fact_type": "spouse_name",
            "value": "Another Private",
            "confidence": 1,
        }])
        self.db.commit()
        output = []
        result = run_backfill(
            self.db,
            batch_size=2,
            dry_run=True,
            emit=output.append,
        )
        self.assertEqual((result.scanned, result.created, result.skipped, result.failed), (2, 2, 0, 0))
        self.assertEqual(self.db.query(LegacyIdentityFact).count(), 0)
        self.assertFalse(any("Private" in line for line in output))


if __name__ == "__main__":
    unittest.main()
