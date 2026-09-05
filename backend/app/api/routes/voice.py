import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.conversation import Conversation, Message, MessageRole
from app.models.user import User
from app.schemas.voice import SpeechRequest, TranscriptionResponse, VoicePreferenceResponse, VoicePreferenceUpdate, VoicePreviewRequest
from app.services.voice import VOICE_PREVIEW_TEXT, VoiceProvider, VoiceProviderError, get_voice_provider, speech_cache, speech_text


router = APIRouter(prefix="/voice", tags=["Voice-enhanced chat"])
logger = logging.getLogger(__name__)
ALLOWED_AUDIO_TYPES = {
    "audio/webm": ".webm",
    "audio/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mpga": ".mpga",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
}


def _provider_failure(exc: VoiceProviderError, message: str) -> HTTPException:
    logger.warning("Voice provider failure kind=%s", exc.kind)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": exc.kind, "message": message})


@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    audio: UploadFile = File(...),
    duration_ms: int | None = Form(default=None),
    user: User = Depends(get_current_user),
    provider: VoiceProvider = Depends(get_voice_provider),
):
    settings = get_settings()
    media_type = (audio.content_type or "").split(";", 1)[0].strip().lower()
    if media_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=415, detail={"code": "unsupported_audio", "message": "That audio format isn't supported."})
    if duration_ms is not None and (duration_ms < 0 or duration_ms > settings.voice_max_recording_seconds * 1000 + 2000):
        raise HTTPException(status_code=413, detail={"code": "recording_too_long", "message": "That recording is too long."})
    content = await audio.read(settings.voice_max_upload_bytes + 1)
    await audio.close()
    if not content:
        raise HTTPException(status_code=422, detail={"code": "empty_audio", "message": "The recording was empty."})
    if len(content) > settings.voice_max_upload_bytes:
        raise HTTPException(status_code=413, detail={"code": "audio_too_large", "message": "That recording is too large."})
    suffix = Path(audio.filename or "").suffix.lower()
    filename = f"recording{suffix if suffix in set(ALLOWED_AUDIO_TYPES.values()) else ALLOWED_AUDIO_TYPES[media_type]}"
    try:
        text = await provider.transcribe(content, filename, media_type)
    except VoiceProviderError as exc:
        raise _provider_failure(exc, "I couldn't transcribe that recording. Try again.") from None
    logger.info("voice_stt user_id=%s bytes=%s model=%s", user.id, len(content), settings.voice_transcription_model)
    return {"text": text}


@router.get("/settings", response_model=VoicePreferenceResponse)
def get_voice_settings(user: User = Depends(get_current_user)):
    return {"voice": user.voice_preference or "marin"}


@router.put("/settings", response_model=VoicePreferenceResponse)
def update_voice_settings(payload: VoicePreferenceUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.voice_preference = payload.voice
    db.commit()
    db.refresh(user)
    return {"voice": user.voice_preference}


def _owned_assistant_message(db: Session, message_id: int, user_id: int) -> Message:
    message = db.scalar(
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Message.id == message_id, Message.role == MessageRole.ASSISTANT, Conversation.user_id == user_id)
    )
    if message is None:
        raise HTTPException(status_code=404, detail={"code": "message_not_found", "message": "That response is unavailable."})
    return message


async def _synthesize(text: str, voice: str, provider: VoiceProvider) -> bytes:
    if len(text) > get_settings().voice_max_tts_characters:
        raise HTTPException(status_code=413, detail={"code": "speech_too_long", "message": "That response is too long for voice playback."})
    try:
        return await provider.synthesize(speech_text(text), voice)
    except VoiceProviderError as exc:
        raise _provider_failure(exc, "Voice playback unavailable.") from None


@router.post("/synthesize")
async def synthesize_message(payload: SpeechRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db), provider: VoiceProvider = Depends(get_voice_provider)):
    message = _owned_assistant_message(db, payload.message_id, user.id)
    voice = payload.voice or user.voice_preference or "marin"
    key = speech_cache.key(message.id, voice, message.content)
    audio = speech_cache.get(key)
    cache_status = "hit"
    if audio is None:
        audio = await _synthesize(message.content, voice, provider)
        speech_cache.put(key, audio)
        cache_status = "miss"
        logger.info("voice_tts user_id=%s message_id=%s voice=%s model=%s", user.id, message.id, voice, get_settings().voice_tts_model)
    else:
        logger.info("voice_tts_cache user_id=%s message_id=%s voice=%s", user.id, message.id, voice)
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=3600", "X-Voice-Cache": cache_status})


@router.post("/preview")
async def preview_voice(payload: VoicePreviewRequest, user: User = Depends(get_current_user), provider: VoiceProvider = Depends(get_voice_provider)):
    key = speech_cache.key(0, payload.voice, VOICE_PREVIEW_TEXT)
    audio = speech_cache.get(key)
    cache_status = "hit"
    if audio is None:
        audio = await _synthesize(VOICE_PREVIEW_TEXT, payload.voice, provider)
        speech_cache.put(key, audio)
        cache_status = "miss"
        logger.info("voice_preview user_id=%s voice=%s", user.id, payload.voice)
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=86400", "X-Voice-Cache": cache_status})
