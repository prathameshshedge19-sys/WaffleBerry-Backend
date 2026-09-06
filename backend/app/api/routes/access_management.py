from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.access import InviteStatus, LegacyAccessEvent, LegacyAccessInvite
from app.models.collaboration import CollaboratorStatus, LegacyCollaborator
from app.models.user import User
from app.models.viewer import LegacyViewerAccess, ViewerAccessStatus
from app.schemas.access import AccessInviteAccepted, AccessInviteCreate, AccessInvitePreview, AccessPanelResponse
from app.services.access_management import (accept_invite, create_invite, ensure_pending, get_invite,
    invite_attempt_limiter, record_access_event)
from app.services.authorization import require_legacy
from app.services.collaboration import decrypt_code, rotate_code
from app.services.email import EmailDeliveryError, email_sender
from app.services.legacy_access import decrypt_viewer_code, rotate_viewer_code

router = APIRouter(prefix="/access", tags=["Legacy access management"])


def _panel(db: Session, legacy):
    collaborators = db.scalars(select(LegacyCollaborator).options(joinedload(LegacyCollaborator.user)).where(
        LegacyCollaborator.legacy_id == legacy.id).order_by(LegacyCollaborator.status, LegacyCollaborator.joined_at.desc())).all()
    viewers = db.scalars(select(LegacyViewerAccess).options(joinedload(LegacyViewerAccess.user)).where(
        LegacyViewerAccess.legacy_id == legacy.id).order_by(LegacyViewerAccess.status, LegacyViewerAccess.joined_at.desc())).all()
    invites = db.scalars(select(LegacyAccessInvite).where(LegacyAccessInvite.legacy_id == legacy.id,
        LegacyAccessInvite.status == InviteStatus.PENDING.value).order_by(LegacyAccessInvite.created_at.desc())).all()
    events = db.scalars(select(LegacyAccessEvent).options(joinedload(LegacyAccessEvent.actor), joinedload(LegacyAccessEvent.target)).where(
        LegacyAccessEvent.legacy_id == legacy.id).order_by(LegacyAccessEvent.created_at.desc()).limit(50)).all()
    member = lambda item, key: {key: item.id, "user_id": item.user_id, "full_name": item.user.full_name,
        "email": item.user.email, "status": item.status, "joined_at": item.joined_at}
    return {"legacy_id": legacy.id, "subject_name": legacy.subject_name or "this Legacy",
        "owner": {"user_id": legacy.owner.id, "full_name": legacy.owner.full_name, "email": legacy.owner.email},
        "counts": {"collaborators": sum(x.status == "active" for x in collaborators),
                   "viewers": sum(x.status == "active" for x in viewers)},
        "collaborators": [member(x, "membership_id") for x in collaborators],
        "viewers": [member(x, "access_id") for x in viewers],
        "codes": {"collaborator": {"code": decrypt_code(legacy.collaborator_code_ciphertext) if legacy.collaborator_code_enabled else None,
            "hint": legacy.collaborator_code_hint, "enabled": legacy.collaborator_code_enabled},
            "viewer": {"code": decrypt_viewer_code(legacy.viewer_code_ciphertext) if legacy.viewer_code_enabled else None,
            "hint": legacy.viewer_code_hint, "enabled": legacy.viewer_code_enabled}},
        "pending_invites": [{"id": x.id, "email": x.email, "role": x.role, "status": x.status,
            "created_at": x.created_at, "expires_at": x.expires_at} for x in invites],
        "events": [{"id": x.id, "event_type": x.event_type,
            "actor_name": x.actor.full_name if x.actor else None, "target_name": x.target.full_name if x.target else None,
            "details": x.details, "created_at": x.created_at} for x in events]}


