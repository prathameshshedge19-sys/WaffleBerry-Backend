"""L16 source-library lifecycle and authorization service."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.collaboration import CollaboratorStatus, LegacyCollaborator
from app.models.legacy import Legacy, LegacySetupStatus
from app.models.media_source import (
    ArtifactKind, ArtifactState, MediaArtifact, MediaProcessingJob, MediaSource,
    ProcessingJobKind, ProcessingJobState, SourceKind, SourceSafetyState,
    SourceState, ProcessingPurpose,
)
from app.models.user import User
from app.services.authorization import legacy_role, require_legacy
from app.services.media_storage import SourceStorage, StorageError, get_source_storage


PIPELINE_VERSION = "l16-foundation-v1"
_MIME_BY_KIND = {
    "image": {"image/jpeg", "image/png", "image/webp"},
    "audio": {"audio/mpeg", "audio/mp3", "audio/mp4", "audio/x-m4a", "audio/ogg", "audio/wav", "audio/x-wav", "audio/flac", "audio/webm"},
    "video": {"video/mp4", "video/webm"},
    "document": {"application/pdf", "text/plain"},
}
_MIME_CANONICAL = {"audio/mp3": "audio/mpeg", "audio/x-m4a": "audio/mp4", "audio/x-wav": "audio/wav"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def max_bytes(kind: str, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    return {"image": settings.media_max_photo_bytes, "document": settings.media_max_document_bytes,
            "audio": settings.media_max_audio_bytes, "video": settings.media_max_video_bytes}[kind]


def sanitize_filename(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").replace("\x00", "")
    value = PurePath(value.replace("\\", "/")).name
    value = re.sub(r"[\x00-\x1f\x7f]", "", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:255] or "unnamed-source"


def request_digest(kind: str, filename: str, mime_type: str, declared_size: int, processing_purpose: str = "source_review") -> str:
    payload = f"{kind}\n{filename}\n{mime_type}\n{declared_size}"
    # Preserve historical normal-upload receipts byte for byte. The explicit
    # purpose comparison on replay binds those historical digests too.
    if processing_purpose != ProcessingPurpose.SOURCE_REVIEW.value:
        payload += f"\nprocessing_purpose={processing_purpose}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _signature_ok(mime_type: str, data: bytes) -> bool:
    if mime_type == "image/jpeg": return data.startswith(b"\xff\xd8\xff")
    if mime_type == "image/png": return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/webp": return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if mime_type == "application/pdf": return data.startswith(b"%PDF-")
    if mime_type == "audio/mpeg": return data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)
    if mime_type == "audio/mp4": return len(data) >= 12 and data[4:8] == b"ftyp"
    if mime_type == "audio/wav": return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    if mime_type == "audio/flac": return data.startswith(b"fLaC")
    if mime_type == "audio/ogg": return data.startswith(b"OggS")
    if mime_type in {"audio/webm", "video/webm"}: return data.startswith(b"\x1a\x45\xdf\xa3")
    if mime_type == "video/mp4": return len(data) >= 12 and data[4:8] == b"ftyp"
    if mime_type == "text/plain":
        return b"\x00" not in data and bool(data.decode("utf-8"))
    return False


def validate_source_bytes(kind: str, mime_type: str, data: bytes, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    kind = kind.value if isinstance(kind, SourceKind) else kind
    mime_type = _MIME_CANONICAL.get((mime_type or "").split(";", 1)[0].strip().lower(), (mime_type or "").split(";", 1)[0].strip().lower())
    if kind not in _MIME_BY_KIND:
        raise HTTPException(422, detail={"code": "unsupported_source_kind", "message": "That source type is not supported."})
    if mime_type not in _MIME_BY_KIND[kind]:
        raise HTTPException(415, detail={"code": "unsupported_source_mime", "message": "That file format is not supported."})
    if not data: raise HTTPException(422, detail={"code": "empty_source", "message": "The source file is empty."})
    if len(data) > max_bytes(kind, settings): raise HTTPException(413, detail={"code": "source_too_large", "message": "That source file is too large."})
    if not _signature_ok(mime_type, data): raise HTTPException(415, detail={"code": "source_signature_invalid", "message": "The file contents do not match the selected format."})
    return mime_type


def _require_builder(db: Session, user_id: int, legacy_id: int) -> Legacy:
    legacy = require_legacy(db, user_id, legacy_id)
    if legacy.setup_status not in {LegacySetupStatus.ACTIVE.value, LegacySetupStatus.COLLECTING_IDENTITY.value}:
        raise HTTPException(409, detail={"code": "legacy_not_active", "message": "Sources are unavailable for this Legacy."})
    return legacy


def _load_source(db: Session, source_id: str, legacy_id: int, *, lock: bool = False) -> MediaSource:
    query = select(MediaSource).where(MediaSource.id == source_id, MediaSource.legacy_id == legacy_id)
    if lock:
        query = query.with_for_update()
    source = db.scalar(query)
    if source is None: raise HTTPException(404, detail="Source not found.")
    return source


def _can_read(db: Session, source: MediaSource, user_id: int) -> bool:
    legacy = db.get(Legacy, source.legacy_id)
    if legacy and legacy_role(db, user_id, legacy) == "owner": return True
    return source.uploader_user_id == user_id and bool(db.scalar(select(LegacyCollaborator.id).where(
        LegacyCollaborator.legacy_id == source.legacy_id, LegacyCollaborator.user_id == user_id,
        LegacyCollaborator.status == CollaboratorStatus.ACTIVE.value)))


def _authorize(db: Session, source: MediaSource, user_id: int, *, owner_only=False, uploader_only=False) -> None:
    legacy = _require_builder(db, user_id, source.legacy_id)
    role = legacy_role(db, user_id, legacy)
    if source.processing_purpose == ProcessingPurpose.VISUAL_REFERENCE.value and role != "owner":
        raise HTTPException(404, detail="Source not found.")
    if owner_only and role != "owner": raise HTTPException(403, detail="Only the Legacy owner can manage this source.")
    if uploader_only and source.uploader_user_id != user_id and role != "owner": raise HTTPException(403, detail="You cannot access this source.")
    if not owner_only and not _can_read(db, source, user_id): raise HTTPException(404, detail="Source not found.")


def serialize_source(source: MediaSource, job: MediaProcessingJob | None = None) -> dict:
    return {"id": source.id, "legacy_id": source.legacy_id, "uploader_user_id": source.uploader_user_id,
            "kind": source.kind, "original_filename": source.original_filename,
            "processing_purpose": source.processing_purpose,
            "mime_type": source.detected_mime_type or source.declared_mime_type,
            "declared_mime_type": source.declared_mime_type, "size_bytes": source.size_bytes,
            "declared_size_bytes": source.declared_size_bytes, "sha256": source.sha256,
            "state": source.state, "safety_state": source.safety_state, "generation": source.generation,
            "metadata": source.metadata_json or {}, "created_at": source.created_at, "updated_at": source.updated_at,
            "uploaded_at": source.uploaded_at, "processing_started_at": source.processing_started_at,
            "processing_finished_at": source.processing_finished_at, "deleted_at": source.deleted_at,
            "purged_at": source.purged_at, "last_error_code": source.last_error_code,
            "job": ({"id": job.id, "kind": job.kind, "state": job.state, "stage": job.stage,
                     "attempts": job.attempts, "next_attempt_at": job.next_attempt_at,
                     "last_error_code": job.last_error_code} if job else None)}


class MediaSourceService:
    def __init__(self, storage: SourceStorage | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings(); self.storage = storage or get_source_storage(self.settings)

    def create(self, db: Session, user: User, legacy_id: int, *, kind: str, filename: str, mime_type: str, size_bytes: int, upload_request_key: str, processing_purpose: str = "source_review") -> MediaSource:
        from app.services.plan_enforcement import admission, check_capacity
        # Scope first, then owner quota lock, then existing Legacy/source locks.
        scoped = _require_builder(db, user.id, legacy_id)
        with admission(db, scoped.owner_user_id, "storage_bytes"):
            pass  # Transaction retains the owner lock through reservation commit.
        db.scalar(select(Legacy).where(Legacy.id == legacy_id).execution_options(populate_existing=True).with_for_update())
        legacy = _require_builder(db, user.id, legacy_id)
        if legacy_role(db, user.id, legacy) not in {"owner", "collaborator"}: raise HTTPException(403, detail="You cannot add sources to this Legacy.")
        try: request_key = str(UUID(upload_request_key))
        except (ValueError, AttributeError): raise HTTPException(422, detail={"code": "invalid_upload_request_key", "message": "Upload request key is invalid."}) from None
        kind = kind.value if isinstance(kind, SourceKind) else kind
        processing_purpose = processing_purpose.value if isinstance(processing_purpose, ProcessingPurpose) else processing_purpose
        if processing_purpose not in {item.value for item in ProcessingPurpose}:
            raise HTTPException(422, detail={"code": "unsupported_processing_purpose"})
        if processing_purpose == ProcessingPurpose.VISUAL_REFERENCE.value:
            if legacy_role(db, user.id, legacy) != "owner":
                raise HTTPException(403, detail="Only the Legacy owner can add visual references.")
            if kind != "image":
                raise HTTPException(422, detail={"code": "visual_reference_image_required"})
            if size_bytes > 20 * 1024 * 1024:
                raise HTTPException(413, detail={"code": "source_too_large"})
        mime_type = _MIME_CANONICAL.get(mime_type.lower().split(";", 1)[0].strip(), mime_type.lower().split(";", 1)[0].strip())
        if kind not in _MIME_BY_KIND or mime_type not in _MIME_BY_KIND[kind]: validate_source_bytes(kind, mime_type, b"x", self.settings)
        if size_bytes < 1 or size_bytes > max_bytes(kind, self.settings): raise HTTPException(413, detail={"code": "source_too_large", "message": "That source file is too large."})
        safe_name = sanitize_filename(filename); digest = request_digest(kind, safe_name, mime_type, size_bytes, processing_purpose)
        existing = db.scalar(select(MediaSource).where(MediaSource.legacy_id == legacy.id, MediaSource.uploader_user_id == user.id, MediaSource.upload_request_key == request_key))
        if existing:
            if existing.processing_purpose != processing_purpose or existing.upload_request_digest != digest: raise HTTPException(409, detail={"code": "upload_request_conflict", "message": "That upload request key was already used."})
            return existing
        from app.services.plan_enforcement import enabled
        if enabled():
            check_capacity(db, legacy.owner_user_id, "storage_bytes", size_bytes)
        source_id, artifact_id = str(uuid4()), str(uuid4()); now = utcnow()
        source = MediaSource(id=source_id, legacy_id=legacy.id, uploader_user_id=user.id, kind=kind, processing_purpose=processing_purpose, original_filename=safe_name,
            declared_mime_type=mime_type, declared_size_bytes=size_bytes, state=SourceState.UPLOADING.value,
            safety_state=SourceSafetyState.PENDING.value, generation=1, metadata_json={}, upload_request_key=request_key,
            upload_request_digest=digest, upload_expires_at=now + timedelta(seconds=self.settings.media_upload_expire_seconds))
        object_key = f"legarya/legacies/{legacy.id}/sources/{source_id}/{artifact_id}"
        db.add(source); db.add(MediaArtifact(id=artifact_id, legacy_id=legacy.id, source_id=source_id, generation=1,
            kind=ArtifactKind.ORIGINAL.value, logical_key="original", storage_backend=self.storage.backend_name,
            object_key=object_key, encryption_key_id=self.storage.encryption_key_id, state=ArtifactState.RESERVED.value, mime_type=mime_type))
        db.add(MediaProcessingJob(id=str(uuid4()), legacy_id=legacy.id, source_id=source_id, generation=1,
            kind=ProcessingJobKind.EXTRACT.value, pipeline_version=PIPELINE_VERSION, state=ProcessingJobState.QUEUED.value,
            stage="awaiting_upload", checkpoint_json={}))
        try: db.commit()
        except IntegrityError:
            db.rollback(); existing = db.scalar(select(MediaSource).where(MediaSource.legacy_id == legacy.id, MediaSource.uploader_user_id == user.id, MediaSource.upload_request_key == request_key))
            if existing and existing.processing_purpose == processing_purpose and existing.upload_request_digest == digest: return existing
            raise HTTPException(409, detail={"code": "upload_request_conflict", "message": "That upload request key was already used."}) from None
        db.refresh(source); return source

    def receive(self, db: Session, user: User, legacy_id: int, source_id: str, data: bytes) -> MediaSource:
        from app.services.plan_enforcement import enabled, admission
        if enabled():
            scoped = _require_builder(db, user.id, legacy_id)
            with admission(db, scoped.owner_user_id, "storage_bytes"):
                pass  # Serialize reservation-to-stored transitions, including expiry.
        source = _load_source(db, source_id, legacy_id, lock=True); _authorize(db, source, user.id, uploader_only=True)
        if source.state != SourceState.UPLOADING.value:
            if source.state in {SourceState.QUEUED.value, SourceState.PROCESSING.value, SourceState.READY.value, SourceState.PARTIALLY_READY.value} and source.sha256 == hashlib.sha256(data).hexdigest(): return source
            raise HTTPException(409, detail={"code": "source_not_uploading", "message": "This source upload is no longer open."})
        if _aware(source.upload_expires_at) <= utcnow():
            source.state = SourceState.DELETING.value; source.generation += 1; source.last_error_code = "upload_expired"; db.commit()
            raise HTTPException(410, detail={"code": "upload_expired", "message": "That upload reservation expired."})
        if len(data) != source.declared_size_bytes: raise HTTPException(400, detail={"code": "source_size_mismatch", "message": "The uploaded byte count does not match the reservation."})
        detected = validate_source_bytes(source.kind, source.declared_mime_type, data, self.settings)
        artifact = db.scalar(select(MediaArtifact).where(MediaArtifact.legacy_id == legacy_id, MediaArtifact.source_id == source.id, MediaArtifact.logical_key == "original", MediaArtifact.generation == source.generation))
        if artifact is None: raise HTTPException(500, detail="Source artifact unavailable.")
        try: stored = self.storage.put(artifact.object_key, data, content_type=detected)
        except StorageError as exc: raise HTTPException(503, detail={"code": exc.code, "message": "The source could not be stored. Try again."}) from None
        now = utcnow(); source.detected_mime_type = detected; source.size_bytes = stored.byte_size; source.sha256 = hashlib.sha256(data).hexdigest()
        source.state = SourceState.QUEUED.value; source.safety_state = SourceSafetyState.CLEAN.value; source.uploaded_at = now; source.updated_at = now
        if source.processing_purpose == ProcessingPurpose.VISUAL_REFERENCE.value:
            source.safety_state = SourceSafetyState.PENDING.value
        artifact.state = ArtifactState.AVAILABLE.value; artifact.byte_size = stored.byte_size; artifact.sha256 = source.sha256; artifact.mime_type = detected; artifact.object_version = stored.version
        job = db.scalar(select(MediaProcessingJob).where(MediaProcessingJob.source_id == source.id, MediaProcessingJob.generation == source.generation, MediaProcessingJob.kind == ProcessingJobKind.EXTRACT.value))
        if job: job.stage = "admitted"; job.next_attempt_at = now
        try: db.commit()
        except Exception:
            db.rollback()
            try: self.storage.delete(artifact.object_key, version=stored.version)
            except StorageError: pass
            raise
        return source

    def list(self, db: Session, user: User, legacy_id: int) -> list[MediaSource]:
        legacy = _require_builder(db, user.id, legacy_id); role = legacy_role(db, user.id, legacy)
        query = select(MediaSource).where(MediaSource.legacy_id == legacy_id)
        if role != "owner": query = query.where(MediaSource.uploader_user_id == user.id, MediaSource.processing_purpose == ProcessingPurpose.SOURCE_REVIEW.value)
        return list(db.scalars(query.order_by(MediaSource.created_at.desc(), MediaSource.id.desc())).all())

    def get(self, db: Session, user: User, legacy_id: int, source_id: str) -> MediaSource:
        source = _load_source(db, source_id, legacy_id); _authorize(db, source, user.id); return source

    def delete(self, db: Session, user: User, legacy_id: int, source_id: str, *, commit: bool = True) -> MediaSource:
        from app.models.media_intelligence import CandidateReviewState, MemorySourceLink, SourceEvidence, SourceMemoryCandidate, SupportState
        db.scalar(select(Legacy).where(Legacy.id == legacy_id).with_for_update())
        source = _load_source(db, source_id, legacy_id, lock=True); _authorize(db, source, user.id, owner_only=True)
        if source.state in {SourceState.DELETING.value, SourceState.DELETED.value}: return source
        now = utcnow(); source.generation += 1; source.state = SourceState.DELETING.value; source.deleted_at = now; source.deleted_by_user_id = user.id
        db.execute(update(MediaProcessingJob).where(MediaProcessingJob.source_id == source.id, MediaProcessingJob.state.in_(("queued", "running", "retry_wait"))).values(state="cancelled", lease_token=None, lease_expires_at=None, finished_at=now, last_error_code="source_deleted"))
        db.execute(update(SourceMemoryCandidate).where(SourceMemoryCandidate.legacy_id == source.legacy_id, SourceMemoryCandidate.source_id == source.id, SourceMemoryCandidate.review_state == CandidateReviewState.PENDING.value).values(review_state=CandidateReviewState.CANCELLED.value, removed_at=now, proposal_json={}, review_draft_json=None))
        # Preserve terminal receipts, but erase private proposal/draft content
        # for skipped and approved candidates as well as pending candidates.
        db.execute(update(SourceMemoryCandidate).where(SourceMemoryCandidate.legacy_id == source.legacy_id, SourceMemoryCandidate.source_id == source.id).values(removed_at=now, proposal_json={}, review_draft_json=None))
        db.execute(update(SourceEvidence).where(SourceEvidence.legacy_id == source.legacy_id, SourceEvidence.source_id == source.id, SourceEvidence.removed_at.is_(None)).values(text=None, locator_json={"kind": "removed"}, origin_json={}, removed_at=now))
        db.execute(update(MemorySourceLink).where(MemorySourceLink.legacy_id == source.legacy_id, MemorySourceLink.source_id == source.id, MemorySourceLink.support_state == SupportState.APPROVED.value).values(support_state=SupportState.UNAVAILABLE.value, removed_at=now))
        if source.processing_purpose == ProcessingPurpose.SOURCE_REVIEW.value:
            from app.services.personality_invalidation import invalidate_in_transaction
            invalidate_in_transaction(db.connection(), [source.legacy_id])
        db.add(MediaProcessingJob(id=str(uuid4()), legacy_id=legacy_id, source_id=source.id, generation=source.generation, kind=ProcessingJobKind.PURGE.value, pipeline_version=PIPELINE_VERSION, state=ProcessingJobState.QUEUED.value, stage="awaiting_purge", checkpoint_json={}))
        from app.services.visual_companions import source_deleted_in_transaction
        source_deleted_in_transaction(db, source)
        if commit:
            db.commit(); db.refresh(source)
        else:
            db.flush()
        return source

    def retry(self, db: Session, user: User, legacy_id: int, source_id: str) -> MediaSource:
        db.scalar(select(Legacy).where(Legacy.id == legacy_id).with_for_update())
        source = _load_source(db, source_id, legacy_id, lock=True); _authorize(db, source, user.id, owner_only=True)
        if source.state not in {SourceState.FAILED.value, SourceState.PARTIALLY_READY.value}: raise HTTPException(409, detail={"code": "source_not_retryable", "message": "This source is not ready for retry."})
        job = db.scalar(select(MediaProcessingJob).where(MediaProcessingJob.source_id == source.id, MediaProcessingJob.generation == source.generation, MediaProcessingJob.kind == ProcessingJobKind.EXTRACT.value).with_for_update())
        if job is None: job = MediaProcessingJob(id=str(uuid4()), legacy_id=legacy_id, source_id=source.id, generation=source.generation, kind=ProcessingJobKind.EXTRACT.value, pipeline_version=PIPELINE_VERSION, state="queued", stage="retry", checkpoint_json={}); db.add(job)
        else: job.state = "queued"; job.lease_token = job.lease_expires_at = None; job.next_attempt_at = utcnow(); job.last_error_code = None
        source.state = SourceState.QUEUED.value; source.last_error_code = None; db.commit(); db.refresh(source); return source
