from typing import Literal

from pydantic import BaseModel


VoiceName = Literal["marin", "cedar"]


class TranscriptionResponse(BaseModel):
    text: str


class VoicePreferenceResponse(BaseModel):
    voice: VoiceName


class VoicePreferenceUpdate(BaseModel):
    voice: VoiceName


class SpeechRequest(BaseModel):
    message_id: int
    voice: VoiceName | None = None


class VoicePreviewRequest(BaseModel):
    voice: VoiceName
