"""Phase 6.8.1 Legacy lifecycle architecture tests."""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.crud.memory import LegacyCRUD
from app.db import Base
from app.models.memory import Legacy, LegacyStatus
from app.models.user import User
from app.services.legacy_lifecycle import (
    LegacyLifecycleService,
    LegacyLifecycleTransitionError,
)


class LegacyLifecyclePolicyTests(unittest.TestCase):
    def setUp(self):
        self.service = LegacyLifecycleService()

    def test_active_capabilities_are_canonical(self):
        policy = self.service.capabilities(LegacyStatus.ACTIVE)
        self.assertTrue(policy.visible_in_normal_lists)
        self.assertTrue(policy.editable)
        self.assertTrue(policy.companion_available)
        self.assertTrue(policy.story_sessions_allowed)
        self.assertTrue(policy.dashboard_available)
        self.assertFalse(policy.read_only)

    def test_archived_capabilities_are_canonical(self):
        policy = self.service.capabilities(LegacyStatus.ARCHIVED)
        self.assertFalse(policy.visible_in_normal_lists)
        self.assertFalse(policy.editable)
        self.assertFalse(policy.companion_available)
        self.assertFalse(policy.story_sessions_allowed)
        self.assertTrue(policy.dashboard_available)
        self.assertTrue(policy.read_only)
        self.assertTrue(policy.recoverable)

    def test_archive_and_restore_are_the_only_persisted_transitions(self):
        allowed = (
            (LegacyStatus.ACTIVE, LegacyStatus.ARCHIVED),
            (LegacyStatus.ARCHIVED, LegacyStatus.ACTIVE),
        )
        for current, target in allowed:
            with self.subTest(current=current, target=target):
                self.service.validate_transition(current, target)
                self.assertTrue(
                    self.service.is_transition_allowed(current, target)
                )

    def test_noop_and_deleted_status_transitions_are_rejected(self):
        for current, target in (
            (LegacyStatus.ACTIVE, LegacyStatus.ACTIVE),
            (LegacyStatus.ARCHIVED, LegacyStatus.ARCHIVED),
            (LegacyStatus.ACTIVE, "deleted"),
        ):
            with self.subTest(current=current, target=target):
                with self.assertRaises(LegacyLifecycleTransitionError):
                    self.service.validate_transition(current, target)

    def test_unknown_status_has_no_capabilities(self):
        with self.assertRaises(LegacyLifecycleTransitionError):
            self.service.capabilities("deleted")


class LegacyLifecyclePersistenceTests(unittest.TestCase):
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
            email="lifecycle-owner@example.test",
            password_hash="hash",
        )
        self.other = User(
            full_name="Other",
            email="lifecycle-other@example.test",
            password_hash="hash",
        )
        self.db.add_all([self.owner, self.other])
        self.db.flush()
        self.legacy = Legacy(
            owner_user_id=self.owner.user_id,
            display_name="Mom",
            relationship="Mother",
        )
        self.db.add(self.legacy)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_active_is_the_persisted_default(self):
        self.db.refresh(self.legacy)
        self.assertEqual(self.legacy.status, LegacyStatus.ACTIVE)

    def test_existing_owner_scope_remains_authoritative(self):
        self.assertIsNotNone(
            LegacyCRUD.get_user_legacy(
                self.db,
                self.legacy.legacy_id,
                self.owner.user_id,
            )
        )
        self.assertIsNone(
            LegacyCRUD.get_user_legacy(
                self.db,
                self.legacy.legacy_id,
                self.other.user_id,
            )
        )

    def test_policy_service_does_not_mutate_persistence(self):
        LegacyLifecycleService().validate_transition(
            LegacyStatus.ACTIVE,
            LegacyStatus.ARCHIVED,
        )
        self.db.refresh(self.legacy)
        self.assertEqual(self.legacy.status, LegacyStatus.ACTIVE)


if __name__ == "__main__":
    unittest.main()
