"""Owner-only L21 metadata/status API. No media content is accepted or served."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.schemas.voice_profile import VoiceActivation, VoiceEnrollmentIntent, VoiceRevisionCommand
from app.services.voice_profiles import VoiceEnrollmentService, VoiceProfileService

PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


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
    }}


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
