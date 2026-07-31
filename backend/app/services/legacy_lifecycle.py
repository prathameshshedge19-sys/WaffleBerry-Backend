"""Canonical Legacy lifecycle policy without persistence mutations."""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.crud.memory import LegacyCRUD
from app.models.memory import LegacyStatus
from app.schemas.memory import LegacyLifecycleResponse


class LegacyLifecycleTransitionError(ValueError):
    """Raised when a requested persisted-state transition is invalid."""


class LegacyLifecycleNotFoundError(Exception):
    """Raised for missing and foreign-owned Legacies alike."""


class LegacyArchivedError(Exception):
    """Raised when an archived Legacy is used by an active-only feature."""


class LegacyDeletionConfirmationError(ValueError):
    """Raised when permanent-deletion confirmation does not match."""


@dataclass(frozen=True)
class LegacyLifecycleCapabilities:
    """Business capabilities associated with one persisted lifecycle state."""

    visible_in_normal_lists: bool
    editable: bool
    companion_available: bool
    story_sessions_allowed: bool
    dashboard_available: bool
    read_only: bool
    recoverable: bool


class LegacyLifecycleService:
    """Single policy boundary for future Legacy lifecycle operations."""

    _CAPABILITIES = {
        LegacyStatus.ACTIVE: LegacyLifecycleCapabilities(
            visible_in_normal_lists=True,
            editable=True,
            companion_available=True,
            story_sessions_allowed=True,
            dashboard_available=True,
            read_only=False,
            recoverable=False,
        ),
        LegacyStatus.ARCHIVED: LegacyLifecycleCapabilities(
            visible_in_normal_lists=False,
            editable=False,
            companion_available=False,
            story_sessions_allowed=False,
            dashboard_available=True,
            read_only=True,
            recoverable=True,
        ),
    }
    _ALLOWED_TRANSITIONS = frozenset(
        {
            (LegacyStatus.ACTIVE, LegacyStatus.ARCHIVED),
            (LegacyStatus.ARCHIVED, LegacyStatus.ACTIVE),
        }
    )

    def capabilities(
        self,
        status: LegacyStatus,
    ) -> LegacyLifecycleCapabilities:
        """Return policy metadata for a canonical persisted state."""
        try:
            return self._CAPABILITIES[status]
        except (KeyError, TypeError) as exc:
            raise LegacyLifecycleTransitionError(
                "Unsupported persisted Legacy lifecycle state."
            ) from exc

    def validate_transition(
        self,
        current: LegacyStatus,
        target: LegacyStatus,
    ) -> None:
        """Validate a future transition without mutating a Legacy."""
        if (current, target) not in self._ALLOWED_TRANSITIONS:
            raise LegacyLifecycleTransitionError(
                f"Legacy transition from {current!s} to {target!s} "
                "is not allowed."
            )

    def is_transition_allowed(
        self,
        current: LegacyStatus,
        target: LegacyStatus,
    ) -> bool:
        """Return a side-effect-free transition-policy decision."""
        return (current, target) in self._ALLOWED_TRANSITIONS

    def archive(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
    ) -> LegacyLifecycleResponse:
        return self._transition(
            db,
            user_id=user_id,
            legacy_id=legacy_id,
            target=LegacyStatus.ARCHIVED,
        )

    def restore(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
    ) -> LegacyLifecycleResponse:
        return self._transition(
            db,
            user_id=user_id,
            legacy_id=legacy_id,
            target=LegacyStatus.ACTIVE,
        )

    def delete(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        confirmation_text: str,
    ) -> None:
        """Permanently delete one owner-scoped Legacy atomically."""
        legacy = LegacyCRUD.get_user_legacy_for_update(
            db,
            legacy_id,
            user_id,
        )
        if legacy is None:
            raise LegacyLifecycleNotFoundError("Legacy was not found.")
        if confirmation_text.strip() != legacy.display_name:
            raise LegacyDeletionConfirmationError(
                "Confirmation text does not match the Legacy name."
            )
        try:
            LegacyCRUD.delete_legacy_graph(db, legacy)
            db.commit()
        except Exception:
            db.rollback()
            raise

    def _transition(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        target: LegacyStatus,
    ) -> LegacyLifecycleResponse:
        legacy = LegacyCRUD.get_user_legacy_for_update(
            db,
            legacy_id,
            user_id,
        )
        if legacy is None:
            raise LegacyLifecycleNotFoundError("Legacy was not found.")
        if legacy.status == target:
            return LegacyLifecycleResponse.model_validate(legacy)
        self.validate_transition(legacy.status, target)
        try:
            LegacyCRUD.apply_status_transition(
                db,
                legacy,
                target=target,
                updated_at=datetime.now(timezone.utc),
            )
            db.commit()
            db.refresh(legacy)
        except Exception:
            db.rollback()
            raise
        return LegacyLifecycleResponse.model_validate(legacy)

    def require_active(self, legacy) -> None:
        """Enforce active-only capabilities on an already owned Legacy."""
        if legacy.status == LegacyStatus.ARCHIVED:
            raise LegacyArchivedError(
                "Restore this Legacy before continuing."
            )
