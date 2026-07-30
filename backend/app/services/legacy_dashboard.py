"""Business assembly for the authenticated My Legacy dashboard."""

from sqlalchemy.orm import Session

from app.crud.legacy_dashboard import LegacyDashboardCRUD
from app.crud.memory import LegacyCRUD
from app.schemas.memory import LegacyDashboardResponse


class LegacyDashboardNotFoundError(Exception):
    """Raised when an owner cannot access the requested Legacy."""


class LegacyDashboardService:
    """Build owner-scoped dashboard projections from normalized records."""

    def get_summary(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
    ) -> LegacyDashboardResponse:
        legacy = LegacyCRUD.get_user_legacy(db, legacy_id, user_id)
        if legacy is None:
            raise LegacyDashboardNotFoundError(
                "Legacy was not found."
            )

        stories = LegacyDashboardCRUD.get_story_counts(db, legacy_id)
        memories = LegacyDashboardCRUD.get_memory_counts(db, legacy_id)
        extraction = LegacyDashboardCRUD.get_extraction_counts(
            db, legacy_id
        )
        return LegacyDashboardResponse(
            legacy_id=legacy.legacy_id,
            title=legacy.display_name,
            relationship=legacy.relationship,
            status=legacy.status,
            created_at=legacy.created_at,
            updated_at=legacy.updated_at,
            stories=stories,
            memories=memories,
            extraction=extraction,
            linked_conversations=(
                LegacyDashboardCRUD.count_linked_conversations(
                    db, legacy_id
                )
            ),
            has_approved_memories=memories["approved"] > 0,
        )
