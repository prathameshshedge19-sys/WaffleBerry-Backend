from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["image", "audio", "video", "document"]
    processing_purpose: Literal["source_review", "visual_reference"] = "source_review"
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=127)
    size_bytes: int = Field(ge=1)
    upload_request_key: UUID


class SourceJobResponse(BaseModel):
    id: str
    kind: str
    state: str
    stage: str
    attempts: int
    next_attempt_at: datetime | None
    last_error_code: str | None


class SourceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    legacy_id: int
    uploader_user_id: int | None
    uploader_name: str | None = None
    kind: str
    original_filename: str
    processing_purpose: Literal["source_review", "visual_reference"] = "source_review"
    mime_type: str
    declared_mime_type: str
    size_bytes: int | None
    declared_size_bytes: int
    sha256: str | None
    state: str
    safety_state: str
    generation: int
    metadata: dict
    created_at: datetime
    updated_at: datetime
    uploaded_at: datetime | None
    processing_started_at: datetime | None
    processing_finished_at: datetime | None
    deleted_at: datetime | None
    purged_at: datetime | None
    last_error_code: str | None
    job: SourceJobResponse | None = None
