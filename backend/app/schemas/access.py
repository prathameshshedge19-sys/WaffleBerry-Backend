from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr


class AccessInviteCreate(BaseModel):
    email: EmailStr
    role: Literal["collaborator", "viewer"]


class AccessInvitePreview(BaseModel):
    legacy_id: int
    subject_name: str
    owner_name: str
    email: EmailStr
    role: Literal["collaborator", "viewer"]
    status: str
    expires_at: datetime


class AccessInviteAccepted(BaseModel):
    legacy_id: int
    subject_name: str
    role: Literal["collaborator", "viewer"]
    status: str


class AccessPanelResponse(BaseModel):
    legacy_id: int
    subject_name: str
    owner: dict
    counts: dict
    collaborators: list[dict]
    viewers: list[dict]
    codes: dict
    pending_invites: list[dict]
    events: list[dict]
