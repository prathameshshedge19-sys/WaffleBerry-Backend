import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.legacy import Legacy
from app.models.collaboration import CollaboratorStatus, LegacyCollaborator
from app.models.user import User
from app.schemas.legacy import LegacyContextResponse, LegacyResponse, LegacySetupStartResponse, LegacyDeleteRequest
from app.services.authorization import legacy_role, require_legacy
from app.services.legacy_setup import create_collecting_legacy, missing_setup_fields, pending_or_new_legacy


router = APIRouter(prefix="/legacies", tags=["Legacies"])
logger = logging.getLogger(__name__)


@router.get("/{legacy_id}/deletion-preview")
def deletion_preview(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.legacy_deletion import preview
    return preview(db, user.id, legacy_id)


@router.delete("/{legacy_id}", status_code=status.HTTP_202_ACCEPTED)
def delete_legacy(legacy_id: int, payload: LegacyDeleteRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.legacy_deletion import request_deletion
    return request_deletion(db, user, legacy_id, payload.confirmation)


def _response(legacy: Legacy, role: str = "owner") -> dict:
    return {
        "id": legacy.id,
        "owner_user_id": legacy.owner_user_id,
        "subject_name": legacy.subject_name,
        "relationship_to_owner": legacy.relationship_to_owner,
        "is_self": legacy.is_self,
        "setup_status": legacy.setup_status,
        "missing_fields": missing_setup_fields(legacy),
        "created_at": legacy.created_at,
        "updated_at": legacy.updated_at,
        "access_role": role,
        "owner_name": legacy.owner.full_name if legacy.owner else None,
    }


@router.get("", response_model=LegacyContextResponse)
def list_legacies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    owned = list(db.scalars(
        select(Legacy).where(Legacy.owner_user_id == user.id, Legacy.deletion_requested_at.is_(None)).order_by(Legacy.updated_at.desc(), Legacy.id.desc())
    ).all())
    collaborations = list(db.scalars(
        select(Legacy).join(LegacyCollaborator).where(
            LegacyCollaborator.user_id == user.id,
            LegacyCollaborator.status == CollaboratorStatus.ACTIVE.value,
            Legacy.deletion_requested_at.is_(None),
        ).order_by(LegacyCollaborator.updated_at.desc(), Legacy.id.desc())
    ).all())
    legacies = [*owned, *collaborations]
    active_id = user.active_legacy_id if any(item.id == user.active_legacy_id for item in legacies) else None
    return {
        "active_legacy_id": active_id,
        "legacies": [_response(item, "owner" if item.owner_user_id == user.id else "collaborator") for item in legacies],
        "owned_legacies": [_response(item, "owner") for item in owned],
        "collaborations": [_response(item, "collaborator") for item in collaborations],
    }


@router.post("/setup", response_model=LegacySetupStartResponse, status_code=status.HTTP_201_CREATED)
def start_legacy_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = create_collecting_legacy(db, user)
    db.commit()
    db.refresh(legacy)
    return {"legacy": _response(legacy)}


@router.post("/setup/bootstrap", response_model=LegacySetupStartResponse)
def bootstrap_legacy_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        locked_user = db.scalar(select(User).where(User.id == user.id).with_for_update()) or user
        legacy = pending_or_new_legacy(db, locked_user)
        db.commit()
        db.refresh(legacy)
        return {"legacy": _response(legacy)}
    except SQLAlchemyError:
        db.rollback()
        # SQL exceptions may contain bound private values. Keep the public error
        # unchanged and emit only the fixed category through the passive sink.
        from app.services import turn_observability as obs
        obs.emit("component_degraded", level=logging.ERROR, category="persistence_failed")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "legacy_setup_bootstrap_failed",
                "message": "Rya couldn't start the Legacy setup. Try again.",
            },
        ) from None


@router.get("/{legacy_id}", response_model=LegacyResponse)
def get_legacy(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id)
    return _response(legacy, legacy_role(db, user.id, legacy) or "owner")

@router.post("/{legacy_id}/select", response_model=LegacyResponse)
def select_legacy(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id)
    user.active_legacy_id = legacy.id
    db.commit()
    db.refresh(legacy)
    return _response(legacy, legacy_role(db, user.id, legacy) or "owner")
