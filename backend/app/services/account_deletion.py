"""Account deletion parent. Short DB transactions; exact-object purge elsewhere."""
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, or_, select, update

from app.database import Base
from app.models.account_deletion import AccountDeletion, AccountDeletionReauth
from app.models.access import LegacyAccessEvent, LegacyAccessInvite
from app.models.auth_challenge import AuthChallenge
from app.models.collaboration import LegacyCollaborator
from app.models.conversation import Conversation, Message
from app.models.legacy import Legacy
from app.models.media_source import MediaSource, MediaArtifact, MediaProcessingJob
from app.models.media_intelligence import MemorySourceLink, SourceCandidateEvidence, SourceMemoryCandidate, SourceEvidence
from app.models.memory import MemoryRevision
from app.models.story import StorySupportLink
from app.models.timeline import LifeEventEvidence
from app.models.turn import ConversationTurn
from app.models.user import User
from app.models.viewer import LegacyViewerAccess
from app.models.visitor import LegacyVisitorProfile
from app.models.voice_profile import VoiceJob, VoiceAsset
from app.services.legacy_deletion import request_deletion, confirmation_text
from app.services.media_sources import MediaSourceService
from app.services.security import hash_password, verify_password
from app.services.voice_profiles import request_account_voice_purge


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def same(value, expected):
    return bool(value and expected and hmac.compare_digest(digest(value), expected))


def locked_user(db, user_id):
    user = db.scalar(select(User).where(User.id == user_id)
                     .execution_options(populate_existing=True).with_for_update())
    if user is None:
        raise HTTPException(401, detail="This session is no longer available.")
    return user


def reauthenticate(db, user_id, access_token, *, password=None, credential=None):
    """Rate-limit first, verify without SQL locks, then bind a five-minute proof."""
    user = locked_user(db, user_id)
    if user.deletion_requested_at is not None:
        raise HTTPException(401, detail="This account is no longer available.")
    timestamp = now()
    row = db.get(AccountDeletionReauth, user_id)
    if row is None:
        row = AccountDeletionReauth(user_id=user_id, window_started_at=timestamp, attempts=0)
        db.add(row)
    if aware(row.window_started_at) + timedelta(minutes=1) <= timestamp:
        row.window_started_at, row.attempts = timestamp, 0
    if row.attempts >= 5:
        raise HTTPException(429, detail="Please wait before verifying your identity again.")
    row.attempts += 1
    row.token_hash = row.session_hash = row.credential_hash = row.expires_at = None
    fingerprint, google_sub, email = user.password_hash, user.google_sub, user.email
    db.commit()
    valid = False
    if password is not None and credential is None:
        valid = verify_password(password, fingerprint)
    elif credential is not None and password is None and google_sub:
        from app.services.google_identity import verify_google_credential, GoogleIdentityError, GoogleIdentityConfigurationError
        try:
            identity = verify_google_credential(credential, fresh_seconds=300)
            valid = identity.sub == google_sub and identity.email == email
        except GoogleIdentityConfigurationError:
            raise HTTPException(503, detail="Google verification is unavailable. Please try again later.") from None
        except GoogleIdentityError:
            valid = False
    if not valid:
        raise HTTPException(401, detail="Fresh identity verification failed.")
    user = locked_user(db, user_id)
    if user.deletion_requested_at is not None or user.password_hash != fingerprint or user.google_sub != google_sub:
        raise HTTPException(401, detail="Please verify your identity again.")
    row = db.get(AccountDeletionReauth, user_id, populate_existing=True)
    proof = secrets.token_urlsafe(32)
    row.token_hash, row.session_hash, row.credential_hash = digest(proof), digest(access_token), digest(fingerprint)
    row.expires_at = now() + timedelta(minutes=5)
    db.commit()
    return proof


def _remove_access(db, user):
    for model, column in ((LegacyCollaborator, LegacyCollaborator.user_id),
                          (LegacyViewerAccess, LegacyViewerAccess.user_id),
                          (LegacyVisitorProfile, LegacyVisitorProfile.viewer_user_id)):
        db.execute(delete(model).where(column == user.id))
    db.execute(delete(LegacyAccessInvite).where(or_(LegacyAccessInvite.email == user.email,
        LegacyAccessInvite.invited_by_user_id == user.id, LegacyAccessInvite.accepted_by_user_id == user.id)))
    db.execute(delete(LegacyAccessEvent).where(or_(LegacyAccessEvent.actor_user_id == user.id,
        LegacyAccessEvent.target_user_id == user.id, LegacyAccessEvent.details["email"].as_string() == user.email)))
    db.execute(delete(AuthChallenge).where(AuthChallenge.email == user.email))


