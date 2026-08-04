"""Schemas for transient audio transcription and speech synthesis."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from app.config import get_settings


SpeechFormat = Literal["mp3", "wav", "opus", "aac", "flac", "pcm"]


class AudioTranscriptionResponse(BaseModel):
    """Transcript returned without persisting audio or text."""

    text: str


class SpeechOptions(BaseModel):
    """Validated optional overrides shared by speech endpoints."""

    voice: str | None = None
    response_format: SpeechFormat | None = None

    @field_validator("voice")
    @classmethod
    def normalize_voice(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Voice must not be blank when provided.")
        return normalized

    @field_validator("response_format", mode="before")
    @classmethod
    def normalize_format(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class SpeechSynthesisRequest(SpeechOptions):
    """Validated text and optional speech output overrides."""

    text: str

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Text must not be blank.")
        maximum = get_settings().tts_max_text_characters
        if len(normalized) > maximum:
            raise ValueError(f"Text must not exceed {maximum} characters.")
        return normalized


class MessageSpeechRequest(SpeechOptions):
    """Speech options for immutable stored assistant content."""

    model_config = ConfigDict(extra="forbid")
