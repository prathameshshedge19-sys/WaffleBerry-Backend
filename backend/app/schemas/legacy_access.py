from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class LegacyCodeInput(BaseModel):
    code: str = Field(min_length=6, max_length=32)

    @field_validator("code")
    @classmethod
    def clean_code(cls, value: str) -> str:
        return value.strip()


class LegacyAccessIdentity(BaseModel):
    legacy_id: int
    subject_name: str


class LegacyViewerResponse(BaseModel):
    access_id: int
    user_id: int
    full_name: str
    email: str | None = None
    status: str
    joined_at: datetime


class LegacyAccessPanelResponse(BaseModel):
    legacy_id: int
    code: str | None
    code_hint: str | None
    code_enabled: bool
    viewers: list[LegacyViewerResponse]
