"""Database-fenced live connections. Never creates a ConversationTurn or message.

Lock order is actor, then session. Each public operation owns a short transaction;
no lock or database transaction is held while awaiting provider/browser I/O.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, or_, select, update

from app.models.conversation import Conversation
from app.models.legacy import Legacy
from app.models.realtime_session import RealtimeSession as Live
from app.models.user import User
from app.services.authorization import can_talk_to_legacy, legacy_role
from app.services import turn_observability as obs

ACTIVE = ("authorized", "connecting", "connected", "reconnecting")
TERMINAL = ("ended", "revoked", "failed")
REASONS = {"client_end", "browser_disconnect", "provider_disconnect", "access_changed", "logout",
           "session_expired", "ticket_expired", "lease_expired", "reconnect_expired", "protocol_error",
           "queue_overrun", "rate_limit", "provider_failed", "idle_timeout", "backend_error"}


class RealtimeError(Exception):
    def __init__(self, code, status=403):
        self.code, self.status = code, status
        super().__init__(code)


def now():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def validate_origin(origin, settings):
    if not origin or len(origin) > 255 or origin not in settings.allowed_cors_origins:
        raise RealtimeError("realtime_not_authorized")
    return origin


def lock_actor(db, actor_id):
    # Also serializes SQLite writes; not a change to the actor's auth policy.
    if db.execute(update(User).where(User.id == actor_id).values(id=User.id)).rowcount != 1:
        raise RealtimeError("realtime_not_authorized")


def _get(db, session_id):
    row = db.scalar(select(Live).where(Live.id == session_id).execution_options(populate_existing=True))
    if row is None:
        raise RealtimeError("realtime_not_authorized")
    return row


def resolve_scope(db, actor_id, *, conversation_id=None, legacy_id=None, mode=None):
    db.expire_all()  # Includes membership rows retained by an earlier caller.
    user = db.get(User, actor_id, populate_existing=True)
    if not user or not user.is_verified:
        raise RealtimeError("realtime_not_authorized")
    if conversation_id is not None:
        conversation = db.get(Conversation, conversation_id, populate_existing=True)
        if not conversation or conversation.user_id != actor_id:
            raise RealtimeError("realtime_not_authorized")
        if legacy_id is not None and (legacy_id != conversation.legacy_id or mode != conversation.mode):
            raise RealtimeError("realtime_access_changed")
        legacy_id, mode = conversation.legacy_id, conversation.mode
    legacy = db.get(Legacy, legacy_id, populate_existing=True)
    if not legacy:
        raise RealtimeError("realtime_not_authorized")
    role = legacy_role(db, actor_id, legacy) if mode == "rya" else "viewer" if mode == "legacy" and can_talk_to_legacy(db, actor_id, legacy) else None
    if role is None:
        raise RealtimeError("realtime_not_authorized")
    allowed = {"active", "collecting_identity"} if mode == "legacy" else {"active"}
    if legacy.setup_status not in allowed:
        raise RealtimeError("realtime_setup_incomplete", 409)
    return legacy.id, mode, role, user.voice_preference


def _finish(row, state, reason, timestamp):
    row.state, row.end_reason, row.ended_at = state, reason, timestamp
    row.active_actor_id = row.lease_owner = row.lease_expires_at = row.reconnect_until = None
    row.ticket_hash = row.ticket_expires_at = None
    row.connection_generation += 1
    if state == "revoked":
        row.revoked_at = timestamp


def _recover(row, timestamp, settings):
    if row.state in TERMINAL:
        return
    reason = None
    if timestamp >= min(utc(row.expires_at), utc(row.auth_expires_at)):
        reason = "session_expired"
    elif row.state == "authorized" and row.ticket_expires_at and timestamp >= utc(row.ticket_expires_at):
        reason = "ticket_expired"
    elif row.state == "reconnecting" and timestamp >= utc(row.reconnect_until):
        reason = "reconnect_expired"
    elif row.state in {"connecting", "connected"} and timestamp >= utc(row.lease_expires_at):
        deadline = utc(row.lease_expires_at) + timedelta(seconds=settings.realtime_reconnect_seconds)
        if timestamp >= deadline:
            reason = "lease_expired"
        else:
            row.state, row.reconnect_until = "reconnecting", deadline
            row.lease_owner = row.lease_expires_at = row.ticket_hash = None
            row.connection_generation += 1
    if reason:
        _finish(row, "ended", reason, timestamp)


def reauthorize(db, row, settings, *, timestamp=None):
    timestamp = timestamp or now()
    if row.state in TERMINAL:
        raise RealtimeError("realtime_access_changed" if row.state == "revoked" else "realtime_session_expired", 409)
    if timestamp >= min(utc(row.expires_at), utc(row.auth_expires_at)):
        raise RealtimeError("realtime_session_expired", 409)
    scope = resolve_scope(db, row.actor_user_id, conversation_id=row.conversation_id,
                          legacy_id=row.legacy_id, mode=row.mode)
    if scope[:3] != (row.legacy_id, row.mode, row.role):
        raise RealtimeError("realtime_access_changed")
    return scope[3]


def _ticket(row, settings, timestamp):
    raw = secrets.token_urlsafe(32)
    row.ticket_hash = hashlib.sha256(raw.encode()).hexdigest()
    row.ticket_expires_at = min(timestamp + timedelta(seconds=settings.realtime_ticket_seconds), utc(row.expires_at), utc(row.auth_expires_at))
    row.ticket_used_at = None
    return raw


def _reply(row, ticket):
    return {"session_id": row.id, "ticket": ticket, "ticket_expires_at": row.ticket_expires_at,
            "expires_at": row.expires_at, "generation": row.connection_generation,
            "conversation_id": row.conversation_id, "state": row.state}


def authorize(db, actor_id, payload, origin, claims, settings):
    timestamp = now()
    lock_actor(db, actor_id)
    cutoff = db.scalar(select(func.max(Live.revoked_at)).where(Live.actor_user_id == actor_id, Live.end_reason == "logout"))
    issued = datetime.fromtimestamp(claims["iat"], timezone.utc)
    if cutoff and issued <= utc(cutoff):
        raise RealtimeError("realtime_access_changed")
    for old in db.scalars(select(Live).where(Live.active_actor_id == actor_id)).all():
        _recover(old, timestamp, settings)
        if old.state not in {"connecting", "connected"}:
            from app.services.realtime_transcripts import release_unanswered
            release_unanswered(db, old.id)
    db.flush()
    if db.scalar(select(Live.id).where(Live.active_actor_id == actor_id)):
        raise RealtimeError("realtime_conflict", 409)
    recent = db.scalar(select(func.count()).select_from(Live).where(Live.actor_user_id == actor_id,
                       Live.created_at >= timestamp - timedelta(minutes=1)))
    if recent >= settings.realtime_creations_per_minute:
        raise RealtimeError("realtime_rate_limit", 429)
    legacy_id, mode, role, _voice = resolve_scope(db, actor_id, **payload.model_dump())
    row = Live(id=str(uuid4()), actor_user_id=actor_id, legacy_id=legacy_id,
               conversation_id=payload.conversation_id, active_actor_id=actor_id, mode=mode, role=role,
               state="authorized", origin=origin, created_at=timestamp,
               expires_at=min(timestamp + timedelta(seconds=settings.realtime_session_seconds), datetime.fromtimestamp(claims["exp"], timezone.utc)),
               auth_expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc),
               auth_issued_at=issued, connection_generation=0)
    ticket = _ticket(row, settings, timestamp)
    db.add(row)
    db.commit()
    return _reply(row, ticket)


def reconnect(db, session_id, actor_id, origin, settings):
    lock_actor(db, actor_id)
    row = _get(db, session_id)
    if row.actor_user_id != actor_id or row.origin != origin:
        raise RealtimeError("realtime_not_authorized")
    timestamp = now()
    _recover(row, timestamp, settings)
    db.flush()
    if row.state != "reconnecting":
        db.commit()
        raise RealtimeError("realtime_conflict", 409)
    from app.services.realtime_transcripts import release_unanswered
    release_unanswered(db, row.id)
    reauthorize(db, row, settings, timestamp=timestamp)
    # Every replacement ticket fences all previous tickets/connections.
    row.connection_generation += 1
    ticket = _ticket(row, settings, timestamp)
    db.commit()
    return _reply(row, ticket)


def consume(db, ticket, origin, owner, settings):
    digest = hashlib.sha256(ticket.encode()).hexdigest()
    candidate = db.scalar(select(Live).where(Live.ticket_hash == digest))
    if candidate is None:
        raise RealtimeError("realtime_ticket_invalid")
    lock_actor(db, candidate.actor_user_id)
    row = _get(db, candidate.id)
    timestamp = now()
    if row.ticket_hash != digest or row.origin != origin:
        raise RealtimeError("realtime_ticket_invalid")
    if row.ticket_used_at:
        raise RealtimeError("realtime_ticket_used", 409)
    if not row.ticket_expires_at or timestamp >= utc(row.ticket_expires_at):
        raise RealtimeError("realtime_ticket_expired", 409)
    if row.state not in {"authorized", "reconnecting"}:
        raise RealtimeError("realtime_conflict", 409)
    if row.state == "reconnecting" and timestamp >= utc(row.reconnect_until):
        raise RealtimeError("realtime_session_expired", 409)
    voice = reauthorize(db, row, settings, timestamp=timestamp)
    row.ticket_used_at = timestamp
    row.connection_generation += 1
    row.state, row.lease_owner = "connecting", owner
    row.lease_expires_at = timestamp + timedelta(seconds=settings.realtime_lease_seconds)
    row.reconnect_until = None
    db.commit()
    return row.id, row.connection_generation, voice


def owned(db, session_id, owner, generation, settings, *, ready=False):
    row = _get(db, session_id)
    lock_actor(db, row.actor_user_id)
    row = _get(db, session_id)
    timestamp = now()
    if (row.lease_owner != owner or row.connection_generation != generation
            or row.state not in {"connecting", "connected"}
            or timestamp >= utc(row.lease_expires_at)):
        raise RealtimeError("realtime_access_changed")
    reauthorize(db, row, settings, timestamp=timestamp)
    row.lease_expires_at = timestamp + timedelta(seconds=settings.realtime_lease_seconds)
    if ready:
        row.state = "connected"
        row.connected_at = row.connected_at or timestamp
    db.commit()
    return row


def close_owned(db, session_id, owner, generation, reason, settings):
    row = _get(db, session_id)
    lock_actor(db, row.actor_user_id)
    row = _get(db, session_id)
    if row.lease_owner != owner or row.connection_generation != generation:
        db.rollback()
        return  # stale worker may release only its own provider resources
    from app.services.realtime_transcripts import release_unanswered
    release_unanswered(db, session_id)
    timestamp = now()
    try:
        reauthorize(db, row, settings, timestamp=timestamp)
    except RealtimeError:
        reason = "session_expired" if timestamp >= min(utc(row.expires_at), utc(row.auth_expires_at)) else "access_changed"
    if reason not in REASONS:
        reason = "backend_error"
    if reason in {"browser_disconnect", "provider_disconnect"}:
        row.state = "reconnecting"
        row.reconnect_until = min(timestamp + timedelta(seconds=settings.realtime_reconnect_seconds), utc(row.expires_at), utc(row.auth_expires_at))
        row.lease_owner = row.lease_expires_at = row.ticket_hash = None
        row.connection_generation += 1
    else:
        state = "revoked" if reason in {"access_changed", "logout"} else "failed" if reason in {"provider_failed", "backend_error", "protocol_error", "queue_overrun", "rate_limit"} else "ended"
        _finish(row, state, reason, timestamp)
    db.commit()


def revoke(db, actor_id, *, legacy_id=None, reason="access_changed"):
    """Called inside the existing revocation/logout transaction, before commit."""
    lock_actor(db, actor_id)
    db.flush()  # membership revocation and live invalidation commit together
    query = select(Live).where(Live.actor_user_id == actor_id)
    if legacy_id is not None:
        query = query.where(Live.legacy_id == legacy_id)
    timestamp = now()
    for row in db.scalars(query).all():
        if row.state in ACTIVE:
            _finish(row, "revoked", reason, timestamp)
            from app.services.realtime_transcripts import release_unanswered
            release_unanswered(db, row.id)
        elif reason == "logout":
            # Retained metadata acts as a live-only old-token cutoff.
            row.revoked_at, row.end_reason = timestamp, "logout"
    obs.emit("realtime_access_revocation")


def sweep(db, settings):
    """Bounded recovery pass. No turn claiming, execution or effect replay."""
    timestamp = now()
    ids = db.scalars(select(Live.actor_user_id).where(Live.active_actor_id.is_not(None), or_(
        Live.expires_at <= timestamp, Live.auth_expires_at <= timestamp,
        Live.lease_expires_at <= timestamp, Live.reconnect_until <= timestamp,
        (Live.state == "authorized") & (Live.ticket_expires_at <= timestamp),
    )).order_by(Live.actor_user_id).limit(1000)).all()
    for actor in ids:
        lock_actor(db, actor)
        row = db.scalar(select(Live).where(Live.active_actor_id == actor).execution_options(populate_existing=True))
        if row:
            _recover(row, now(), settings)
            if row.state not in {"connecting", "connected"}:
                from app.services.realtime_transcripts import release_unanswered
                release_unanswered(db, row.id)
        db.commit()


def bind_once(db, session_id, actor_id, factory, settings, *, owner, generation):
    """Phase C transaction contract, not exposed by any Phase B route.

    Caller commits binding + first-turn admission together. Factory must only
    stage/flush a Conversation (no commit, provider calls or messages). The actor
    write lock precedes reading the binding; competing retries never call it.
    """
    lock_actor(db, actor_id)
    row = _get(db, session_id)
    if row.actor_user_id != actor_id:
        raise RealtimeError("realtime_not_authorized")
    if (row.state != "connected" or row.lease_owner != owner or row.connection_generation != generation
            or row.lease_expires_at is None or now() >= utc(row.lease_expires_at)):
        raise RealtimeError("realtime_access_changed")
    reauthorize(db, row, settings)
    if row.conversation_id is None:
        conversation = factory(row)
        db.flush()
        if (conversation.user_id, conversation.legacy_id, conversation.mode) != (actor_id, row.legacy_id, row.mode):
            raise RealtimeError("realtime_not_authorized")
        row.conversation_id = conversation.id
        db.flush()
    return row.conversation_id
