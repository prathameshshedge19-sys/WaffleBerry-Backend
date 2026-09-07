from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class StoryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    scope: str = Field(pattern="^(full_biography|childhood|education|career|family|relationship|place|event|custom)$")
    narrative_perspective: str = Field(pattern="^(legacy_first_person|biography_third_person)$")


class StoryEditChapter(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    narrative_text: str = Field(max_length=12000)


class StoryGenerate(BaseModel):
    request_key: str = Field(min_length=1, max_length=128)


class StoryPublish(BaseModel):
    published: bool


class StoryChapterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    ordinal: int
    narrative_text: str
    generation_status: str
    human_edited: bool
    audit_summary: dict | None
    support: list[dict] = []


class StoryVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    version_number: int
    status: str
    human_edited: bool
    audit_summary: dict | None
    created_at: datetime
    chapters: list[StoryChapterResponse]


class StoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    legacy_id: int
    title: str
    scope: str
    narrative_perspective: str
    visibility: str
    lifecycle_state: str
    staleness_state: str
    staleness_reason: str | None
    current_version_id: str | None
    created_at: datetime
    updated_at: datetime
    current_version: StoryVersionResponse | None = None
