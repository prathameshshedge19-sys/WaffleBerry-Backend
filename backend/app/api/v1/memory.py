"""Authenticated, owner-scoped Memory Review API."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies.auth import get_current_user
from app.models.memory import MemoryReviewStatus, MemoryType
from app.models.user import User
from app.schemas.memory import (
    MEMORY_CATEGORIES,
    MemoryReviewActionRequest,
    MemoryReviewEditRequest,
    MemoryReviewListResponse,
    MemoryReviewResponse,
)
from app.services.memory.review import (
    MemoryReviewConflictError,
    MemoryReviewDuplicateError,
    MemoryReviewNotFoundError,
    MemoryReviewService,
)


router = APIRouter()


def get_memory_review_service() -> MemoryReviewService:
    """Create a request-safe stateless review coordinator."""
    return MemoryReviewService()


def _safe_review_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MemoryReviewDuplicateError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "equivalent_memory_exists",
                "message": (
                    "An equivalent memory already exists. "
                    "Refresh before deciding what to keep."
                ),
            },
        )
    if isinstance(exc, MemoryReviewConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "memory_changed",
                "message": (
                    "This memory changed in another review. "
                    "Refresh and try again."
                ),
            },
        )
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Legacy or memory was not found.",
    )


@router.get(
    "/legacies/{legacy_id}/memories/review",
    response_model=MemoryReviewListResponse,
)
def list_memory_review(
    legacy_id: int,
    review_status: MemoryReviewStatus = Query(
        default=MemoryReviewStatus.CANDIDATE
    ),
    category: str | None = Query(default=None),
    memory_type: MemoryType | None = Query(default=None),
    source_type: str | None = Query(default=None),
    has_contradiction: bool | None = Query(default=None),
    has_enrichment: bool | None = Query(default=None),
    story_session_id: int | None = Query(default=None, gt=0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: MemoryReviewService = Depends(get_memory_review_service),
):
    """List one owner's memories, defaulting to candidates needing review."""
    if category is not None and category not in MEMORY_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported memory category.",
        )
    try:
        items, total = service.list_memories(
            db,
            user_id=current_user.user_id,
            legacy_id=legacy_id,
            review_status=review_status,
            category=category,
            memory_type=memory_type,
            source_type=source_type,
            has_contradiction=has_contradiction,
            has_enrichment=has_enrichment,
            story_session_id=story_session_id,
            offset=offset,
            limit=limit,
        )
    except MemoryReviewNotFoundError as exc:
        raise _safe_review_error(exc) from None
    return MemoryReviewListResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/legacies/{legacy_id}/memories/{memory_id}",
    response_model=MemoryReviewResponse,
)
def get_memory_review(
    legacy_id: int,
    memory_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: MemoryReviewService = Depends(get_memory_review_service),
):
    try:
        return service.get_memory(
            db,
            user_id=current_user.user_id,
            legacy_id=legacy_id,
            memory_id=memory_id,
        )
    except MemoryReviewNotFoundError as exc:
        raise _safe_review_error(exc) from None


@router.post(
    "/legacies/{legacy_id}/memories/{memory_id}/approve",
    response_model=MemoryReviewResponse,
)
def approve_memory(
    legacy_id: int,
    memory_id: int,
    action: MemoryReviewActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: MemoryReviewService = Depends(get_memory_review_service),
):
    try:
        return service.approve(
            db,
            user_id=current_user.user_id,
            legacy_id=legacy_id,
            memory_id=memory_id,
            expected_updated_at=action.expected_updated_at,
        )
    except (MemoryReviewNotFoundError, MemoryReviewConflictError) as exc:
        raise _safe_review_error(exc) from None


@router.post(
    "/legacies/{legacy_id}/memories/{memory_id}/reject",
    response_model=MemoryReviewResponse,
)
def reject_memory(
    legacy_id: int,
    memory_id: int,
    action: MemoryReviewActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: MemoryReviewService = Depends(get_memory_review_service),
):
    try:
        return service.reject(
            db,
            user_id=current_user.user_id,
            legacy_id=legacy_id,
            memory_id=memory_id,
            expected_updated_at=action.expected_updated_at,
        )
    except (MemoryReviewNotFoundError, MemoryReviewConflictError) as exc:
        raise _safe_review_error(exc) from None


@router.patch(
    "/legacies/{legacy_id}/memories/{memory_id}",
    response_model=MemoryReviewResponse,
)
def edit_memory(
    legacy_id: int,
    memory_id: int,
    edit: MemoryReviewEditRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: MemoryReviewService = Depends(get_memory_review_service),
):
    try:
        return service.edit(
            db,
            user_id=current_user.user_id,
            legacy_id=legacy_id,
            memory_id=memory_id,
            edit=edit,
        )
    except (
        MemoryReviewNotFoundError,
        MemoryReviewConflictError,
    ) as exc:
        raise _safe_review_error(exc) from None
