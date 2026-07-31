"""Persisted Legacy and Guided Story APIs with background extraction."""

import asyncio
import json
import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.crud.memory import (
    LegacyCRUD,
    MemoryPersistenceError,
    StorySessionCRUD,
)
from app.db import get_db
from app.dependencies.ai import get_chat_service
from app.dependencies.auth import get_current_user
from app.models.memory import (
    StoryMessage,
    StoryMessageRole,
    StorySessionStatus,
)
from app.models.user import User
from app.schemas.memory import (
    ExtractionRunResponse,
    LegacyCreate,
    LegacyDashboardResponse,
    LegacyResponse,
    LegacySettingsResponse,
    LegacySettingsUpdate,
    PersistedStoryStreamRequest,
    StoryMessageCreate,
    StorySessionCompletionResponse,
    StorySessionCreate,
    StorySessionResponse,
)
from app.schemas.user import StoryGuideMessage
from app.services.ai.exceptions import AIServiceError
from app.services.chat_service import ChatService
from app.services.memory.background_extraction import (
    StoryExtractionConflictError,
    StoryExtractionNotFoundError,
    StoryExtractionService,
    execute_story_extraction,
)
from app.services.legacy_dashboard import (
    LegacyDashboardNotFoundError,
    LegacyDashboardService,
)
from app.services.legacy_settings import (
    LegacySettingsConflictError,
    LegacySettingsNotFoundError,
    LegacySettingsService,
)


router = APIRouter()
logger = logging.getLogger(__name__)


def _event(event: str, payload: dict) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


@router.post("/legacies", response_model=LegacyResponse)
def synchronize_legacy(
    legacy: LegacyCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Idempotently persist one browser Legacy correlation for its owner."""
    return LegacyCRUD.create_legacy(db, current_user.user_id, legacy)


@router.get("/legacies", response_model=list[LegacyResponse])
def list_legacies(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return LegacyCRUD.get_user_legacies(db, current_user.user_id)


@router.get("/legacies/{legacy_id}", response_model=LegacyResponse)
def get_legacy(
    legacy_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    legacy = LegacyCRUD.get_user_legacy(
        db, legacy_id, current_user.user_id
    )
    if legacy is None:
        raise HTTPException(status_code=404, detail="Legacy was not found.")
    return legacy


@router.patch(
    "/legacies/{legacy_id}",
    response_model=LegacySettingsResponse,
)
def update_legacy_settings(
    legacy_id: int,
    changes: LegacySettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update owner-scoped Legacy identity fields only."""
    try:
        return LegacySettingsService().update(
            db,
            user_id=current_user.user_id,
            legacy_id=legacy_id,
            changes=changes,
        )
    except LegacySettingsConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "legacy_changed",
                "message": (
                    "This Legacy changed elsewhere. "
                    "Refresh and try again."
                ),
            },
        ) from None
    except LegacySettingsNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Legacy was not found.",
        ) from None


