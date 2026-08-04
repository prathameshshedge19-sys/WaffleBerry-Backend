"""Authenticated transient audio transcription endpoint."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.dependencies.ai import get_transcription_service
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.audio import AudioTranscriptionResponse
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
