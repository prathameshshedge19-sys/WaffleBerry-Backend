from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from urllib.parse import quote
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.media_source import ArtifactKind, ArtifactState, MediaArtifact, MediaProcessingJob, SourceSafetyState, SourceState
from app.models.user import User
from app.schemas.media_source import SourceCreate, SourceResponse
from app.services.media_sources import MediaSourceService, serialize_source
from app.services.media_storage import StorageError
from app.services.authorization import require_legacy, legacy_role
from app.services.media_sources import max_bytes


def _private(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/legacies/{legacy_id}/sources", tags=["Media & Sources"], dependencies=[Depends(_private)])


def _enabled() -> None:
    if not get_settings().media_enabled:
        raise HTTPException(404, detail="Media sources are not enabled.")


def _with_job(db: Session, source):
    job = db.scalar(select(MediaProcessingJob).where(MediaProcessingJob.source_id == source.id, MediaProcessingJob.generation == source.generation).order_by(MediaProcessingJob.created_at.desc()))
    result = serialize_source(source, job)
    uploader = db.get(User, source.uploader_user_id) if source.uploader_user_id else None
    result["uploader_name"] = uploader.full_name if uploader else None
    return result


@router.get("/capabilities")
def source_capabilities(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_legacy(db, user.id, legacy_id)
    settings = get_settings()
    active = legacy.setup_status == "active"
    return {"enabled": settings.media_enabled and active, "can_review": legacy_role(db, user.id, legacy) == "owner",
            "formats": [{"kind": kind, "mime_type": mime, "extensions": extensions, "max_bytes": max_bytes(kind, settings)}
                        for kind, mime, extensions in [("document", "text/plain", [".txt"]), ("document", "application/pdf", [".pdf"]),
                            ("image", "image/jpeg", [".jpg", ".jpeg"]), ("image", "image/png", [".png"]), ("image", "image/webp", [".webp"])]],
            "audio_video_intelligence": False, "scanned_pdf_intelligence": False,
            "coverage_note": "Document review covers up to 32 text sections. Scanned PDFs and audio/video transcription are not available."}


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
def create_source(legacy_id: int, payload: SourceCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    source = MediaSourceService().create(db, user, legacy_id, kind=payload.kind, filename=payload.filename,
                                         mime_type=payload.mime_type, size_bytes=payload.size_bytes,
                                         upload_request_key=str(payload.upload_request_key), processing_purpose=payload.processing_purpose)
    return _with_job(db, source)


@router.put("/{source_id}/content", response_model=SourceResponse, status_code=status.HTTP_202_ACCEPTED)
async def receive_source(legacy_id: int, source_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    settings = get_settings()
    source = MediaSourceService().get(db, user, legacy_id, source_id)
    limit = min(source.declared_size_bytes, max_bytes(source.kind, settings))
    # End the read-only preflight transaction before waiting for network I/O.
    # Expire its identity map so receive() locks and checks current source state
    # if deletion or another upload commits while the body is arriving.
    db.rollback()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(413, detail={"code": "source_too_large", "message": "That source file is too large."})
    source = MediaSourceService().receive(db, user, legacy_id, source_id, bytes(body))
    return _with_job(db, source)


@router.get("", response_model=list[SourceResponse])
def list_sources(legacy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    service = MediaSourceService()
    return [_with_job(db, source) for source in service.list(db, user, legacy_id)]


@router.get("/{source_id}", response_model=SourceResponse)
def get_source(legacy_id: int, source_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    source = MediaSourceService().get(db, user, legacy_id, source_id)
    return _with_job(db, source)


@router.get("/{source_id}/evidence")
def source_evidence(legacy_id: int, source_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    source = MediaSourceService().get(db, user, legacy_id, source_id)
    if source.state in {"deleting", "deleted"}:
        raise HTTPException(410, detail="Original source no longer available.")
    from app.models.media_intelligence import SourceEvidence
    rows = db.scalars(select(SourceEvidence).where(SourceEvidence.legacy_id == legacy_id, SourceEvidence.source_id == source_id,
        SourceEvidence.removed_at.is_(None)).order_by(SourceEvidence.created_at, SourceEvidence.id).limit(200)).all()
    return [{"id": row.id, "kind": row.kind, "text": row.text, "locator": row.locator_json,
             "language": row.language, "confidence": row.confidence, "origin": row.origin_json} for row in rows]


def _stream(handle) -> Iterator[bytes]:
    try:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        handle.close()


@router.get("/{source_id}/content")
def read_source(legacy_id: int, source_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    service = MediaSourceService(); source = service.get(db, user, legacy_id, source_id)
    if source.state in {SourceState.DELETING.value, SourceState.DELETED.value} or source.safety_state != SourceSafetyState.CLEAN.value:
        raise HTTPException(404, detail="Source content is unavailable.")
    artifact = db.scalar(select(MediaArtifact).where(MediaArtifact.legacy_id == legacy_id, MediaArtifact.source_id == source.id,
                                                     MediaArtifact.kind == ArtifactKind.ORIGINAL.value, MediaArtifact.state == ArtifactState.AVAILABLE.value))
    if artifact is None:
        raise HTTPException(404, detail="Source content is unavailable.")
    try:
        handle = service.storage.open(artifact.object_key)
    except StorageError:
        raise HTTPException(503, detail={"code": "storage_unavailable", "message": "Source content is temporarily unavailable."}) from None
    return StreamingResponse(_stream(handle), media_type=source.detected_mime_type or source.declared_mime_type,
                                    headers={"Content-Disposition": f"attachment; filename=source; filename*=UTF-8''{quote(source.original_filename, safe='')}",
                                             "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.delete("/{source_id}", response_model=SourceResponse, status_code=status.HTTP_202_ACCEPTED)
def delete_source(legacy_id: int, source_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    return _with_job(db, MediaSourceService().delete(db, user, legacy_id, source_id))


@router.post("/{source_id}/retry", response_model=SourceResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_source(legacy_id: int, source_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _enabled()
    return _with_job(db, MediaSourceService().retry(db, user, legacy_id, source_id))