def request_account_deletion(db, user_id, *, proof=None, access_token=None, verified_support=False):
    """One atomic, repeatable parent admission; caller commits. No storage I/O.

    verified_support is an internal CLI contract, never a client payload field.
    """
    db.info["account_deletion_cleanup"] = True
    user = locked_user(db, user_id)
    existing = db.scalar(select(AccountDeletion).where(AccountDeletion.user_id == user_id))
    if existing is not None:
        if verified_support or (same(proof, existing.request_proof_hash) and same(access_token, existing.session_hash)):
            return existing
        raise HTTPException(401, detail="This account is no longer available.")
    if not verified_support:
        auth = db.get(AccountDeletionReauth, user_id, populate_existing=True)
        if (not auth or not auth.expires_at or aware(auth.expires_at) <= now()
                or not same(proof, auth.token_hash) or not same(access_token, auth.session_hash)
                or not same(user.password_hash, auth.credential_hash)):
            raise HTTPException(403, detail="Verify your identity again before deleting your account.")
    from app.services.deletion_journal import record_intent
    from app.services.backup_retention import PolicyError
    try:
        request_id = record_intent(db, user_id)
    except (PolicyError, OSError, sqlite3.Error, ValueError):
        raise HTTPException(503, detail="Deletion recovery storage is unavailable. Please contact support.") from None
    from app.services.realtime_sessions import revoke
    revoke(db, user_id, reason="access_changed")  # independent of feature flags
    # Retire in-flight turns before taking Legacy locks, matching normal writers.
    conversations = list(db.scalars(select(Conversation).where(Conversation.user_id == user_id)
        .order_by(Conversation.id).with_for_update()))
    turns = list(db.scalars(select(ConversationTurn).where(ConversationTurn.actor_user_id == user_id)
        .order_by(ConversationTurn.id).with_for_update()))
    timestamp = now()
    for turn in turns:
        if turn.state in {"pending", "streaming"}:
            turn.state, turn.finished_at, turn.claim_token = "failed", timestamp, None
            turn.safe_error_code = "account_deleted"
    ids = set(db.scalars(select(Legacy.id).where(Legacy.owner_user_id == user_id)))
    ids.update(db.scalars(select(MediaSource.legacy_id).where(MediaSource.uploader_user_id == user_id)))
    ids.update(db.scalars(select(VoiceJob.legacy_id).where(VoiceJob.requested_by_user_id == user_id)))
    ids.update(c.legacy_id for c in conversations if c.legacy_id is not None)
    legacies = list(db.scalars(select(Legacy).where(Legacy.id.in_(ids)).order_by(Legacy.id).with_for_update()))
    request_account_voice_purge(db, user_id)
    for legacy in legacies:
        if legacy.owner_user_id == user_id:
            request_deletion(db, user, legacy.id, confirmation_text(legacy), commit=False)
    source_service = MediaSourceService()
    for source in db.scalars(select(MediaSource).where(MediaSource.uploader_user_id == user_id)
                             .order_by(MediaSource.id).with_for_update()).all():
        source_service.request_account_purge(db, user_id, source)
    _remove_access(db, user)
    user.deletion_requested_at, user.active_legacy_id = timestamp, None
    user.password_hash = hash_password(secrets.token_urlsafe(48))
    user.is_verified = False
    db.execute(delete(AccountDeletionReauth).where(AccountDeletionReauth.user_id == user_id))
    row = AccountDeletion(id=request_id, user_id=user_id, target_user_id=user_id,
        state="queued", request_proof_hash=digest(proof) if proof else None,
        session_hash=digest(access_token) if access_token else None,
        requested_via="support" if verified_support else "in_app", requested_at=timestamp, attempts=0)
    db.add(row)
    db.flush()
    return row


def _source_registry(db, user_id):
    sources = list(db.scalars(select(MediaSource).where(MediaSource.uploader_user_id == user_id)))
    if any(s.state != "deleted" or s.purged_at is None for s in sources):
        return None
    artifacts = list(db.scalars(select(MediaArtifact).where(MediaArtifact.source_id.in_([s.id for s in sources]))))
    if any(a.state != "purged" for a in artifacts):
        return None
    return {(a.id, a.legacy_id, a.source_id, a.object_key, a.object_version,
             a.storage_backend, a.encryption_key_id, a.sha256) for a in artifacts}


def _erase_shared_sources(db, user_id):
    ids = list(db.scalars(select(MediaSource.id).where(MediaSource.uploader_user_id == user_id)))
    evidence = select(SourceEvidence.id).where(SourceEvidence.source_id.in_(ids))
    db.execute(delete(StorySupportLink).where(StorySupportLink.evidence_id.in_(evidence)))
    db.execute(delete(LifeEventEvidence).where(LifeEventEvidence.evidence_id.in_(evidence)))
    for model in (MemorySourceLink, SourceCandidateEvidence, SourceMemoryCandidate,
                  SourceEvidence, MediaProcessingJob, MediaArtifact):
        db.execute(delete(model).where(model.source_id.in_(ids)))
    db.execute(delete(MediaSource).where(MediaSource.id.in_(ids)))


