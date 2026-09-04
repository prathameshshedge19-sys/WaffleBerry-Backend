from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class CollaboratorCodeInput(BaseModel):
    code: str = Field(min_length=6, max_length=32)

    @field_validator("code")
    @classmethod
    def clean_code(cls, value: str) -> str:
        return value.strip()


class CollaborationPreview(BaseModel):
    legacy_id: int
    subject_name: str
    owner_name: str
    access_role: str | None = None


class CollaboratorMemberResponse(BaseModel):
    membership_id: int
    user_id: int
    full_name: str
    email: str
    status: str
    joined_at: datetime


class CollaborationPanelResponse(BaseModel):
    legacy_id: int
    code: str | None
    code_hint: str | None
    code_enabled: bool
    collaborators: list[CollaboratorMemberResponse]


class CollaborationJoinResponse(BaseModel):
    legacy_id: int
    subject_name: str
    owner_name: str
    access_role: str
