"""Schemas for transient audio transcription."""

from pydantic import BaseModel


class AudioTranscriptionResponse(BaseModel):
    """Transcript returned without persisting audio or text."""

    text: str
