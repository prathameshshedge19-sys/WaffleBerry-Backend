"""Strict L21 control-plane commands; media bytes arrive only in L21.3."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class VoiceEnrollmentIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_key: UUID
    expected_revision: int = Field(ge=0)
    language: Literal["mr"] = "mr"
    consented: Literal[True]
    consent_copy_version: Literal["l21-voice-consent-v1"]
    policy_version: Literal["l21-voice-policy-v1"]
    authority_basis: Literal["self", "authorized_representative", "estate_representative"]
    source_category: Literal["self_recording", "authorized_recording"]
    presented_copy_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class VoiceActivation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: UUID
    expected_revision: int = Field(ge=1)
    binding_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved: Literal[True]


class VoiceRevisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
