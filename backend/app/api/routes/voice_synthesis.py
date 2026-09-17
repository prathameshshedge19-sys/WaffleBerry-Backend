"""L21.4 fixed preview and persisted Legacy-message speech delivery."""

from __future__ import annotations

from datetime import timezone
import hashlib
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.routes.voice_profile import VoicePrivateRoute
from app.config import Settings, get_settings
from app.database import get_db
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.user import User
from app.models.voice_profile import VoiceAsset, VoiceJob
from app.services.authorization import require_persona_legacy
from app.services.media_storage import StorageError
from app.services.voice import VoiceProvider, VoiceProviderError, get_voice_provider
from app.services.voice_profiles import AuthorizedSpeechContext, LegacySpeechOrchestrator, VoiceJobService, utcnow
from app.services.voice_storage import VoiceStorage
from app.services.voice_synthesis_manifest import inference_config_digest, test_manifest


router = APIRouter(tags=["Preserved voice synthesis"], route_class=VoicePrivateRoute)
PRIVATE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
PREVIEW_TEXT = "नमस्कार. हा माझ्या जतन केलेल्या आवाजाचा नमुना आहे."
PREVIEW_TEXT_VERSION = "l21-owner-preview-mr-v1"
PREVIEW_TEXT_DIGEST = hashlib.sha256(PREVIEW_TEXT.encode("utf-8")).hexdigest()


class _NoVoiceSelection(BaseModel):
    """Reject caller-selected profiles, versions, manifests, or synthesis text."""

    model_config = ConfigDict(extra="forbid")


def _json(value, code=200):
    return JSONResponse(value, status_code=code, headers=PRIVATE)


def _enabled(settings: Settings):
    if not (settings.voice_cloning_enabled and settings.voice_message_playback_enabled):
        raise HTTPException(404, detail={"code": "voice_playback_unavailable",
            "message": "Preserved voice playback is unavailable."})


def _manifest_digest(settings):
    if settings.voice_synthesis_manifest_digest:
        return settings.voice_synthesis_manifest_digest
    if settings.legarya_debug and settings.voice_synthesis_provider == "fake":
        return test_manifest().digest
    raise HTTPException(503, detail={"code": "voice_worker_unavailable",
        "message": "Preserved voice playback is temporarily unavailable."})


def _message(db, user_id, conversation_id, message_id):
    row = db.execute(select(Message, Conversation).join(Conversation,
        Message.conversation_id == Conversation.id).where(
        Message.id == message_id, Message.conversation_id == conversation_id,
        Message.role == MessageRole.ASSISTANT,
        Conversation.id == conversation_id, Conversation.user_id == user_id,
        Conversation.mode == "legacy")).first()
    if row is None:
        raise HTTPException(404, detail={"code": "message_not_found",
            "message": "That response is unavailable."})
    message, conversation = row
    if conversation.legacy_id is None:
        raise HTTPException(404, detail={"code": "message_not_found",
            "message": "That response is unavailable."})
    require_persona_legacy(db, user_id, conversation.legacy_id)
    return message, conversation


def _authorize_job(db, job_id, user_id):
    job = db.get(VoiceJob, job_id)
    if job is None or job.kind != "synthesize" or job.requested_by_user_id != user_id:
        raise HTTPException(404, detail={"code": "voice_job_not_found",
            "message": "That voice request is unavailable."})
    if job.purpose == "preview":
        legacy = db.scalar(select(Legacy).where(Legacy.id == job.legacy_id,
            Legacy.owner_user_id == user_id, Legacy.deletion_requested_at.is_(None)))
        if legacy is None:
            raise HTTPException(404, detail={"code": "voice_job_not_found",
                "message": "That voice request is unavailable."})
    elif job.purpose == "message":
        _message(db, user_id, job.conversation_id, job.message_id)
    else:
        raise HTTPException(404, detail={"code": "voice_job_not_found",
            "message": "That voice request is unavailable."})
    return job


def _job_payload(job):
    state = {"succeeded": "ready", "retry_wait": "queued"}.get(job.state, job.state)
    return {"job_id": job.id, "state": state, "voice_delivery": "preserved",
        "poll_after_ms": 1500 if state in {"queued", "running"} else None,
        "fallback_available": bool(job.purpose == "message" and state == "failed"),
        "error_code": job.safe_error_code if state == "failed" else None}


