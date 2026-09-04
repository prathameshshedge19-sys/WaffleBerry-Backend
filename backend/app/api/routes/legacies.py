from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.legacy import Legacy
from app.models.collaboration import CollaboratorStatus, LegacyCollaborator
from app.models.user import User
from app.schemas.legacy import LegacyContextResponse, LegacyResponse, LegacySetupStartResponse
from app.services.authorization import legacy_role, require_legacy
from app.services.legacy_setup import create_collecting_legacy, missing_setup_fields


router = APIRouter(prefix="/legacies", tags=["Legacies"])


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
        select(Legacy).where(Legacy.owner_user_id == user.id).order_by(Legacy.updated_at.desc(), Legacy.id.desc())
    ).all())
    collaborations = list(db.scalars(
        select(Legacy).join(LegacyCollaborator).where(
            LegacyCollaborator.user_id == user.id,
            LegacyCollaborator.status == CollaboratorStatus.ACTIVE.value,
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
