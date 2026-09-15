"""Owner-only private preserved-voice enrollment and status API."""

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.schemas.voice_profile import VoiceActivation, VoiceEnrollmentIntent, VoiceRevisionCommand
from app.services.media_storage import StorageError
from app.services.voice_profiles import (
    CONSENT_COPY, CONSENT_POLICY, CONSENT_TEXT, CONSENT_TEXT_DIGEST,
    StaleVoiceClaim, VoiceEnrollmentService, VoiceJobService, VoiceProfileService,
)
from app.services.voice_storage import VoiceStorage

PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
VOICE_UPLOAD_MIMES = frozenset({
    "audio/wav", "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/webm",
    "audio/ogg", "video/mp4", "video/webm",
})


class VoicePrivateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                response = await original(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, headers=PRIVATE_HEADERS, content={
                    "detail": {"code": "voice_request_invalid", "message": "Invalid Voice Profile request."}})
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), **PRIVATE_HEADERS}
                raise
            except Exception:
                return JSONResponse(status_code=503, headers=PRIVATE_HEADERS, content={
                    "detail": {"code": "voice_unavailable", "message": "Voice Profile is temporarily unavailable."}})
            response.headers.update(PRIVATE_HEADERS)
            return response

        return handle


router = APIRouter(prefix="/legacies/{legacy_id}/voice-profile", tags=["Voice Profile"], route_class=VoicePrivateRoute)


def cloning_enabled():
    if not get_settings().voice_cloning_enabled:
        raise HTTPException(404, detail="Voice Profile is unavailable.")


def enrollment_enabled():
    settings = get_settings()
    if not settings.voice_cloning_enabled or not settings.voice_enrollment_enabled:
        raise HTTPException(404, detail="Voice enrollment is unavailable.")


def _profile_json(status):
    settings = get_settings()
    return {**status, "capabilities": {
        "owner_managed": True,
        "can_enroll": settings.voice_cloning_enabled and settings.voice_enrollment_enabled,
        "message_playback": settings.voice_cloning_enabled and settings.voice_message_playback_enabled,
        "live": settings.voice_cloning_enabled and settings.voice_live_enabled,
    }, "consent": {"copy": CONSENT_TEXT, "copy_version": CONSENT_COPY,
        "policy_version": CONSENT_POLICY, "copy_digest": CONSENT_TEXT_DIGEST}}


def _version_json(version):
    return {"version_id": version.id, "version_number": version.version_number,
        "lifecycle": version.status, "language": version.language,
        "ready": version.status == "ready", "failure_code": version.safe_failure_code}


@router.get("", dependencies=[Depends(cloning_enabled)])
def get_profile(legacy_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    return _profile_json(VoiceProfileService().status(db, user.id, legacy_id))


@router.post("/enrollments", status_code=202, dependencies=[Depends(enrollment_enabled)])
def reserve_enrollment(legacy_id: int, payload: VoiceEnrollmentIntent,
                       user=Depends(get_current_user), db=Depends(get_db)):
    version = VoiceEnrollmentService().reserve_intent(db, user.id, legacy_id, payload)
    db.commit()
    return _version_json(version)


async def _bounded_body(request: Request, maximum: int) -> bytes:
    if request.headers.get("content-encoding"):
        raise HTTPException(415, detail={"code": "voice_media_encoding_unsupported",
            "message": "Compressed transfer encoding is not supported."})
    declared = request.headers.get("content-length")
    if declared:
        try:
            length = int(declared)
        except ValueError:
            length = -1
        if length < 1 or length > maximum:
            raise HTTPException(413, detail={"code": "voice_media_too_large",
                "message": "The recording is empty or exceeds the upload limit."})
    body = bytearray()
    async for chunk in request.stream():
        if not isinstance(chunk, bytes) or len(body) + len(chunk) > maximum:
            raise HTTPException(413, detail={"code": "voice_media_too_large",
                "message": "The recording is empty or exceeds the upload limit."})
        body.extend(chunk)
    if not body:
        raise HTTPException(422, detail={"code": "voice_media_empty",
            "message": "Choose a recording with audio."})
    return bytes(body)


@router.put("/enrollments/{version_id}/content", status_code=202,
    dependencies=[Depends(enrollment_enabled)])
async def upload_enrollment(legacy_id: int, version_id: UUID, request: Request,
                            user=Depends(get_current_user), db=Depends(get_db)):
    settings = get_settings()
    mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime not in VOICE_UPLOAD_MIMES:
        raise HTTPException(415, detail={"code": "voice_media_type_unsupported",
            "message": "Choose a supported audio or video recording."})
    source = await _bounded_body(request, settings.voice_enrollment_max_bytes)
    digest = hashlib.sha256(source).hexdigest()
    storage = VoiceStorage()
    service = VoiceEnrollmentService()
    version_key = str(version_id)
    asset, created = service.reserve_upload(db, user.id, legacy_id, version_key,
        sha256=digest, byte_count=len(source), mime_type=mime, storage=storage,
        retention_seconds=settings.voice_original_retention_seconds)
    db.commit()
    if not created and asset.state == "available":
        return {"version_id": version_key, "lifecycle": "queued"}
    try:
        stored = await run_in_threadpool(storage.put_original,
            asset.object_key, source, mime)
        asset.object_version = stored.version
        if not await run_in_threadpool(storage.verify, asset):
            raise StorageError("storage_verification_failed")
        version, _job = service.publish_upload(db, user.id, legacy_id,
            version_key, asset.id, stored)
        db.commit()
        return _version_json(version)
    except StaleVoiceClaim:
        db.rollback()
        VoiceJobService().schedule_asset_purge(db, legacy_id=legacy_id,
            asset_id=asset.id, not_before=asset.writer_deadline)
        db.commit()
        raise HTTPException(409, detail={"code": "voice_upload_changed",
            "message": "This enrollment changed before the upload completed."})
    except StorageError:
        db.rollback()
        service.fail_upload(db, user.id, legacy_id, version_key, asset.id,
            "voice_storage_unavailable")
        db.commit()
        raise HTTPException(503, detail={"code": "voice_storage_unavailable",
            "message": "The recording could not be stored securely. Please try again."})


@router.post("/activate", dependencies=[Depends(enrollment_enabled)])
def activate(legacy_id: int, payload: VoiceActivation,
             user=Depends(get_current_user), db=Depends(get_db)):
    profile = VoiceProfileService().activate(db, user.id, legacy_id, payload)
    db.commit()
    return _profile_json(VoiceProfileService().status(db, user.id, legacy_id))


@router.post("/revoke", status_code=202, dependencies=[Depends(cloning_enabled)])
def revoke(legacy_id: int, payload: VoiceRevisionCommand,
           user=Depends(get_current_user), db=Depends(get_db)):
    VoiceProfileService().revoke(db, user.id, legacy_id, payload.expected_revision)
    db.commit()
    return _profile_json(VoiceProfileService().status(db, user.id, legacy_id))


# Cleanup remains callable after feature disablement; authorization and revision
# fencing still apply. This mirrors the existing L19 deletion contract.
@router.delete("", status_code=202)
def delete(legacy_id: int, expected_revision: int = Query(ge=1),
           user=Depends(get_current_user), db=Depends(get_db)):
    VoiceProfileService().delete(db, user.id, legacy_id, expected_revision)
    db.commit()
    return {"status": "deleting"}
