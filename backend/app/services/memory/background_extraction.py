"""Durable Story completion scheduling and independent-session extraction."""

import logging
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crud.memory import LegacyCRUD, StorySessionCRUD
from app.db import SessionLocal
from app.dependencies.ai import get_memory_storage_pipeline
from app.models.memory import (
    MemoryExtractionRun,
    MemoryExtractionRunStatus,
    LegacyStatus,
    StoryMessage,
    StorySessionStatus,
)


logger = logging.getLogger(__name__)


class StoryExtractionError(Exception):
    pass


class StoryExtractionNotFoundError(StoryExtractionError):
    pass


class StoryExtractionConflictError(StoryExtractionError):
    pass


class StoryExtractionService:
    """Centralize Story lifecycle and extraction-run idempotency."""

    def complete(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        story_session_id: int,
    ) -> tuple[object, MemoryExtractionRun]:
        legacy = LegacyCRUD.get_user_legacy(db, legacy_id, user_id)
        if legacy is None:
            raise StoryExtractionNotFoundError("Legacy was not found.")
        if legacy.status == LegacyStatus.ARCHIVED:
            raise StoryExtractionConflictError(
                "Restore this Legacy before continuing."
            )
        story = StorySessionCRUD.get_legacy_story_session(
            db, story_session_id, legacy_id
        )
        if story is None:
            raise StoryExtractionNotFoundError("Story Session was not found.")
        boundary = (
            db.query(func.max(StoryMessage.sequence))
            .filter(StoryMessage.story_session_id == story_session_id)
            .scalar()
            or 0
        )
        if boundary < 1:
            raise StoryExtractionConflictError(
                "A Story Session needs a saved message before completion."
            )
        run = (
            db.query(MemoryExtractionRun)
            .filter(
                MemoryExtractionRun.story_session_id == story_session_id,
                MemoryExtractionRun.message_boundary == boundary,
                MemoryExtractionRun.trigger_type == "session_completed",
            )
            .first()
        )
        if run is None:
            run = MemoryExtractionRun(
                legacy_id=legacy_id,
                story_session_id=story_session_id,
                message_boundary=boundary,
                trigger_type="session_completed",
            )
            db.add(run)
        story.status = StorySessionStatus.COMPLETED
        story.completed_at = datetime.now(timezone.utc)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            story = StorySessionCRUD.get_legacy_story_session(
                db, story_session_id, legacy_id
            )
            run = (
                db.query(MemoryExtractionRun)
                .filter(
                    MemoryExtractionRun.story_session_id
                    == story_session_id,
                    MemoryExtractionRun.message_boundary == boundary,
                    MemoryExtractionRun.trigger_type
                    == "session_completed",
                )
                .one()
            )
            story.status = StorySessionStatus.COMPLETED
            story.completed_at = datetime.now(timezone.utc)
            db.commit()
        db.refresh(story)
        db.refresh(run)
        return story, run

    def get_run(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        story_session_id: int,
        extraction_run_id: int,
    ) -> MemoryExtractionRun:
        if LegacyCRUD.get_user_legacy(db, legacy_id, user_id) is None:
            raise StoryExtractionNotFoundError("Run was not found.")
        run = (
            db.query(MemoryExtractionRun)
            .filter(
                MemoryExtractionRun.extraction_run_id == extraction_run_id,
                MemoryExtractionRun.legacy_id == legacy_id,
                MemoryExtractionRun.story_session_id == story_session_id,
            )
            .first()
        )
        if run is None:
            raise StoryExtractionNotFoundError("Run was not found.")
        return run

    def prepare_retry(self, db: Session, **scope) -> MemoryExtractionRun:
        run = self.get_run(db, **scope)
        legacy = LegacyCRUD.get_user_legacy(
            db,
            scope["legacy_id"],
            scope["user_id"],
        )
        if legacy.status == LegacyStatus.ARCHIVED:
            raise StoryExtractionConflictError(
                "Restore this Legacy before continuing."
            )
        if run.status in {
            MemoryExtractionRunStatus.RUNNING,
            MemoryExtractionRunStatus.COMPLETED,
            MemoryExtractionRunStatus.PENDING,
        }:
            raise StoryExtractionConflictError(
                "This extraction run cannot be retried in its current state."
            )
        run.status = MemoryExtractionRunStatus.PENDING
        run.last_error_code = None
        db.commit()
        db.refresh(run)
        return run


async def execute_story_extraction(
    *,
    extraction_run_id: int,
    user_id: int,
    legacy_id: int,
    story_session_id: int,
) -> None:
    """Run with immutable IDs and a fresh session, never request ORM state."""
    db = SessionLocal()
    try:
        run = (
            db.query(MemoryExtractionRun)
            .filter(
                MemoryExtractionRun.extraction_run_id == extraction_run_id,
                MemoryExtractionRun.legacy_id == legacy_id,
                MemoryExtractionRun.story_session_id == story_session_id,
            )
            .with_for_update()
            .first()
        )
        if run is None or run.status != MemoryExtractionRunStatus.PENDING:
            return
        legacy = LegacyCRUD.get_user_legacy(db, legacy_id, user_id)
        if legacy is None:
            run.status = MemoryExtractionRunStatus.FAILED
            run.last_error_code = "ownership_mismatch"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return
        if legacy.status == LegacyStatus.ARCHIVED:
            run.status = MemoryExtractionRunStatus.FAILED
            run.last_error_code = "legacy_archived"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return
        run.status = MemoryExtractionRunStatus.RUNNING
        run.attempt_count += 1
        run.started_at = datetime.now(timezone.utc)
        db.commit()
        try:
            report = await get_memory_storage_pipeline().process_story_session(
                db,
                user_id=user_id,
                legacy_id=legacy_id,
                story_session_id=story_session_id,
                metadata={
                    "extraction_run_id": extraction_run_id,
                    "message_boundary": run.message_boundary,
                },
            )
            run = db.get(MemoryExtractionRun, extraction_run_id)
            run.status = (
                MemoryExtractionRunStatus.FAILED
                if report.errors
                else MemoryExtractionRunStatus.COMPLETED
            )
            run.candidate_count = report.candidates_extracted
            run.memories_created = report.memories_created
            run.last_error_code = (
                "partial_persistence_failure"
                if report.errors
                else None
            )
        except Exception as exc:
            db.rollback()
            run = db.get(MemoryExtractionRun, extraction_run_id)
            run.status = MemoryExtractionRunStatus.FAILED
            run.last_error_code = _safe_error_code(exc)
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(
            "story_memory_extraction_finished",
            extra={
                "legacy_id": legacy_id,
                "story_session_id": story_session_id,
                "extraction_run_id": extraction_run_id,
                "message_boundary": run.message_boundary,
                "attempt_count": run.attempt_count,
                "run_status": run.status.value,
                "candidate_count": run.candidate_count,
                "memories_created": run.memories_created,
                "error_category": run.last_error_code,
            },
        )
    finally:
        db.close()


def _safe_error_code(exc: Exception) -> str:
    name = type(exc).__name__.casefold()
    if "ownership" in name or "crosslegacy" in name or "source" in name:
        return "invalid_source"
    if "response" in name or "validation" in name:
        return "invalid_extraction_response"
    if "provider" in name or "connection" in name or "timeout" in name:
        return "provider_unavailable"
    if "provenance" in name:
        return "provenance_failure"
    return "extraction_failed"
