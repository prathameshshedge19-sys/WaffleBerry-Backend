"""Public contracts for ephemeral Live Call sessions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LiveCallSessionCreate(BaseModel):
    legacy_id: int = Field(gt=0)
    engine: Literal["auto", "realtime", "cascade"] = "auto"


class LiveCallSessionResponse(BaseModel):
    session_id: str
    transport_token: str
    transport: Literal["websocket", "webrtc"] = "websocket"
    engine: Literal["cascade", "realtime"] = "cascade"
    engine_reason: Literal[
        "none", "feature_flag_disabled", "voice_not_realtime_capable",
        "explicit_cascade_selection", "external_realtime_disabled",
    ] = "none"
    realtime_strict: bool = False
    realtime_capable: bool = False
    speech_renderer: Literal[
        "realtime_native", "external_streaming_tts", "external_nonstreaming_tts", "cascade_legacy"
    ] = "cascade_legacy"
    event_version: Literal[1] = 1
    legacy_name: str
    relationship: str
    effective_voice: str
    base_delivery_profile: str = "identity_neutral_v1"
    state: str
    conversation_style: Literal["natural", "gentle", "expressive"] = "natural"
    response_length: Literal["short", "balanced", "detailed"] = "balanced"
    expires_at: datetime


class LiveCallSessionEndResponse(BaseModel):
    session_id: str
    state: Literal["ended"] = "ended"


class RealtimeBootstrapResponse(BaseModel):
    client_secret: str
    expires_at: int | None = None
    model: str
    voice: str


class RealtimeToolRequest(BaseModel):
    call_id: str = Field(min_length=1, max_length=200)
    name: Literal["get_legacy_identity_context", "retrieve_legacy_memory_context"]
    arguments: dict = Field(default_factory=dict)


class RealtimeToolResponse(BaseModel):
    call_id: str
    result: dict


class RealtimeSpeechRequest(BaseModel):
    response_id: str = Field(min_length=1, max_length=200)
    generation_id: str = Field(min_length=1, max_length=200)
    user_input_turn_id: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=500)


class RealtimeSpeechResponse(BaseModel):
    response_id: str
    generation_id: str
    audio: str
    content_type: str
