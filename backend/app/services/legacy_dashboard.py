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
        story_session_categories = [
            self._build_story_session_category(category)
            for category in (
                LegacyDashboardCRUD.get_story_session_category_counts(
                    db, legacy_id
                )
            )
        ]
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
            story_session_categories=story_session_categories,
            linked_conversations=(
                LegacyDashboardCRUD.count_linked_conversations(
                    db, legacy_id
                )
            ),
            has_approved_memories=memories["approved"] > 0,
        )

    @staticmethod
    def _build_story_session_category(
        category: dict[str, str | int | None],
    ) -> dict[str, str | int]:
        """Calculate factual session progress for one normalized chapter."""
        total = int(category["total_sessions"] or 0)
        completed = int(category["completed_sessions"] or 0)
        category_id = str(category["id"])
        title = category_id.replace("-", " ").replace("_", " ").title()
        percentage = (
            round((completed / total) * 100)
            if total > 0
            else 0
        )
        return {
            "id": category_id,
            "title": title,
            "session_completion_percentage": percentage,
            "completed_sessions": completed,
            "total_sessions": total,
        }
