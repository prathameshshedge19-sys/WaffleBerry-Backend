from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LegacyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int
    subject_name: str | None
    relationship_to_owner: str | None
    is_self: bool | None
    setup_status: str
    missing_fields: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    access_role: str = "owner"
    owner_name: str | None = None


class LegacyContextResponse(BaseModel):
    active_legacy_id: int | None
    legacies: list[LegacyResponse]
    owned_legacies: list[LegacyResponse] = Field(default_factory=list)
    collaborations: list[LegacyResponse] = Field(default_factory=list)


class LegacySetupStartResponse(BaseModel):
    legacy: LegacyResponse


class LegacyDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str = Field(min_length=1, max_length=80)
    acknowledge_permanent: Literal[True]
