import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.access import InviteStatus, LegacyAccessEvent, LegacyAccessInvite
from app.models.collaboration import CollaboratorStatus, LegacyCollaborator
from app.models.legacy import Legacy
from app.models.user import User
from app.models.viewer import LegacyViewerAccess, ViewerAccessStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def invite_digest(token: str) -> str:
    key = (get_settings().jwt_secret_key + ":l10:invite").encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def record_access_event(db: Session, legacy_id: int, event_type: str, *, actor_user_id: int | None = None,
                        target_user_id: int | None = None, details: dict | None = None) -> None:
    db.add(LegacyAccessEvent(legacy_id=legacy_id, actor_user_id=actor_user_id,
                             target_user_id=target_user_id, event_type=event_type, details=details or {}))


class InviteAttemptLimiter:
    def __init__(self, limit: int = 8, window: int = 60):
        self.limit, self.window = limit, window
        self.attempts, self.lock = defaultdict(deque), Lock()

    def allow(self, key: int) -> bool:
        now = monotonic()
        with self.lock:
            values = self.attempts[key]
            while values and now - values[0] > self.window:
                values.popleft()
            if len(values) >= self.limit:
                return False
            values.append(now)
            return True

    def clear(self, key: int) -> None:
        with self.lock:
            self.attempts.pop(key, None)


invite_attempt_limiter = InviteAttemptLimiter()


def create_invite(db: Session, legacy: Legacy, owner: User, email: str, role: str) -> tuple[LegacyAccessInvite, str]:
    normalized = email.strip().lower()
    if normalized == owner.email.lower():
        raise HTTPException(409, "The owner already has full access.")
    target = db.scalar(select(User).where(User.email == normalized))
    if target:
        model = LegacyCollaborator if role == "collaborator" else LegacyViewerAccess
        existing = db.scalar(select(model).where(model.legacy_id == legacy.id, model.user_id == target.id))
        if existing and existing.status == "active":
            raise HTTPException(409, f"This person already has active {role} access.")
    pending = db.scalar(select(LegacyAccessInvite).where(
        LegacyAccessInvite.legacy_id == legacy.id, LegacyAccessInvite.email == normalized,
        LegacyAccessInvite.role == role, LegacyAccessInvite.status == InviteStatus.PENDING.value))
    if pending:
        raise HTTPException(409, "A pending invitation already exists for this email and role.")
    token = secrets.token_urlsafe(32)
    invite = LegacyAccessInvite(legacy_id=legacy.id, email=normalized, role=role,
        token_digest=invite_digest(token), invited_by_user_id=owner.id,
        expires_at=_now() + timedelta(days=get_settings().access_invite_expire_days))
    db.add(invite)
    record_access_event(db, legacy.id, "invitation_created", actor_user_id=owner.id,
                        target_user_id=target.id if target else None, details={"email": normalized, "role": role})
    db.commit(); db.refresh(invite)
    return invite, token


def get_invite(db: Session, token: str) -> LegacyAccessInvite:
    invite = db.scalar(select(LegacyAccessInvite).where(LegacyAccessInvite.token_digest == invite_digest(token)))
    if not invite:
        raise HTTPException(404, "Invitation not found.")
    return invite


def ensure_pending(invite: LegacyAccessInvite) -> None:
    expiry = invite.expires_at if invite.expires_at.tzinfo else invite.expires_at.replace(tzinfo=timezone.utc)
    if invite.status != InviteStatus.PENDING.value:
        raise HTTPException(409, "This invitation is no longer available.")
    if expiry <= _now():
        raise HTTPException(410, "This invitation has expired.")


def accept_invite(db: Session, invite: LegacyAccessInvite, user: User) -> tuple[Legacy, str]:
    ensure_pending(invite)
    if user.email.lower() != invite.email.lower():
        raise HTTPException(403, "Sign in with the email address that received this invitation.")
    legacy = db.get(Legacy, invite.legacy_id)
    if invite.role == "collaborator":
        member = db.scalar(select(LegacyCollaborator).where(LegacyCollaborator.legacy_id == legacy.id,
                                                             LegacyCollaborator.user_id == user.id))
        if member:
            member.status = CollaboratorStatus.ACTIVE.value; member.added_by_user_id = invite.invited_by_user_id
        else:
            db.add(LegacyCollaborator(legacy_id=legacy.id, user_id=user.id, added_by_user_id=invite.invited_by_user_id))
        user.active_legacy_id = legacy.id
    else:
        access = db.scalar(select(LegacyViewerAccess).where(LegacyViewerAccess.legacy_id == legacy.id,
                                                            LegacyViewerAccess.user_id == user.id))
        if access:
            access.status = ViewerAccessStatus.ACTIVE.value; access.granted_via_code = False
        else:
            db.add(LegacyViewerAccess(legacy_id=legacy.id, user_id=user.id, granted_via_code=False))
    invite.status = InviteStatus.ACCEPTED.value; invite.accepted_by_user_id = user.id; invite.accepted_at = _now()
    record_access_event(db, legacy.id, "invitation_accepted", actor_user_id=user.id,
                        target_user_id=user.id, details={"role": invite.role})
    db.commit()
    return legacy, invite.role