@router.get(
    "/legacies/{legacy_id}/dashboard",
    response_model=LegacyDashboardResponse,
)
def get_legacy_dashboard(
    legacy_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return an owner-scoped summary derived from persisted Legacy data."""
    try:
        return LegacyDashboardService().get_summary(
            db,
            user_id=current_user.user_id,
            legacy_id=legacy_id,
        )
    except LegacyDashboardNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Legacy was not found.",
        ) from None


@router.post(
    "/legacies/{legacy_id}/story-sessions",
    response_model=StorySessionResponse,
)
def create_or_resume_story_session(
    legacy_id: int,
    story: StorySessionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return StorySessionCRUD.get_or_create_active_story_session(
            db, legacy_id, current_user.user_id, story
        )
    except MemoryPersistenceError:
        raise HTTPException(
            status_code=404, detail="Legacy was not found."
        ) from None


@router.get(
    "/legacies/{legacy_id}/story-sessions/{story_session_id}",
    response_model=StorySessionResponse,
)
def get_story_session(
    legacy_id: int,
    story_session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if LegacyCRUD.get_user_legacy(
        db, legacy_id, current_user.user_id
    ) is None:
        raise HTTPException(status_code=404, detail="Story was not found.")
    story = StorySessionCRUD.get_legacy_story_session(
        db, story_session_id, legacy_id
    )
    if story is None:
        raise HTTPException(status_code=404, detail="Story was not found.")
    return story


@router.post(
    "/legacies/{legacy_id}/story-sessions/{story_session_id}/messages/stream"
)
async def stream_persisted_story(
    legacy_id: int,
    story_session_id: int,
    payload: PersistedStoryStreamRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    chat_service: ChatService = Depends(get_chat_service),
):
    legacy = LegacyCRUD.get_user_legacy(
        db, legacy_id, current_user.user_id
    )
    story = StorySessionCRUD.get_legacy_story_session(
        db, story_session_id, legacy_id
    )
    if legacy is None or story is None:
        raise HTTPException(status_code=404, detail="Story was not found.")
    user_key = f"user:{payload.client_message_id}"
    assistant_key = f"assistant:{payload.client_message_id}"
    existing_assistant = (
        db.query(StoryMessage)
        .filter(
            StoryMessage.story_session_id == story_session_id,
            StoryMessage.client_message_id == assistant_key,
        )
        .first()
    )
    if existing_assistant is not None:
        async def replay():
            yield _event("start", {})
            yield _event("complete", {"text": existing_assistant.content})
        return StreamingResponse(replay(), media_type="text/event-stream")

    if payload.content:
        if story.status == StorySessionStatus.COMPLETED:
            story.status = StorySessionStatus.IN_PROGRESS
            story.completed_at = None
            db.commit()
        StorySessionCRUD.append_story_message(
            db,
            story_session_id,
            legacy_id,
            StoryMessageCreate(
                role=StoryMessageRole.USER,
                content=payload.content,
            ),
            client_message_id=user_key,
        )
    history = StorySessionCRUD.get_story_messages(
        db, story_session_id, legacy_id
    )
    prepared_history = [
        StoryGuideMessage(
            role=(
                message.role.value
                if hasattr(message.role, "value")
                else str(message.role)
            ),
            content=message.content,
        )
        for message in history
    ]
    chapter_label = story.title or story.chapter_key
    relationship = legacy.relationship
    display_name = legacy.display_name
    db.commit()
    response_stream = chat_service.stream_story_response(
        prepared_history,
        chapter=chapter_label,
        relationship=relationship,
        display_name=display_name,
    )

    async def events():
        chunks: list[str] = []
        try:
            yield _event("start", {})
            async for delta in response_stream:
                if await request.is_disconnected():
                    return
                chunks.append(delta)
                yield _event("delta", {"text": delta})
            complete = "".join(chunks).strip()
            if not complete:
                raise AIServiceError("Story response was empty.")
            StorySessionCRUD.append_story_message(
                db,
                story_session_id,
                legacy_id,
                StoryMessageCreate(
                    role=StoryMessageRole.ASSISTANT,
                    content=complete,
                ),
                client_message_id=assistant_key,
            )
            yield _event("complete", {"text": complete})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "Persisted Story Guide stream failed safely.",
                extra={
                    "legacy_id": legacy_id,
                    "story_session_id": story_session_id,
                    "error_category": "story_stream_failed",
                },
            )
            if not await request.is_disconnected():
                yield _event(
                    "error",
                    {
                        "code": "story_stream_failed",
                        "message": (
                            "Berry's response was interrupted. "
                            "Your story was still saved."
                        ),
                    },
                )
        finally:
            close = getattr(response_stream, "aclose", None)
            if close is not None:
                await close()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/legacies/{legacy_id}/story-sessions/{story_session_id}/complete",
    response_model=StorySessionCompletionResponse,
)
def complete_story_session(
    legacy_id: int,
    story_session_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = StoryExtractionService()
    try:
        story, run = service.complete(
            db,
            user_id=current_user.user_id,
            legacy_id=legacy_id,
            story_session_id=story_session_id,
        )
    except StoryExtractionNotFoundError:
        raise HTTPException(status_code=404, detail="Story was not found.") from None
    except StoryExtractionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if run.status.value == "pending":
        background_tasks.add_task(
            execute_story_extraction,
            extraction_run_id=run.extraction_run_id,
            user_id=current_user.user_id,
            legacy_id=legacy_id,
            story_session_id=story_session_id,
        )
    return StorySessionCompletionResponse(
        story_session=story,
        extraction_run=run,
    )


@router.get(
    "/legacies/{legacy_id}/story-sessions/{story_session_id}/extraction-runs/{extraction_run_id}",
    response_model=ExtractionRunResponse,
)
def get_extraction_run(
    legacy_id: int,
    story_session_id: int,
    extraction_run_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return StoryExtractionService().get_run(
            db,
            user_id=current_user.user_id,
            legacy_id=legacy_id,
            story_session_id=story_session_id,
            extraction_run_id=extraction_run_id,
        )
    except StoryExtractionNotFoundError:
        raise HTTPException(status_code=404, detail="Run was not found.") from None


@router.post(
    "/legacies/{legacy_id}/story-sessions/{story_session_id}/extraction-runs/{extraction_run_id}/retry",
    response_model=ExtractionRunResponse,
)
def retry_extraction_run(
    legacy_id: int,
    story_session_id: int,
    extraction_run_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = StoryExtractionService()
    try:
        run = service.prepare_retry(
            db,
            user_id=current_user.user_id,
            legacy_id=legacy_id,
            story_session_id=story_session_id,
            extraction_run_id=extraction_run_id,
        )
    except StoryExtractionNotFoundError:
        raise HTTPException(status_code=404, detail="Run was not found.") from None
    except StoryExtractionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    background_tasks.add_task(
        execute_story_extraction,
        extraction_run_id=run.extraction_run_id,
        user_id=current_user.user_id,
        legacy_id=legacy_id,
        story_session_id=story_session_id,
    )
    return run
