"""Presentation-only commands. No caller-controlled actor, provider or storage key."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Crop(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    rotation: Literal[0, 90, 180, 270] = 0

    @model_validator(mode="after")
    def in_bounds(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("The selected crop must be within the oriented image.")
        return self


class VersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: UUID
    crop: Crop
    confirmed: Literal[True]
    confirmation_copy_version: Literal["l19-likeness-v1"]
    request_key: UUID
    expected_revision: int = Field(ge=0)


class Activation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: UUID
    expected_revision: int = Field(ge=1)
    bundle_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved: Literal[True]


class Toggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    expected_revision: int = Field(ge=1)
