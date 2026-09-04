from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.collaboration import CollaboratorStatus, LegacyCollaborator
from app.models.legacy import Legacy
from app.models.viewer import LegacyViewerAccess, ViewerAccessStatus


def is_owner(user_id: int, legacy: Legacy) -> bool:
    return legacy.owner_user_id == user_id


def collaborator_membership(db: Session, user_id: int, legacy_id: int) -> LegacyCollaborator | None:
    return db.scalar(
        select(LegacyCollaborator).where(
            LegacyCollaborator.legacy_id == legacy_id,
            LegacyCollaborator.user_id == user_id,
        )
    )


def is_collaborator(db: Session, user_id: int, legacy: Legacy) -> bool:
    membership = collaborator_membership(db, user_id, legacy.id)
    return bool(membership and membership.status == CollaboratorStatus.ACTIVE.value)


def is_active_collaborator(db: Session, user_id: int, legacy: Legacy) -> bool:
    return is_collaborator(db, user_id, legacy)


def legacy_role(db: Session, user_id: int, legacy: Legacy) -> str | None:
    if is_owner(user_id, legacy):
        return "owner"
    if is_collaborator(db, user_id, legacy):
        return "collaborator"
    return None


def can_view_legacy(db: Session, user_id: int, legacy: Legacy) -> bool:
    return legacy_role(db, user_id, legacy) is not None


def can_build_legacy(db: Session, user_id: int, legacy: Legacy) -> bool:
    return legacy_role(db, user_id, legacy) in {"owner", "collaborator"}


def viewer_access(db: Session, user_id: int, legacy_id: int) -> LegacyViewerAccess | None:
    return db.scalar(select(LegacyViewerAccess).where(LegacyViewerAccess.legacy_id == legacy_id, LegacyViewerAccess.user_id == user_id))


def can_view_legacy_as_persona(db: Session, user_id: int, legacy: Legacy) -> bool:
    access = viewer_access(db, user_id, legacy.id)
    return bool(access and access.status == ViewerAccessStatus.ACTIVE.value)


def is_active_viewer(db: Session, user_id: int, legacy: Legacy) -> bool:
    return can_view_legacy_as_persona(db, user_id, legacy)


def can_talk_to_legacy(db: Session, user_id: int, legacy: Legacy) -> bool:
    return is_active_viewer(db, user_id, legacy)


def can_manage_access(user_id: int, legacy: Legacy) -> bool:
    return is_owner(user_id, legacy)


def persona_legacy(db: Session, user_id: int, legacy_id: int | None) -> Legacy | None:
    if legacy_id is None:
        return None
    legacy = db.get(Legacy, legacy_id)
    return legacy if legacy and can_view_legacy_as_persona(db, user_id, legacy) else None


def require_persona_legacy(db: Session, user_id: int, legacy_id: int) -> Legacy:
    legacy = persona_legacy(db, user_id, legacy_id)
    if legacy is None:
        raise HTTPException(status_code=404, detail={"code": "legacy_access_changed", "message": "Your access to this Legacy has changed."})
    return legacy


def accessible_legacy(db: Session, user_id: int, legacy_id: int | None, *, owner_only: bool = False) -> Legacy | None:
    if legacy_id is None:
        return None
    legacy = db.get(Legacy, legacy_id)
    if legacy is None:
        return None
    if owner_only:
        return legacy if is_owner(user_id, legacy) else None
    return legacy if can_build_legacy(db, user_id, legacy) else None


def require_legacy(db: Session, user_id: int, legacy_id: int, *, owner_only: bool = False) -> Legacy:
    legacy = accessible_legacy(db, user_id, legacy_id, owner_only=owner_only)
    if legacy is None:
        raise HTTPException(status_code=404, detail="Legacy not found.")
    return legacy
