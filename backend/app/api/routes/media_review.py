from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.legacy import Legacy
from app.models.user import User
from app.schemas.media_intelligence import CandidateResponse, CandidateReviewRequest, ReviewDraftRequest
from app.services.memory import MemoryProviderError, get_memory_provider
from app.services.media_review import MediaReviewService, _owner_legacy
from app.api.routes.media_sources import _private


router = APIRouter(prefix="/media-review", tags=["Media source review"], dependencies=[Depends(_private)])


def _service() -> MediaReviewService:
    return MediaReviewService()


def _enabled() -> None:
    if not get_settings().media_enabled:
        raise HTTPException(404, detail="Media sources are not enabled.")


@router.get("/legacies/{legacy_id}/candidates", response_model=list[CandidateResponse])
def list_candidates(legacy_id: int, source_id: str | None = Query(default=None), user: User = Depends(get_current_user), db: Session = Depends(get_db), service: MediaReviewService = Depends(_service)):
    _enabled()
    return service.list_candidates(db, user, legacy_id, source_id)


@router.get("/legacies/{legacy_id}/candidates/{candidate_id}", response_model=CandidateResponse)
def get_candidate(legacy_id: int, candidate_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db), service: MediaReviewService = Depends(_service)):
    _enabled()
    return service.get_candidate(db, user, legacy_id, candidate_id)


@router.put("/legacies/{legacy_id}/candidates/{candidate_id}/draft", response_model=CandidateResponse)
async def edit_candidate(payload: ReviewDraftRequest, legacy_id: int, candidate_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db), service: MediaReviewService = Depends(_service)):
    _enabled()
    try:
        legacy = _owner_legacy(db, user.id, legacy_id, lock=False)
        service.get_candidate(db, user, legacy_id, candidate_id)
        provider = get_memory_provider()
        normalized = await provider.canonicalize_edit(legacy, payload.canonical_text)
    except MemoryProviderError:
        raise HTTPException(503, detail={"code": "memory_provider_unavailable", "message": "The edit preview is temporarily unavailable."}) from None
    return await service.prepare_edit(db, user, legacy_id, candidate_id, canonical_text=normalized.canonical_text, category=payload.category, expected_version=payload.expected_version, entities=[item.model_dump(mode="json") for item in normalized.entities], source_language=normalized.source_language)


@router.post("/legacies/{legacy_id}/candidates/{candidate_id}/review", response_model=CandidateResponse)
async def review_candidate(payload: CandidateReviewRequest, legacy_id: int, candidate_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db), service: MediaReviewService = Depends(_service)):
    _enabled()
    from app.services.memory import LivingMemoryService
    _owner_legacy(db, user.id, legacy_id, lock=False)
    try:
        if payload.action != "skip":
            service.memory = LivingMemoryService(get_memory_provider())
        return await service.review(db, user, legacy_id, candidate_id, action=payload.action, expected_version=payload.expected_version, review_request_key=payload.review_request_key)
    except MemoryProviderError:
        raise HTTPException(503, detail={"code": "memory_provider_unavailable", "message": "Preservation is temporarily unavailable."}) from None


@router.get("/legacies/{legacy_id}/sources/{source_id}/candidates", response_model=list[CandidateResponse])
def list_source_candidates(legacy_id: int, source_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db), service: MediaReviewService = Depends(_service)):
    _enabled()
    return service.list_candidates(db, user, legacy_id, source_id)
