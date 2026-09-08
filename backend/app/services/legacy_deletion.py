"""Owner-confirmed, irreversible removal with durable private-file cleanup.

The marker revokes application access immediately. Never discard object registry
rows until existing purge workers and an exact-key erasure check have succeeded.
No bucket-wide listing, inferred Legacy scope, or storage I/O in request locks.
"""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update

from app.models.legacy import Legacy
from app.models.user import User
from app.models.conversation import Conversation
from app.models.turn import ConversationTurn
from app.models.memory import Memory
from app.models.timeline import LifeEvent, LifeEventEvidence
from app.models.story import Story, StorySupportLink
from app.models.media_source import MediaSource, MediaArtifact, MediaProcessingJob
from app.models.media_intelligence import MemorySourceLink, SourceCandidateEvidence, SourceMemoryCandidate, SourceEvidence
from app.models.visual_companion import VisualCompanion, VisualCompanionVersion, VisualCompanionAsset, VisualGenerationJob
from app.services.media_sources import MediaSourceService, PIPELINE_VERSION
from app.services.visual_companions import VisualCompanionService
from app.services.visual_storage import VisualStorage
from app.services.media_storage import StorageError


def confirmation_text(legacy):
    return f"DELETE LEGACY {legacy.id}"


def preview(db, owner_id, legacy_id):
    legacy = db.get(Legacy, legacy_id, populate_existing=True)
    if legacy is None or legacy.owner_user_id != owner_id:
        raise HTTPException(404, detail="Legacy not found.")
    counts = {key: db.scalar(select(func.count()).select_from(model).where(model.legacy_id == legacy_id))
              for key, model in (("memories", Memory), ("conversations", Conversation),
                                 ("timeline_events", LifeEvent), ("saved_stories", Story), ("uploaded_files", MediaSource))}
    return {"legacy_id": legacy.id, "subject_name": legacy.subject_name,
            "confirmation_text": confirmation_text(legacy), "counts": counts,
            "status": "deleting" if legacy.deletion_requested_at else "available"}


def request_deletion(db, user, legacy_id, confirmation, *, source_service=None):
    # Source admission and canonical writers use the same Legacy fence.
    legacy = db.scalar(select(Legacy).where(Legacy.id == legacy_id)
                       .execution_options(populate_existing=True).with_for_update())
    if legacy is None or legacy.owner_user_id != user.id:
        raise HTTPException(404, detail="Legacy not found.")
    if confirmation != confirmation_text(legacy):
        raise HTTPException(422, detail="Type the exact confirmation text before deleting this Legacy.")
    if legacy.deletion_requested_at is not None:
        return {"legacy_id": legacy_id, "status": "deleting"}
    service = source_service or MediaSourceService()
    sources = db.scalars(select(MediaSource).where(MediaSource.legacy_id == legacy_id)
                         .order_by(MediaSource.id).with_for_update()).all()
    for source in sources:
        service.delete(db, user, legacy_id, source.id, commit=False)
        # Recover expired reservations that were already marked deleting but
        # have no live purge job. Never mistake that state for completed erasure.
        if source.state == "deleting" and db.scalar(select(MediaProcessingJob.id).where(
                MediaProcessingJob.source_id == source.id, MediaProcessingJob.kind == "purge",
                MediaProcessingJob.state.in_(("queued", "running", "retry_wait")))) is None:
            db.add(MediaProcessingJob(id=str(uuid4()), legacy_id=legacy_id, source_id=source.id,
                generation=source.generation, kind="purge", pipeline_version=PIPELINE_VERSION,
                state="queued", stage="awaiting_purge", checkpoint_json={}))
    profile = db.scalar(select(VisualCompanion).where(VisualCompanion.legacy_id == legacy_id))
    if profile is not None:
        VisualCompanionService().delete(db, user.id, legacy_id, profile.revision)
    legacy.deletion_requested_at = datetime.now(timezone.utc)
    legacy.setup_status = "archived"
    legacy.collaborator_code_enabled = legacy.viewer_code_enabled = False
    legacy.collaborator_code_digest = legacy.collaborator_code_ciphertext = legacy.collaborator_code_hint = None
    legacy.viewer_code_digest = legacy.viewer_code_ciphertext = legacy.viewer_code_hint = None
    db.commit()
    return {"legacy_id": legacy_id, "status": "deleting"}


