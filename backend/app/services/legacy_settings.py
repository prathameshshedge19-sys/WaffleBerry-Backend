"""Owner-scoped orchestration for non-destructive Legacy settings."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.crud.memory import LegacyCRUD
from app.schemas.memory import (
    LegacySettingsResponse,
    LegacySettingsUpdate,
)


class LegacySettingsNotFoundError(Exception):
    """Raised for missing and non-owned Legacies alike."""


class LegacySettingsConflictError(Exception):
    """Raised when a settings form is based on stale Legacy data."""


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

        LegacyCRUD.apply_identity_changes(legacy, **effective)
        legacy.updated_at = datetime.now(timezone.utc)
        try:
            db.commit()
            db.refresh(legacy)
        except Exception:
            db.rollback()
            raise
        return LegacySettingsResponse.model_validate(legacy)

    @staticmethod
    def _require_fresh(actual: datetime | None, expected: datetime) -> None:
        if actual is None:
            raise LegacySettingsConflictError(
                "This Legacy changed. Refresh and try again."
            )
        actual_value = actual.replace(tzinfo=None)
        expected_value = expected.replace(tzinfo=None)
        if abs((actual_value - expected_value).total_seconds()) > 0.001:
            raise LegacySettingsConflictError(
                "This Legacy changed. Refresh and try again."
            )