@router.post("/legacies/{legacy_id}/voice-profile/preview")
def preview(legacy_id: int, payload: _NoVoiceSelection | None = Body(default=None),
            user: User = Depends(get_current_user),
            db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    _enabled(settings)
    legacy = db.scalar(select(Legacy).where(Legacy.id == legacy_id,
        Legacy.owner_user_id == user.id, Legacy.deletion_requested_at.is_(None)))
    if legacy is None:
        raise HTTPException(404, detail={"code": "legacy_not_found", "message": "Legacy not found."})
    context = AuthorizedSpeechContext(legacy_id, "legacy", user.id, None, 1)
    orchestrator = LegacySpeechOrchestrator()
    if orchestrator.resolve_version(db, context) is None:
        raise HTTPException(409, detail={"code": "preserved_voice_not_ready",
            "message": "A preserved voice is not ready."})
    job = orchestrator.admit_synthesis(db, context,
        authoritative_text=PREVIEW_TEXT, purpose="preview",
        request_key=PREVIEW_TEXT_VERSION, model_manifest_digest=_manifest_digest(settings),
        inference_config_digest=inference_config_digest())
    db.commit()
    return _json({**_job_payload(job), "preview_text_version": PREVIEW_TEXT_VERSION},
        200 if job.state == "succeeded" else 202)


@router.post("/legacy-conversations/{conversation_id}/messages/{message_id}/speech")
async def message_speech(conversation_id: int, message_id: int,
        payload: _NoVoiceSelection | None = Body(default=None),
        standard_fallback: bool = Query(default=False),
        user: User = Depends(get_current_user), db: Session = Depends(get_db),
        provider: VoiceProvider = Depends(get_voice_provider),
        settings: Settings = Depends(get_settings)):
    message, conversation = _message(db, user.id, conversation_id, message_id)
    context = AuthorizedSpeechContext(conversation.legacy_id, "legacy", user.id,
        message.id, 1)
    orchestrator = LegacySpeechOrchestrator()
    preserved_enabled = settings.voice_cloning_enabled and settings.voice_message_playback_enabled
    preserved = None if standard_fallback or not preserved_enabled else orchestrator.resolve_version(db, context)
    job = None if preserved is None else orchestrator.admit_synthesis(db, context,
        authoritative_text=message.content, purpose="message",
        request_key=f"message:{conversation.id}:{message.id}",
        model_manifest_digest=_manifest_digest(settings),
        inference_config_digest=inference_config_digest(),
        conversation_id=conversation.id, message_id=message.id)
    if job is not None:
        db.commit()
        return _json(_job_payload(job), 200 if job.state == "succeeded" else 202)
    if len(message.content) > settings.voice_max_tts_characters:
        raise HTTPException(413, detail={"code": "speech_too_long",
            "message": "That response is too long for voice playback."})
    try:
        audio = await provider.synthesize(message.content, user.voice_preference or "marin")
    except VoiceProviderError as exc:
        raise HTTPException(503, detail={"code": exc.kind,
            "message": "Voice playback is temporarily unavailable."}) from None
    return Response(audio, media_type="audio/mpeg", headers={**PRIVATE,
        "X-Voice-Delivery": "standard_fallback"})


@router.get("/voice-synthesis/jobs/{job_id}")
def job_status(job_id: UUID, user: User = Depends(get_current_user),
               db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    _enabled(settings)
    VoiceJobService().expire_generated(db)
    job = _authorize_job(db, str(job_id), user.id)
    db.commit()
    return _json(_job_payload(job))


@router.get("/voice-synthesis/jobs/{job_id}/content")
def job_content(job_id: UUID, user: User = Depends(get_current_user),
        db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    _enabled(settings)
    service = VoiceJobService()
    service.expire_generated(db)
    job = _authorize_job(db, str(job_id), user.id)
    asset = db.scalar(select(VoiceAsset).where(VoiceAsset.job_id == job.id,
        VoiceAsset.kind == "generated", VoiceAsset.state == "available"))
    expiry = asset.expires_at if asset is not None else None
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if job.state != "succeeded" or asset is None or expiry is None or expiry <= utcnow():
        db.commit()
        raise HTTPException(404, detail={"code": "voice_audio_not_found",
            "message": "That generated audio is unavailable."})
    try:
        audio = VoiceStorage().read_private(asset)
        if len(audio) != asset.byte_count or hashlib.sha256(audio).hexdigest() != asset.sha256:
            raise StorageError("storage_verification_failed")
    except StorageError:
        raise HTTPException(503, detail={"code": "voice_audio_unavailable",
            "message": "Voice playback is temporarily unavailable."}) from None
    return Response(audio, media_type="audio/wav", headers={**PRIVATE,
        "X-Voice-Delivery": "preserved"})
