"""Owner-scoped orchestration for non-destructive Legacy settings."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.crud.memory import LegacyCRUD
from app.schemas.memory import (
    LegacySettingsResponse,
    LegacySettingsUpdate,
)
from app.models.memory import LegacyStatus


class LegacySettingsNotFoundError(Exception):
    """Raised for missing and non-owned Legacies alike."""


class LegacySettingsConflictError(Exception):
    """Raised when a settings form is based on stale Legacy data."""


class LegacySettingsArchivedError(Exception):
    """Raised when an archived Legacy receives a settings mutation."""


class LegacySettingsService:
    """Validate concurrency and coordinate focused identity updates."""

    def update(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        changes: LegacySettingsUpdate,
    ) -> LegacySettingsResponse:
        legacy = LegacyCRUD.get_user_legacy_for_update(
            db, legacy_id, user_id
        )
        if legacy is None:
            raise LegacySettingsNotFoundError(
                "Legacy was not found."
            )
        if legacy.status == LegacyStatus.ARCHIVED:
            raise LegacySettingsArchivedError(
                "Restore this Legacy before continuing."
            )
        self._require_fresh(legacy.updated_at, changes.expected_updated_at)

        supplied = changes.model_dump(
            exclude={"expected_updated_at"},
            exclude_unset=True,
        )
        effective = {
            key: value
            for key, value in supplied.items()
            if getattr(legacy, key) != value
        }
        if not effective:
            return LegacySettingsResponse.model_validate(legacy)

        updated_at = datetime.now(timezone.utc)
        try:
            changed = LegacyCRUD.apply_identity_changes_if_current(
                db,
                legacy_id=legacy_id,
                user_id=user_id,
                expected_updated_at=legacy.updated_at,
                updated_at=updated_at,
                changes=effective,
            )
            if not changed:
                db.rollback()
                raise LegacySettingsConflictError(
                    "This Legacy changed. Refresh and try again."
                )
            db.commit()
            db.expire_all()
        except Exception:
            db.rollback()
            raise
        refreshed = LegacyCRUD.get_user_legacy(db, legacy_id, user_id)
        if refreshed is None:
            raise LegacySettingsNotFoundError("Legacy was not found.")
        return LegacySettingsResponse.model_validate(refreshed)

    @staticmethod
    def _require_fresh(actual: datetime | None, expected: datetime) -> None:
        if actual is None:
            raise LegacySettingsConflictError(
                "This Legacy changed. Refresh and try again."
            )
        actual_value = LegacySettingsService._as_naive_utc(actual)
        expected_value = LegacySettingsService._as_naive_utc(expected)
        if actual_value != expected_value:
            raise LegacySettingsConflictError(
                "This Legacy changed. Refresh and try again."
            )

    @staticmethod
    def _as_naive_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
