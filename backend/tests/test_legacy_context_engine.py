"""Current Legacy Profile and modality-neutral memory-engine tests."""

import unittest
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db import Base
from app.models.memory import (
    Legacy, Memory, MemoryParticipant, MemoryProvenance, MemoryReviewStatus,
    MemoryType,
)
from app.models.user import User
from app.services.memory.identity_facts import IdentityFactProjectionService
from app.services.memory.legacy_context import CurrentLegacyProfileService, LegacyMemoryEngine


class LegacyContextEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        event.listen(cls.engine, "connect", lambda connection, _: connection.execute(
            "PRAGMA foreign_keys=ON"
        ))
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.db = self.Session()
        owner = User(full_name="Owner", email="profile@example.test", password_hash="hash")
        self.db.add(owner)
        self.db.flush()
        self.owner_id = owner.user_id
        self.legacy = Legacy(
            owner_user_id=owner.user_id, display_name="Anjali", relationship="Mother",
        )
        self.db.add(self.legacy)
        self.db.flush()
        self._identity("Anjali", "full_name")
        self._identity("Mohan Deshmukh", "spouse_name", "husband")
        self._identity("Aditya Deshmukh", "sibling_name", "younger brother")
        self.bruno = self._memory(
            "pet", "Bruno", "We have a Labrador named Bruno.",
            participants=(("Bruno", "dog"),), tags=("pet", "Labrador"),
        )
        self.luffy = self._memory(
            "pet", "Luffy", "We have another Labrador named Luffy.",
            participants=(("Luffy", "dog"),), tags=("pet", "Labrador"),
        )
        self.tv = self._memory("home", "Living-room TV", "Our TV is 85 inches.")
        self.brother_story = self._memory(
            "story", "Childhood with Aditya",
            "My younger brother Aditya and I grew up in Pune and teased each other.",
            memory_type=MemoryType.NARRATIVE,
            participants=(("Aditya Deshmukh", "younger brother"),),
            tags=("childhood", "Pune"),
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _identity(self, value, fact_type, relationship=None):
        memory = self._memory(
            "identity", f"{fact_type} identity", f"{value} is the current {fact_type}.",
            details={"identity_facts": [{
                "fact_type": fact_type, "value": value,
                "relationship": relationship, "confidence": 1,
            }]},
        )
        IdentityFactProjectionService().project_memory(self.db, memory)

    def _memory(self, category, title, summary, *, memory_type=MemoryType.ATOMIC,
                participants=(), tags=(), details=None):
        memory = Memory(
            legacy_id=self.legacy.legacy_id, memory_type=memory_type,
            category=category, title=title, summary=summary,
            details=details or {}, review_status=MemoryReviewStatus.APPROVED,
            extraction_confidence=Decimal("1"),
        )
        self.db.add(memory)
        self.db.flush()
        self.db.add(MemoryProvenance(
            memory_id=memory.memory_id, source_type="conversation",
            excerpt=summary, speaker="user",
        ))
        for name, relationship in participants:
            self.db.add(MemoryParticipant(
                memory_id=memory.memory_id, name=name,
                relationship=relationship, role="subject",
            ))
        # Tags are optional for this proof; include them as structured detail so
        # the profile remains independent of tag-writing helpers.
        if tags:
            memory.details = {**(details or {}), "profile_tags": list(tags)}
        self.db.flush()
        return memory

    def test_profile_synthesizes_identity_family_pets_and_home_without_migration(self):
        profile = CurrentLegacyProfileService().build(
            self.db, user_id=self.owner_id, legacy_id=self.legacy.legacy_id,
        )
        values = " ".join(fact.value for fact in profile.facts)
        self.assertEqual(profile.display_name, "Anjali")
        for expected in ("Anjali", "Mohan Deshmukh", "Aditya Deshmukh",
                         "Bruno", "Luffy", "Labrador", "85 inches"):
            self.assertIn(expected, values)
        self.assertIn("family", profile.sections)
        self.assertIn("pets", profile.sections)
        self.assertIn("home", profile.sections)
        self.assertNotIn(self.brother_story.memory_id, {
            fact.source_id for fact in profile.facts if fact.source_kind == "memory"
        })

    def test_dirty_existing_self_wording_is_cleaned_in_profile_and_detailed_context(self):
        dirty_tv = self._memory(
            "home", "Dirty TV", "Anjali says there is an 85-inch TV at home.",
        )
        dirty_story = self._memory(
            "story", "Growing up", "Anjali and Aditya grew up together in Pune.",
            memory_type=MemoryType.NARRATIVE,
            participants=(("Aditya", "younger brother"),),
        )
        self.db.commit()
        profile = CurrentLegacyProfileService().build(
            self.db, user_id=self.owner_id, legacy_id=self.legacy.legacy_id,
        )
        value = next(f.value for f in profile.facts if f.source_id == dirty_tv.memory_id)
        self.assertEqual(value, "We have an 85-inch TV at home.")
        turn = self._turn(LegacyMemoryEngine(), "Tell me about growing up with Aditya", ())
        prompt = turn.prompt_context()
        self.assertIn("Aditya and I grew up together in Pune.", prompt)
        self.assertNotIn("Anjali and Aditya grew up", prompt)

    def test_random_topic_switch_and_entity_followup_do_not_contaminate(self):
        engine = LegacyMemoryEngine()
        history = []
        brother = self._turn(engine, "Who is your brother?", history)
        self.assertIn("Aditya Deshmukh", {fact.value for fact in brother.profile_facts})
        history.append(SimpleNamespace(role="user", content="Who is your brother?"))
        details = self._turn(engine, "Tell me more about him.", history)
        self.assertIn(self.brother_story.memory_id, {
            item.memory_id for item in details.detailed_memories
        })
        history.append(SimpleNamespace(role="user", content="Tell me more about him."))
        television = self._turn(engine, "What about our TV?", history)
        self.assertIn(self.tv.memory_id, {fact.source_id for fact in television.profile_facts})
        self.assertNotIn(self.brother_story.memory_id, {
            item.memory_id for item in television.detailed_memories
        })
        bruno = self._turn(engine, "And Bruno?", history)
        self.assertIn(self.bruno.memory_id, {fact.source_id for fact in bruno.profile_facts})
        family = self._turn(engine, "Tell me about your family.", history)
        self.assertTrue({"Mohan Deshmukh", "Aditya Deshmukh"}.issubset(
            {fact.value for fact in family.profile_facts}
        ))

    def test_partial_and_true_unknown_follow_structural_no_memory_rule(self):
        engine = LegacyMemoryEngine()
        dogs = self._turn(engine, "Do you have dogs?", ())
        self.assertFalse(dogs.may_say_no_memory)
        self.assertTrue({self.bruno.memory_id, self.luffy.memory_id}.issubset(
            {fact.source_id for fact in dogs.profile_facts}
        ))
        unknown = self._turn(engine, "What is our submarine's serial number?", ())
        self.assertTrue(unknown.may_say_no_memory)
        self.assertEqual(unknown.retrieval_status, "true_unknown")

    def test_same_engine_is_conversation_independent_and_reflects_current_edit(self):
        engine = LegacyMemoryEngine()
        first = self._turn(engine, "What about our TV?", ())
        self.assertIn("85 inches", " ".join(f.value for f in first.profile_facts))
        self.tv.summary = "Our TV is 90 inches."
        self.db.commit()
        chat = self._turn(engine, "What about our TV?", ())
        live = self._turn(engine, "What about our television?", ())
        self.assertIn("90 inches", " ".join(f.value for f in chat.profile_facts))
        self.assertIn("90 inches", " ".join(f.value for f in live.profile_facts))
        self.assertNotIn("85 inches", " ".join(f.value for f in chat.profile_facts))
        self.assertEqual(
            {f.source_id for f in chat.profile_facts},
            {f.source_id for f in live.profile_facts},
        )

    def _turn(self, engine, message, history):
        return engine.prepare_legacy_context(
            self.db, user_id=self.owner_id, legacy_id=self.legacy.legacy_id,
            conversation_id=999, user_message=message, recent_history=history,
        )


if __name__ == "__main__":
    unittest.main()
