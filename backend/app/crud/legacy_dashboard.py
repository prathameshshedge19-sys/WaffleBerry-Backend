"""Read-only data access for owner-scoped My Legacy summaries."""

from sqlalchemy import case, distinct, func
from sqlalchemy.orm import Session

from app.models.memory import (
    Memory,
    MemoryExtractionRun,
    MemoryExtractionRunStatus,
    MemoryReviewStatus,
    MemoryType,
    StoryMessage,
    StoryMessageRole,
    StorySession,
    StorySessionStatus,
)
from app.models.user import Conversation


class LegacyDashboardCRUD:
    """Aggregate persisted facts without introducing summary columns."""

    @staticmethod
    def get_story_counts(db: Session, legacy_id: int) -> dict[str, int]:
        row = (
            db.query(
                func.count(StorySession.story_session_id),
                func.count(distinct(StorySession.chapter_key)),
                func.count(
                    case(
                        (
                            StorySession.status
                            == StorySessionStatus.IN_PROGRESS,
                            1,
                        )
                    )
                ),
                func.count(
                    case(
                        (
                            StorySession.status == StorySessionStatus.PAUSED,
                            1,
                        )
                    )
                ),
                func.count(
                    case(
                        (
                            StorySession.status
                            == StorySessionStatus.COMPLETED,
                            1,
                        )
                    )
                ),
            )
            .filter(StorySession.legacy_id == legacy_id)
            .one()
        )
        message_row = (
            db.query(
                func.count(StoryMessage.story_message_id),
                func.count(
                    case(
                        (
                            StoryMessage.role == StoryMessageRole.USER,
                            1,
                        )
                    )
                ),
            )
            .join(
                StorySession,
                StorySession.story_session_id
                == StoryMessage.story_session_id,
            )
            .filter(StorySession.legacy_id == legacy_id)
            .one()
        )
        return {
            "total_sessions": row[0],
            "distinct_chapters": row[1],
            "in_progress_sessions": row[2],
            "paused_sessions": row[3],
            "completed_sessions": row[4],
            "total_messages": message_row[0],
            "contributed_messages": message_row[1],
        }

    @staticmethod
    def get_story_session_category_counts(
        db: Session,
        legacy_id: int,
    ) -> list[dict[str, str | int | None]]:
        """Group existing sessions by a normalized persisted chapter key."""
        normalized_key = func.lower(func.trim(StorySession.chapter_key))
        rows = (
            db.query(
                normalized_key,
                func.count(StorySession.story_session_id),
                func.count(
                    case(
                        (
                            StorySession.status
                            == StorySessionStatus.COMPLETED,
                            1,
                        )
                    )
                ),
            )
            .filter(StorySession.legacy_id == legacy_id)
            .group_by(normalized_key)
            .order_by(normalized_key.asc())
            .all()
        )
        return [
            {
                "id": row[0],
                "total_sessions": row[1],
                "completed_sessions": row[2],
            }
            for row in rows
        ]

    @staticmethod
    def get_memory_counts(db: Session, legacy_id: int) -> dict[str, int]:
        row = (
            db.query(
                func.count(Memory.memory_id),
                func.count(
                    case(
                        (
                            Memory.review_status
                            == MemoryReviewStatus.CANDIDATE,
                            1,
                        )
                    )
                ),
                func.count(
                    case(
                        (
                            Memory.review_status
                            == MemoryReviewStatus.APPROVED,
                            1,
                        )
                    )
                ),
                func.count(
                    case(
                        (
                            Memory.review_status
                            == MemoryReviewStatus.REJECTED,
                            1,
                        )
                    )
                ),
                func.count(
                    case(
                        (
                            Memory.review_status
                            == MemoryReviewStatus.SUPERSEDED,
                            1,
                        )
                    )
                ),
                func.count(
                    case((Memory.memory_type == MemoryType.ATOMIC, 1))
                ),
                func.count(
                    case((Memory.memory_type == MemoryType.NARRATIVE, 1))
                ),
            )
            .filter(Memory.legacy_id == legacy_id)
            .one()
        )
        keys = (
            "total",
            "candidate",
            "approved",
            "rejected",
            "superseded",
            "atomic",
            "narrative",
        )
        return dict(zip(keys, row))

    @staticmethod
    def get_extraction_counts(
        db: Session,
        legacy_id: int,
    ) -> dict[str, int]:
        row = (
            db.query(
                func.count(MemoryExtractionRun.extraction_run_id),
                func.count(
                    case(
                        (
                            MemoryExtractionRun.status
                            == MemoryExtractionRunStatus.PENDING,
                            1,
                        )
                    )
                ),
                func.count(
                    case(
                        (
                            MemoryExtractionRun.status
                            == MemoryExtractionRunStatus.RUNNING,
                            1,
                        )
                    )
                ),
                func.count(
                    case(
                        (
                            MemoryExtractionRun.status
                            == MemoryExtractionRunStatus.COMPLETED,
                            1,
                        )
                    )
                ),
                func.count(
                    case(
                        (
                            MemoryExtractionRun.status
                            == MemoryExtractionRunStatus.FAILED,
                            1,
                        )
                    )
                ),
            )
            .filter(MemoryExtractionRun.legacy_id == legacy_id)
            .one()
        )
        keys = (
            "total_runs",
            "pending_runs",
            "running_runs",
            "completed_runs",
            "failed_runs",
        )
        return dict(zip(keys, row))

    @staticmethod
    def count_linked_conversations(db: Session, legacy_id: int) -> int:
        return (
            db.query(func.count(Conversation.conversation_id))
            .filter(Conversation.legacy_id == legacy_id)
            .scalar()
            or 0
        )
