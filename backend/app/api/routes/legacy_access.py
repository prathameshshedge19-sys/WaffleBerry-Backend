from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.viewer import LegacyViewerAccess, ViewerAccessStatus
from app.schemas.legacy_access import LegacyAccessIdentity, LegacyAccessPanelResponse, LegacyCodeInput
from app.services.authorization import require_legacy, require_persona_legacy
from app.services.access_management import record_access_event
from app.services.legacy_access import (
    INVALID_VIEWER_CODE_MESSAGE,
    decrypt_viewer_code,
    grant_viewer_access,
    legacy_for_viewer_code,
    rotate_viewer_code,
    viewer_code_attempt_limiter,
)


router = APIRouter(prefix="/legacy-access", tags=["Legacy persona access"])


def _identity(legacy) -> dict:
    return {"legacy_id": legacy.id, "subject_name": legacy.subject_name or "this Legacy"}


def _invalid_code() -> HTTPException:
    return HTTPException(status_code=404, detail=INVALID_VIEWER_CODE_MESSAGE)


@router.post("/preview", response_model=LegacyAccessIdentity)
def preview_legacy_code(payload: LegacyCodeInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not viewer_code_attempt_limiter.allow(user.id):
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait and try again.")
    legacy = legacy_for_viewer_code(db, payload.code)
    if legacy is None:
        raise _invalid_code()
    viewer_code_attempt_limiter.clear(user.id)
    return _identity(legacy)


@router.post("/join", response_model=LegacyAccessIdentity)
def join_legacy_access(payload: LegacyCodeInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not viewer_code_attempt_limiter.allow(user.id):
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait and try again.")
    legacy = legacy_for_viewer_code(db, payload.code)
    if legacy is None:
        raise _invalid_code()
    access = grant_viewer_access(db, legacy, user.id)
    if access.status == ViewerAccessStatus.REVOKED.value:
        raise HTTPException(status_code=403, detail="The owner must restore your Legacy access or send a new invitation.")
    record_access_event(db, legacy.id, "viewer_joined_by_code", actor_user_id=user.id, target_user_id=user.id)
    db.commit()
    viewer_code_attempt_limiter.clear(user.id)
    return _identity(legacy)


@router.get("/{legacy_id}/identity", response_model=LegacyAccessIdentity)
def legacy_access_identity(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _identity(require_persona_legacy(db, user.id, legacy_id))


@router.get("/legacies/{legacy_id}", response_model=LegacyAccessPanelResponse)
def legacy_access_panel(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True)
    accesses = db.scalars(select(LegacyViewerAccess).options(joinedload(LegacyViewerAccess.user)).where(
        LegacyViewerAccess.legacy_id == legacy.id,
    ).order_by(LegacyViewerAccess.joined_at.desc())).all()
    return {
        "legacy_id": legacy.id,
        "code": decrypt_viewer_code(legacy.viewer_code_ciphertext) if legacy.viewer_code_enabled else None,
        "code_hint": legacy.viewer_code_hint,
        "code_enabled": legacy.viewer_code_enabled,
        "viewers": [{"access_id": item.id, "user_id": item.user_id, "full_name": item.user.full_name, "email": item.user.email, "status": item.status, "joined_at": item.joined_at} for item in accesses],
    }


@router.post("/legacies/{legacy_id}/code", response_model=LegacyAccessPanelResponse)
def regenerate_legacy_code(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True)
    rotate_viewer_code(db, legacy)
    record_access_event(db, legacy.id, "viewer_code_regenerated", actor_user_id=user.id)
    db.commit()
    return legacy_access_panel(legacy_id, user, db)


@router.delete("/legacies/{legacy_id}/code", status_code=status.HTTP_204_NO_CONTENT)
def disable_legacy_code(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True)
    legacy.viewer_code_enabled = False
    record_access_event(db, legacy.id, "viewer_code_disabled", actor_user_id=user.id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