def _erase_shared_speech(db, user_id):
    jobs = list(db.scalars(select(VoiceJob).where(VoiceJob.requested_by_user_id == user_id)))
    assets = list(db.scalars(select(VoiceAsset).where(VoiceAsset.job_id.in_([j.id for j in jobs]))))
    if any(a.state != "purged" or a.purged_at is None or a.absence_checks < 1 for a in assets):
        return False
    db.execute(delete(VoiceAsset).where(VoiceAsset.id.in_([a.id for a in assets])))
    for asset in assets:
        db.execute(delete(VoiceJob).where(VoiceJob.kind == "purge",
            VoiceJob.request_key.like(f"asset-purge:{asset.id}:%")))
    db.execute(delete(VoiceJob).where(VoiceJob.id.in_([j.id for j in jobs])))
    return True


def finalize_account(sessions, storage, deletion_id):
    """Snapshot -> exact erasure without SQL locks -> locked recheck -> finalize."""
    with sessions() as db:
        row = db.get(AccountDeletion, deletion_id)
        if row is None or row.state == "completed":
            return "completed"
        user_id = row.user_id
        if db.scalar(select(Legacy.id).where(Legacy.owner_user_id == user_id).limit(1)) is not None:
            return "waiting_for_purge"
        registry = _source_registry(db, user_id)
        if registry is None:
            return "waiting_for_purge"
    from app.services.voice_runtime_cleanup import cleanup_voice_runtime
    if cleanup_voice_runtime() is None:
        return "waiting_for_purge"
    from app.services.visual_storage import VisualStorage
    eraser = VisualStorage(storage, sessions=sessions)
    for _, legacy_id, source_id, key, _, backend, encryption, sha256 in registry:
        if (backend != storage.backend_name or encryption != storage.encryption_key_id
                or not key.startswith(f"legarya/legacies/{legacy_id}/sources/{source_id}/")
                or (backend == "s3" and not sha256 and not eraser.writes.known(key))):
            return "waiting_for_purge"
        eraser.erase(key)
    with sessions.begin() as db:
        # The actor is always locked before the parent row. Concurrent workers
        # may both prove absence; only one can execute this terminal transaction.
        user = db.scalar(select(User).where(User.id == user_id).with_for_update())
        row = db.scalar(select(AccountDeletion).where(AccountDeletion.id == deletion_id)
                        .execution_options(populate_existing=True).with_for_update())
        if row.state == "completed":
            return "completed"
        if user is None or user.deletion_requested_at is None:
            raise RuntimeError("account_deletion_invariant")
        if db.scalar(select(Legacy.id).where(Legacy.owner_user_id == user_id).limit(1)) is not None:
            return "waiting_for_purge"
        ids = set(db.scalars(select(MediaSource.legacy_id).where(MediaSource.uploader_user_id == user_id)))
        ids.update(db.scalars(select(VoiceJob.legacy_id).where(VoiceJob.requested_by_user_id == user_id)))
        list(db.scalars(select(Legacy).where(Legacy.id.in_(ids)).order_by(Legacy.id).with_for_update()))
        if _source_registry(db, user_id) != registry or not _erase_shared_speech(db, user_id):
            return "waiting_for_purge"
        _erase_shared_sources(db, user_id)
        _remove_access(db, user)
        conversation_ids = select(Conversation.id).where(Conversation.user_id == user_id)
        message_ids = select(Message.id).where(Message.conversation_id.in_(conversation_ids))
        db.execute(update(MemoryRevision).where(or_(MemoryRevision.source_conversation_id.in_(conversation_ids),
            MemoryRevision.source_message_id.in_(message_ids))).values(source_conversation_id=None, source_message_id=None))
        # Audited nullable provenance is anonymized, never used to delete another
        # owner's canonical content. RESTRICT consent/approval links fail closed.
        for table in Base.metadata.tables.values():
            if table.name in {"account_deletions", "visual_companion_versions"}:
                continue
            for column in table.columns:
                if column.nullable and any(f.target_fullname == "users.id" and f.ondelete != "RESTRICT" for f in column.foreign_keys):
                    db.execute(update(table).where(column == user_id).values({column.name: None}))
        row.state, row.completed_at, row.user_id = "completed", now(), None
        row.request_proof_hash = row.session_hash = row.safe_error_code = row.next_attempt_at = None
        db.flush()
        # Core DELETE deliberately avoids ORM ownership cascades. Explicitly
        # staged owned Legacies must already be gone; FK restrictions remain on.
        db.execute(delete(User).where(User.id == user_id, User.deletion_requested_at.is_not(None)))
    return "completed"
