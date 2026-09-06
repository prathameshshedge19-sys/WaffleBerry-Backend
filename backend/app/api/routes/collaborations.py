from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.collaboration import CollaboratorStatus, LegacyCollaborator
from app.models.user import User
from app.schemas.collaboration import (
    CollaborationJoinResponse,
    CollaborationPanelResponse,
    CollaborationPreview,
    CollaboratorCodeInput,
)
from app.services.authorization import require_legacy
from app.services.access_management import record_access_event
from app.services.collaboration import (
    INVALID_CODE_MESSAGE,
    decrypt_code,
    join_attempt_limiter,
    join_legacy,
    legacy_for_code,
    rotate_code,
)


router = APIRouter(prefix="/collaborations", tags=["Legacy collaboration"])


def _invalid_code() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=INVALID_CODE_MESSAGE)


def _identity(legacy, access_role: str | None = None) -> dict:
    return {
        "legacy_id": legacy.id,
        "subject_name": legacy.subject_name or "this Legacy",
        "owner_name": legacy.owner.full_name,
        "access_role": access_role,
    }


@router.post("/preview", response_model=CollaborationPreview)
def preview_collaboration(payload: CollaboratorCodeInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not join_attempt_limiter.allow(user.id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts. Please wait and try again.")
    legacy = legacy_for_code(db, payload.code)
    if legacy is None:
        raise _invalid_code()
    join_attempt_limiter.clear(user.id)
    existing = db.scalar(select(LegacyCollaborator).where(LegacyCollaborator.legacy_id == legacy.id, LegacyCollaborator.user_id == user.id))
    role = "owner" if legacy.owner_user_id == user.id else (existing.status if existing else None)
    return _identity(legacy, role)


@router.post("/join", response_model=CollaborationJoinResponse)
def join_collaboration(payload: CollaboratorCodeInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not join_attempt_limiter.allow(user.id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts. Please wait and try again.")
    legacy = legacy_for_code(db, payload.code)
    if legacy is None:
        raise _invalid_code()
    role, _membership = join_legacy(db, legacy, user.id)
    if role == "revoked":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The owner must restore your collaboration access.")
    join_attempt_limiter.clear(user.id)
    user.active_legacy_id = legacy.id
    if role == "collaborator":
        record_access_event(db, legacy.id, "collaborator_joined_by_code", actor_user_id=user.id, target_user_id=user.id)
    db.commit()
    return _identity(legacy, role)


@router.get("/legacies/{legacy_id}", response_model=CollaborationPanelResponse)
def collaboration_panel(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True)
    memberships = db.scalars(
        select(LegacyCollaborator)
        .options(joinedload(LegacyCollaborator.user))
        .where(LegacyCollaborator.legacy_id == legacy.id)
        .order_by(LegacyCollaborator.status, LegacyCollaborator.joined_at.desc())
    ).all()
    return {
        "legacy_id": legacy.id,
        "code": decrypt_code(legacy.collaborator_code_ciphertext) if legacy.collaborator_code_enabled else None,
        "code_hint": legacy.collaborator_code_hint,
        "code_enabled": legacy.collaborator_code_enabled,
        "collaborators": [
            {
                "membership_id": item.id,
                "user_id": item.user_id,
                "full_name": item.user.full_name,
                "email": item.user.email,
                "status": item.status,
                "joined_at": item.joined_at,
            }
            for item in memberships
        ],
    }


@router.post("/legacies/{legacy_id}/code", response_model=CollaborationPanelResponse)
def regenerate_collaboration_code(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True)
    rotate_code(db, legacy)
    record_access_event(db, legacy.id, "collaborator_code_regenerated", actor_user_id=user.id)
    db.commit()
    return collaboration_panel(legacy_id, user, db)


@router.delete("/legacies/{legacy_id}/code", status_code=status.HTTP_204_NO_CONTENT)
def disable_collaboration_code(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True)
    legacy.collaborator_code_enabled = False
    record_access_event(db, legacy.id, "collaborator_code_disabled", actor_user_id=user.id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/legacies/{legacy_id}/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_collaborator(legacy_id: int, membership_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True)
    membership = db.scalar(select(LegacyCollaborator).where(LegacyCollaborator.id == membership_id, LegacyCollaborator.legacy_id == legacy.id))
    if membership is None:
        raise HTTPException(status_code=404, detail="Collaborator not found.")
    membership.status = CollaboratorStatus.REVOKED.value
    from app.config import get_settings
    if get_settings().realtime_enabled:
        from app.services.realtime_sessions import revoke
        revoke(db, membership.user_id, legacy_id=legacy.id)
    if membership.user.active_legacy_id == legacy.id:
        membership.user.active_legacy_id = None
    record_access_event(db, legacy.id, "collaborator_revoked", actor_user_id=user.id, target_user_id=membership.user_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/legacies/{legacy_id}/members/{membership_id}/restore", response_model=CollaborationPanelResponse)
def restore_collaborator(legacy_id: int, membership_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True)
    membership = db.scalar(select(LegacyCollaborator).where(LegacyCollaborator.id == membership_id, LegacyCollaborator.legacy_id == legacy.id))
    if membership is None:
        raise HTTPException(status_code=404, detail="Collaborator not found.")
    membership.status = CollaboratorStatus.ACTIVE.value
    record_access_event(db, legacy.id, "collaborator_restored", actor_user_id=user.id, target_user_id=membership.user_id)
    db.commit()
    return collaboration_panel(legacy_id, user, db)
