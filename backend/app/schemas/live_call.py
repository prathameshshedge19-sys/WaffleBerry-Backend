"""Public contracts for ephemeral Live Call sessions."""

from typing import Literal

from pydantic import BaseModel, Field


class LiveCallSessionCreate(BaseModel):
    legacy_id: int = Field(gt=0)


class LiveCallSessionResponse(BaseModel):
    session_id: str
    transport_token: str
    transport: Literal["websocket"] = "websocket"
    event_version: Literal[1] = 1
    legacy_name: str
    relationship: str
    effective_voice: str
    state: str
    conversation_style: Literal["natural", "gentle", "expressive"] = "natural"
    response_length: Literal["short", "balanced", "detailed"] = "balanced"


class LiveCallSessionEndResponse(BaseModel):
    session_id: str
    state: Literal["ended"] = "ended"
