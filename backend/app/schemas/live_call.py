"""Public contracts for ephemeral Live Call sessions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LiveCallSessionCreate(BaseModel):
    legacy_id: int = Field(gt=0)
    conversation_id: int | None = Field(default=None, gt=0)
    engine: Literal["auto", "realtime", "cascade"] = "auto"


class LiveCallSessionResponse(BaseModel):
    session_id: str
    conversation_id: int
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
    available_seconds: int | None = Field(default=None, ge=0)
    quota_plan: str
    quota_resets_at: datetime | None = None


class LiveCallSessionEndResponse(BaseModel):
    session_id: str
    state: Literal["ended"] = "ended"


class LiveCallOperationalEvent(BaseModel):
    """Privacy-safe, bounded client observations for one authenticated call."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["call_started", "call_ended"]
    outcome: Literal[
        "started", "startup_failed", "completed_normally", "transport_failed",
        "provider_failed", "session_expired", "user_ended",
    ]
    failure_category: Literal[
        "none", "session_creation", "bootstrap", "microphone", "peer_connection",
        "sdp_exchange", "data_channel", "remote_audio", "external_renderer",
        "provider_rate_limited", "provider_quota_exhausted",
        "provider_transient_failure", "provider_unknown_failure",
        "session_expired", "transport", "unknown",
    ] = "none"
    duration_ms: int = Field(default=0, ge=0, le=86_400_000)
    turn_started_count: int = Field(default=0, ge=0, le=10_000)
    turn_completed_count: int = Field(default=0, ge=0, le=10_000)
    turn_failed_count: int = Field(default=0, ge=0, le=10_000)
    turn_recovered_count: int = Field(default=0, ge=0, le=10_000)
    recovery_count: int = Field(default=0, ge=0, le=1_000)
    response_failure_count: int = Field(default=0, ge=0, le=10_000)
    external_tts_failure_count: int = Field(default=0, ge=0, le=10_000)
    memory_route_count: int = Field(default=0, ge=0, le=10_000)
    memory_supported_count: int = Field(default=0, ge=0, le=10_000)
    memory_unsupported_count: int = Field(default=0, ge=0, le=10_000)
    memory_error_count: int = Field(default=0, ge=0, le=10_000)
    memory_timeout_count: int = Field(default=0, ge=0, le=10_000)
    startup_phase: Literal[
        "controller_created", "microphone_start", "microphone_ready", "session_creating",
        "session_created", "bootstrap_start", "bootstrap_received", "peer_created",
        "local_track_added", "offer_created", "local_description_set", "sdp_request_start",
        "sdp_response_received", "remote_description_set", "data_channel_wait",
        "data_channel_open", "remote_track_received", "greeting_requested", "ready",
        "startup_failed", "cleanup",
    ] | None = None
    failure_code: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    peer_state: Literal[
        "new", "connecting", "connected", "disconnected", "failed", "closed", "unavailable"
    ] | None = None
    data_channel_state: Literal[
        "connecting", "open", "closing", "closed", "unavailable"
    ] | None = None


class LiveCallMemoryTurn(BaseModel):
    """One final committed user transcription; ownership comes from the session."""

    model_config = ConfigDict(extra="forbid")
    turn_id: int = Field(gt=0, le=10_000)
    text: str = Field(min_length=1, max_length=4000)


class RealtimeBootstrapResponse(BaseModel):
    client_secret: str
    expires_at: int | None = None
    model: str
    voice: str


class RealtimeToolRequest(BaseModel):
    turn_id: int = Field(gt=0, le=10_000)
    call_id: str = Field(min_length=1, max_length=200)
    name: Literal["get_legacy_identity_context", "retrieve_legacy_memory_context"]
    arguments: dict = Field(default_factory=dict)


class RealtimeToolResponse(BaseModel):
    call_id: str
    result: dict


class RealtimeRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: int = Field(gt=0, le=10_000)
    text: str = Field(min_length=1, max_length=500)


class RealtimeRouteResponse(BaseModel):
    route: Literal["direct", "identity", "memory", "followup"]
    tool_name: Literal[
        "get_legacy_identity_context", "retrieve_legacy_memory_context"
    ] | None = None
    response_language: Literal["english", "marathi", "hindi"] = "english"


class RealtimeAssistantTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: int = Field(gt=0, le=10_000)
    response_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=4000)
    response_owner: Literal["native_realtime", "validated_personal"] = "native_realtime"
    playback_completed: bool = False


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
