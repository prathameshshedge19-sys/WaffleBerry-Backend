from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VisitorProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    legacy_id: int
    viewer_user_id: int
    preferred_name: str | None
    claimed_relationship: str | None
    matched_entity_id: int | None
    relationship_status: str
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime


class VisitorProfileState(BaseModel):
    profile: VisitorProfileResponse | None
    greeting: str


class VisitorProfileUpdate(BaseModel):
    preferred_name: str = Field(min_length=1, max_length=255)
    claimed_relationship: str | None = Field(default=None, max_length=80)

    @field_validator("preferred_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split()).strip(" ,.-")
        if not value:
            raise ValueError("Preferred name must not be blank.")
        return value

    @field_validator("claimed_relationship")
    @classmethod
    def clean_relationship(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split()).strip(" ,.-").casefold() or None
