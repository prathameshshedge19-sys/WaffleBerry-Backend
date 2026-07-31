"""Phase 6.6.5 owner-scoped Legacy Settings tests."""

import unittest
from datetime import timedelta

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.v1.story_memory import update_legacy_settings
from app.crud.memory import LegacyCRUD
from app.db import Base
from app.dependencies.auth import get_current_user
from app.models.memory import Legacy, StorySession, StorySessionStatus
from app.models.user import Conversation, User
from app.schemas.memory import LegacyCreate, LegacySettingsUpdate
from app.services.legacy_dashboard import LegacyDashboardService
from app.services.legacy_settings import (
    LegacySettingsConflictError,
    LegacySettingsNotFoundError,
    LegacySettingsService,
)


class LegacySettingsTests(unittest.TestCase):
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
            email="settings-owner@example.test",
            password_hash="hash",
        )
        self.other = User(
            full_name="Other",
            email="settings-other@example.test",
            password_hash="hash",
        )
        self.db.add_all([self.owner, self.other])
        self.db.commit()
        self.legacy = LegacyCRUD.create_legacy(
            self.db,
            self.owner.user_id,
            LegacyCreate(
                display_name="Mom",
                relationship="Mother",
                client_correlation_id="settings-browser-id",
            ),
        )
        self.service = LegacySettingsService()

    def tearDown(self):
        self.db.close()

    def update(self, **values):
        return self.service.update(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            changes=LegacySettingsUpdate(
                expected_updated_at=self.legacy.updated_at,
                **values,
            ),
        )

    def test_owner_updates_display_name_and_dashboard(self):
        result = self.update(display_name="Mamá")

        self.assertEqual(result.display_name, "Mamá")
        dashboard = LegacyDashboardService().get_summary(
            self.db,
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
        )
        self.assertEqual(dashboard.title, "Mamá")

    def test_owner_updates_relationship(self):
        result = self.update(relationship="My mother")
        self.assertEqual(result.relationship, "My mother")

    def test_partial_update_preserves_unsupplied_field(self):
        result = self.update(display_name="Mama")
        self.assertEqual(result.relationship, "Mother")

    def test_user_facing_text_is_trimmed(self):
        result = self.update(
            display_name="  Mama  ",
            relationship="  Parent  ",
        )
        self.assertEqual(result.display_name, "Mama")
        self.assertEqual(result.relationship, "Parent")

    def test_blank_and_excessive_names_are_rejected(self):
        with self.assertRaises(ValidationError):
            LegacySettingsUpdate(
                expected_updated_at=self.legacy.updated_at,
                display_name="   ",
            )
        with self.assertRaises(ValidationError):
            LegacySettingsUpdate(
                expected_updated_at=self.legacy.updated_at,
                display_name="x" * 256,
            )

    def test_blank_relationship_is_rejected(self):
        with self.assertRaises(ValidationError):
            LegacySettingsUpdate(
                expected_updated_at=self.legacy.updated_at,
                relationship="  ",
            )

    def test_null_editable_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            LegacySettingsUpdate(
                expected_updated_at=self.legacy.updated_at,
                display_name=None,
            )

    def test_protected_and_status_fields_are_rejected(self):
        for field, value in (
            ("owner_user_id", self.other.user_id),
            ("legacy_id", 999),
            ("client_correlation_id", "replacement-id"),
            ("status", "archived"),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                LegacySettingsUpdate.model_validate(
                    {
                        "expected_updated_at": self.legacy.updated_at,
                        "display_name": "Mama",
                        field: value,
                    }
                )

    def test_other_user_and_missing_legacy_are_indistinguishable(self):
        request = LegacySettingsUpdate(
            expected_updated_at=self.legacy.updated_at,
            display_name="Mama",
        )
        for user_id, legacy_id in (
            (self.other.user_id, self.legacy.legacy_id),
            (self.owner.user_id, 999999),
        ):
            with self.subTest(user_id=user_id, legacy_id=legacy_id):
                with self.assertRaises(LegacySettingsNotFoundError):
                    self.service.update(
                        self.db,
                        user_id=user_id,
                        legacy_id=legacy_id,
                        changes=request,
                    )

    def test_route_returns_neutral_404(self):
        request = LegacySettingsUpdate(
            expected_updated_at=self.legacy.updated_at,
            display_name="Mama",
        )
        with self.assertRaises(HTTPException) as context:
            update_legacy_settings(
                self.legacy.legacy_id,
                request,
                current_user=self.other,
                db=self.db,
            )
        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "Legacy was not found.")

    def test_missing_credentials_are_rejected(self):
        with self.assertRaises(HTTPException) as context:
            get_current_user(credentials=None, db=self.db)
        self.assertEqual(context.exception.status_code, 401)

    def test_no_op_preserves_updated_at_and_row_count(self):
        original_updated_at = self.legacy.updated_at
        result = self.update(display_name="Mom")

        self.assertEqual(result.updated_at, original_updated_at)
        self.assertEqual(self.db.query(func.count(Legacy.legacy_id)).scalar(), 1)

    def test_real_update_advances_updated_at(self):
        original_updated_at = self.legacy.updated_at
        result = self.update(display_name="Mama")
        self.assertGreater(result.updated_at, original_updated_at)

    def test_stale_update_is_rejected_and_route_returns_409(self):
        request = LegacySettingsUpdate(
            expected_updated_at=(
                self.legacy.updated_at - timedelta(seconds=1)
            ),
            display_name="Mama",
        )
        with self.assertRaises(LegacySettingsConflictError):
            self.service.update(
                self.db,
                user_id=self.owner.user_id,
                legacy_id=self.legacy.legacy_id,
                changes=request,
            )
        with self.assertRaises(HTTPException) as context:
            update_legacy_settings(
                self.legacy.legacy_id,
                request,
                current_user=self.owner,
                db=self.db,
            )
        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail["code"], "legacy_changed")

    def test_related_records_and_protected_identity_are_preserved(self):
        story = StorySession(
            legacy_id=self.legacy.legacy_id,
            chapter_key="childhood",
            status=StorySessionStatus.IN_PROGRESS,
            created_by_user_id=self.owner.user_id,
        )
        conversation = Conversation(
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            title="Companion chat",
        )
        self.db.add_all([story, conversation])
        self.db.commit()
        original_owner = self.legacy.owner_user_id
        original_correlation = self.legacy.client_correlation_id

        self.update(display_name="Mama")

        self.assertEqual(self.legacy.owner_user_id, original_owner)
        self.assertEqual(
            self.legacy.client_correlation_id,
            original_correlation,
        )
        self.assertEqual(len(self.legacy.story_sessions), 1)
        self.assertEqual(len(self.legacy.conversations), 1)

    def test_existing_synchronization_reuses_same_legacy(self):
        self.update(display_name="Mama")
        synchronized = LegacyCRUD.create_legacy(
            self.db,
            self.owner.user_id,
            LegacyCreate(
                display_name="Ignored retry value",
                relationship="Mother",
                client_correlation_id="settings-browser-id",
            ),
        )
        self.assertEqual(synchronized.legacy_id, self.legacy.legacy_id)
        self.assertEqual(self.db.query(func.count(Legacy.legacy_id)).scalar(), 1)


if __name__ == "__main__":
    unittest.main()