def _ready(db, legacy_id):
    for model, clause in (
        (MediaSource, (MediaSource.state != "deleted") | MediaSource.purged_at.is_(None)),
        (MediaArtifact, MediaArtifact.state != "purged"),
        (VisualCompanionVersion, VisualCompanionVersion.state != "purged"),
        (VisualCompanionAsset, VisualCompanionAsset.state != "purged"),
    ):
        if db.scalar(select(model.id).where(model.legacy_id == legacy_id, clause).limit(1)) is not None:
            return False
    return True


def finalize_one(sessions, storage):
    with sessions() as db:
        pending = list(db.scalars(select(Legacy.id).where(Legacy.deletion_requested_at.is_not(None))
                                  .order_by(Legacy.deletion_requested_at, Legacy.id)))
    for legacy_id in pending:
        if _finalize_id(sessions, storage, legacy_id) == "legacy_erased":
            return "legacy_erased"
    return "legacy_cleanup_pending" if pending else "idle"


def _finalize_id(sessions, storage, legacy_id):
    """Existing media worker calls this after normal jobs, including idle cycles.

Unconfirmed historical S3 uploads stay fail-closed with their cleanup metadata;
absence alone cannot prove that a previously accepted remote PUT won't arrive.
"""
    with sessions() as db:
        legacy = db.get(Legacy, legacy_id)
        if legacy is None or legacy.deletion_requested_at is None:
            return "idle"
        if not _ready(db, legacy_id):
            return "legacy_cleanup_pending"
        artifacts = list(db.scalars(select(MediaArtifact).where(MediaArtifact.legacy_id == legacy_id)))
        registry = {(a.id, a.object_key, a.object_version) for a in artifacts}
        for artifact in artifacts:
            if (artifact.storage_backend != storage.backend_name
                    or artifact.encryption_key_id != storage.encryption_key_id
                    or not artifact.object_key.startswith(f"legarya/legacies/{legacy_id}/sources/{artifact.source_id}/")):
                return "legacy_cleanup_pending"
            if storage.backend_name == "s3" and not artifact.sha256:
                return "legacy_cleanup_pending"
    try:
        eraser = VisualStorage(storage)
        for _, key, _ in registry:
            eraser.erase(key)  # exact key and its versions only; errors retain registry
    except (StorageError, OSError):
        return "legacy_cleanup_pending"
    with sessions.begin() as db:
        # Drain admitted work before taking the Legacy fence: conversation ->
        # turn -> Legacy matches the normal turn's canonical effect ordering.
        users = db.scalars(select(User).where(User.active_legacy_id == legacy_id).order_by(User.id).with_for_update()).all()
        db.scalars(select(Conversation).where(Conversation.legacy_id == legacy_id).order_by(Conversation.id).with_for_update()).all()
        db.scalars(select(ConversationTurn).where(ConversationTurn.legacy_id == legacy_id).order_by(ConversationTurn.id).with_for_update()).all()
        legacy = db.scalar(select(Legacy).where(Legacy.id == legacy_id).with_for_update())
        if legacy is None:
            return "idle"
        if legacy.deletion_requested_at is None or not _ready(db, legacy_id):
            return "legacy_cleanup_pending"
        current = set(db.execute(select(MediaArtifact.id, MediaArtifact.object_key, MediaArtifact.object_version)
                                 .where(MediaArtifact.legacy_id == legacy_id)).all())
        if current != registry:
            return "legacy_cleanup_pending"
        for user in users:
            user.active_legacy_id = None
        db.execute(update(VisualCompanion).where(VisualCompanion.legacy_id == legacy_id)
                   .values(current_version_id=None, desired_version_id=None))
        db.flush()
        # Explicit child-first order for RESTRICT FKs. Remaining same-Legacy
        # facts, messages, memberships and derived rows use existing cascades.
        for model in (VisualGenerationJob, VisualCompanionAsset, VisualCompanionVersion, VisualCompanion,
                      StorySupportLink, LifeEventEvidence, MemorySourceLink, SourceCandidateEvidence,
                      SourceMemoryCandidate, SourceEvidence, MediaProcessingJob, MediaArtifact, MediaSource,
                      Conversation):
            db.execute(delete(model).where(model.legacy_id == legacy_id))
        db.execute(delete(Legacy).where(Legacy.id == legacy_id))
    return "legacy_erased"
