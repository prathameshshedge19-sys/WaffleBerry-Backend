"""Validation and provider orchestration for transient audio transcription."""

import io
import logging
from dataclasses import dataclass
from time import perf_counter

from app.services.ai.provider import AIProvider


logger = logging.getLogger(__name__)

MAX_AUDIO_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_AUDIO_DURATION_SECONDS = 60

ALLOWED_AUDIO_TYPES: dict[str, str] = {
    "audio/webm": "webm",
    "audio/mp4": "mp4",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/flac": "flac",
}


class AudioValidationError(ValueError):
    """A safe, machine-readable audio upload validation failure."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ValidatedAudio:
    """A validated bounded in-memory audio upload."""

    data: bytes
    content_type: str
    filename: str


def normalize_audio_content_type(value: str | None) -> str:
    """Remove codec parameters and normalize a supplied MIME type."""
    return (value or "").split(";", 1)[0].strip().lower()


def validate_audio_upload(data: bytes, content_type: str | None) -> ValidatedAudio:
    """Validate bytes and MIME type without trusting the client filename."""
    if not data:
        raise AudioValidationError(
            "audio_empty",
            "The uploaded audio file is empty.",
            400,
        )
    if len(data) > MAX_AUDIO_UPLOAD_BYTES:
        raise AudioValidationError(
            "audio_too_large",
            "The recording is too large.",
            413,
        )

    normalized_type = normalize_audio_content_type(content_type)
    extension = ALLOWED_AUDIO_TYPES.get(normalized_type)
    if extension is None:
        raise AudioValidationError(
            "audio_format_unsupported",
            "This recording format is not supported.",
            422,
        )

    return ValidatedAudio(
        data=data,
        content_type=normalized_type,
        filename=f"voice-message.{extension}",
    )


class TranscriptionService:
    """Transcribe validated audio without persistence or database access."""

    def __init__(self, provider: AIProvider, *, model: str) -> None:
        normalized_model = model.strip() if isinstance(model, str) else ""
        if not normalized_model:
            raise ValueError("AUDIO_TRANSCRIPTION_MODEL must be configured.")
        self._provider = provider
        self._model = normalized_model

    async def transcribe(self, audio: ValidatedAudio) -> str:
        """Transcribe one validated in-memory upload, then release it."""
        started_at = perf_counter()
        stream = io.BytesIO(audio.data)
        try:
            transcript = await self._provider.transcribe_audio(
                stream,
                filename=audio.filename,
                content_type=audio.content_type,
                model=self._model,
            )
            if not isinstance(transcript, str) or not transcript.strip():
                raise ValueError("Transcription provider returned no text.")
            logger.info(
                "Audio transcription succeeded (mime=%s, bytes=%d, model=%s, latency_ms=%d).",
                audio.content_type,
                len(audio.data),
                self._model,
                round((perf_counter() - started_at) * 1000),
            )
            return transcript.strip()
        except Exception as exc:
            logger.warning(
                "Audio transcription failed (category=%s, mime=%s, bytes=%d, model=%s, latency_ms=%d).",
                getattr(exc, "code", "transcription_failed"),
                audio.content_type,
                len(audio.data),
                self._model,
                round((perf_counter() - started_at) * 1000),
            )
            raise
        finally:
            stream.close()