@router.get("/legacies/{legacy_id}", response_model=AccessPanelResponse)
def access_panel(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _panel(db, require_legacy(db, user.id, legacy_id, owner_only=True))


@router.post("/legacies/{legacy_id}/invites", status_code=201)
def invite_person(legacy_id: int, payload: AccessInviteCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True)
    invite, token = create_invite(db, legacy, user, str(payload.email), payload.role)
    url = f"{get_settings().frontend_base_url.rstrip('/')}/invite.html?token={token}"
    try:
        email_sender.send_invitation(recipient=invite.email, inviter_name=user.full_name,
            subject_name=legacy.subject_name or "this Legacy", role=invite.role, invite_url=url)
    except EmailDeliveryError:
        invite.status = InviteStatus.REVOKED.value; invite.revoked_at = datetime.now(timezone.utc); db.commit()
        raise HTTPException(503, "The invitation could not be delivered.")
    return {"message": "Invitation sent.", "invite_id": invite.id, "expires_at": invite.expires_at}


@router.delete("/legacies/{legacy_id}/invites/{invite_id}", status_code=204)
def revoke_invite(legacy_id: int, invite_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True)
    invite = db.scalar(select(LegacyAccessInvite).where(LegacyAccessInvite.id == invite_id, LegacyAccessInvite.legacy_id == legacy.id))
    if not invite or invite.status != InviteStatus.PENDING.value: raise HTTPException(404, "Pending invitation not found.")
    invite.status = InviteStatus.REVOKED.value; invite.revoked_at = datetime.now(timezone.utc)
    record_access_event(db, legacy.id, "invitation_revoked", actor_user_id=user.id,
                        details={"email": invite.email, "role": invite.role}); db.commit()
    return Response(status_code=204)


@router.get("/invites/{token}", response_model=AccessInvitePreview)
def preview_invite(token: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not invite_attempt_limiter.allow(user.id): raise HTTPException(429, "Too many attempts. Please wait and try again.")
    invite = get_invite(db, token); ensure_pending(invite)
    if user.email.lower() != invite.email.lower():
        raise HTTPException(403, "Sign in with the email address that received this invitation.")
    legacy = invite.legacy
    return {"legacy_id": legacy.id, "subject_name": legacy.subject_name or "this Legacy", "owner_name": legacy.owner.full_name,
        "email": invite.email, "role": invite.role, "status": invite.status, "expires_at": invite.expires_at}


@router.post("/invites/{token}/accept", response_model=AccessInviteAccepted)
def accept_access_invite(token: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not invite_attempt_limiter.allow(user.id): raise HTTPException(429, "Too many attempts. Please wait and try again.")
    legacy, role = accept_invite(db, get_invite(db, token), user); invite_attempt_limiter.clear(user.id)
    return {"legacy_id": legacy.id, "subject_name": legacy.subject_name or "this Legacy", "role": role, "status": "active"}


def _code_action(legacy, kind: str, action: str, db: Session, user: User):
    if kind not in {"collaborator", "viewer"}: raise HTTPException(404, "Access code type not found.")
    if action == "regenerate":
        rotate_code(db, legacy) if kind == "collaborator" else rotate_viewer_code(db, legacy)
    elif action == "enable":
        field = f"{kind if kind == 'collaborator' else 'viewer'}_code_ciphertext"
        if not getattr(legacy, field): rotate_code(db, legacy) if kind == "collaborator" else rotate_viewer_code(db, legacy)
        else: setattr(legacy, f"{kind}_code_enabled", True)
    else: setattr(legacy, f"{kind}_code_enabled", False)
    record_access_event(db, legacy.id, f"{kind}_code_{action}d" if action != "disable" else f"{kind}_code_disabled",
                        actor_user_id=user.id); db.commit()


@router.post("/legacies/{legacy_id}/codes/{kind}/{action}", response_model=AccessPanelResponse)
def change_code(legacy_id: int, kind: str, action: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if action not in {"regenerate", "enable"}: raise HTTPException(404, "Code action not found.")
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True); _code_action(legacy, kind, action, db, user)
    return _panel(db, legacy)


@router.delete("/legacies/{legacy_id}/codes/{kind}", status_code=204)
def disable_code(legacy_id: int, kind: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True); _code_action(legacy, kind, "disable", db, user)
    return Response(status_code=204)


@router.post("/legacies/{legacy_id}/members/{role}/{record_id}/{action}", response_model=AccessPanelResponse)
def change_member(legacy_id: int, role: str, record_id: int, action: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id, owner_only=True)
    if role == "collaborator": model, active, revoked = LegacyCollaborator, CollaboratorStatus.ACTIVE.value, CollaboratorStatus.REVOKED.value
    elif role == "viewer": model, active, revoked = LegacyViewerAccess, ViewerAccessStatus.ACTIVE.value, ViewerAccessStatus.REVOKED.value
    else: raise HTTPException(404, "Access role not found.")
    if action not in {"revoke", "restore"}: raise HTTPException(404, "Access action not found.")
    member = db.scalar(select(model).where(model.id == record_id, model.legacy_id == legacy.id))
    if not member: raise HTTPException(404, "Access record not found.")
    member.status = revoked if action == "revoke" else active
    if action == "revoke" and get_settings().realtime_enabled:
        from app.services.realtime_sessions import revoke
        revoke(db, member.user_id, legacy_id=legacy.id)
    if role == "collaborator" and action == "revoke" and member.user.active_legacy_id == legacy.id: member.user.active_legacy_id = None
    record_access_event(db, legacy.id, f"{role}_{action}d", actor_user_id=user.id, target_user_id=member.user_id)
    db.commit(); return _panel(db, legacy)
