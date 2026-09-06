"""LegaRya connection protocol. No provider-native messages or transcript writes."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    conversation_id: int | None = Field(default=None, gt=0)
    legacy_id: int | None = Field(default=None, gt=0)
    mode: Literal["rya", "legacy"] | None = None

    @model_validator(mode="after")
    def scope(self):
        if self.conversation_id is not None:
            if self.legacy_id is not None or self.mode is not None:
                raise ValueError("Existing conversations supply their own scope")
        elif self.legacy_id is None or self.mode is None:
            raise ValueError("New chats require Legacy and mode")
        return self


class Authenticate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["authenticate"]
    ticket: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["ping", "end_call"]


class AudioFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["audio_frame"]
    sequence: int = Field(ge=0, le=2**31 - 1)
    pcm: str = Field(min_length=4, max_length=6400)


class PlaybackControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["interrupt", "playback_started", "playback_progress", "playback_drained"]
    session_id: str = Field(min_length=36, max_length=36)
    generation: int = Field(ge=1)
    turn_id: int = Field(gt=0)
    active_generation_id: str = Field(min_length=36, max_length=36)
    response_id: str | None = Field(default=None, max_length=128)
    sequence: int = Field(default=-1, ge=-1, le=10000)
    samples: int = Field(default=0, ge=0, le=2880000)
    seal: str | None = Field(default=None, min_length=43, max_length=43)
