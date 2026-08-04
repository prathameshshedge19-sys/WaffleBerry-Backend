"""Authenticated transient audio transcription endpoint."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response

from app.api.v1.speech_http import speech_audio_response, speech_http_error
from app.dependencies.ai import get_speech_service, get_transcription_service
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.audio import AudioTranscriptionResponse, SpeechSynthesisRequest
from app.services.ai.exceptions import (
    AIAuthenticationError,
    AIConnectionError,
    AIInvalidResponseError,
    AIProviderError,
    AIProviderUnavailableError,
    AIQuotaExceededError,
    AIRateLimitError,
    AITimeoutError,
)
from app.services.ai.speech_service import SpeechService
from app.services.ai.transcription_service import (
    MAX_AUDIO_UPLOAD_BYTES,
    AudioValidationError,
    TranscriptionService,
    validate_audio_upload,
)


router = APIRouter()


def _safe_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _provider_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (AIRateLimitError, AIQuotaExceededError)):
        return _safe_error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "transcription_rate_limited",
            "Transcription is temporarily unavailable.",
        )
    if isinstance(exc, AITimeoutError):
        return _safe_error(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "transcription_timeout",
            "The transcription request timed out.",
        )
    if isinstance(
        exc,
        (
            AIAuthenticationError,
            AIConnectionError,
            AIProviderUnavailableError,
        ),
    ):
        return _safe_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "transcription_provider_unavailable",
            "Transcription is temporarily unavailable.",
        )
    if isinstance(exc, (AIInvalidResponseError, AIProviderError, ValueError)):
        return _safe_error(
            status.HTTP_502_BAD_GATEWAY,
            "transcription_failed",
            "The recording could not be transcribed. Please try again.",
        )
    return _safe_error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "transcription_provider_unavailable",
        "Transcription is temporarily unavailable.",
    )


def get_speech_service_for_request() -> SpeechService:
    """Resolve speech configuration lazily with a safe API error."""
    try:
        return get_speech_service()
    except Exception as exc:
        raise speech_http_error(exc) from None


@router.post(
    "/audio/transcribe",
    response_model=AudioTranscriptionResponse,
)
async def transcribe_audio(
    file: UploadFile | None = File(default=None),
    _current_user: User = Depends(get_current_user),
    service: TranscriptionService = Depends(get_transcription_service),
) -> AudioTranscriptionResponse:
    """Transcribe bounded audio in memory; duration is client-limited to 60s."""
    if file is None:
        raise _safe_error(
            status.HTTP_400_BAD_REQUEST,
            "audio_missing",
            "An audio file is required.",
        )

    try:
        data = await file.read(MAX_AUDIO_UPLOAD_BYTES + 1)
        validated = validate_audio_upload(
            data,
            file.content_type,
        )
        text = await service.transcribe(validated)
        return AudioTranscriptionResponse(text=text)
    except AudioValidationError as exc:
        raise _safe_error(
            exc.status_code,
            exc.code,
            exc.safe_message,
        ) from None
    except Exception as exc:
        raise _provider_error(exc) from None
    finally:
        await file.close()


@router.post("/audio/speech", response_class=Response)
async def synthesize_speech(
    request: SpeechSynthesisRequest,
    _current_user: User = Depends(get_current_user),
    service: SpeechService = Depends(get_speech_service_for_request),
) -> Response:
    """Return transient generated speech audio without persistence."""
    try:
        result = await service.synthesize(
            text=request.text,
            voice=request.voice,
            response_format=request.response_format,
        )
    except Exception as exc:
        raise speech_http_error(exc) from None

    return speech_audio_response(
        result,
        filename_stem="waffleberry-speech",
    )
