from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TimelineEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    legacy_id: int
    title: str
    description: str | None
    event_type: str
    date_start: date | None
    date_end: date | None
    date_precision: str
    is_approximate: bool
    date_label: str | None
    sequence_hint: int | None
    place_label: str | None
    confidence: float | None
    origin: str
    review_state: str
    lifecycle_state: str
    conflict: dict | None
    memory_count: int
    source_count: int
    memory_ids: list[int]
    created_at: datetime
    updated_at: datetime


class TimelineEventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    event_type: str = Field(default="other", max_length=40)
    date_start: date | None = None
    date_end: date | None = None
    date_precision: str = Field(default="unknown", max_length=16)
    is_approximate: bool = False
    date_label: str | None = Field(default=None, max_length=255)
    sequence_hint: int | None = None
    place_label: str | None = Field(default=None, max_length=255)
    memory_ids: list[int] = Field(default_factory=list, max_length=20)
    entity_ids: list[int] = Field(default_factory=list, max_length=20)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        return " ".join(value.split()).strip()


class TimelineEventPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    event_type: str | None = Field(default=None, max_length=40)
    date_start: date | None = None
    date_end: date | None = None
    date_precision: str | None = Field(default=None, max_length=16)
    is_approximate: bool | None = None
    date_label: str | None = Field(default=None, max_length=255)
    sequence_hint: int | None = None
    place_label: str | None = Field(default=None, max_length=255)


class TimelineReviewRequest(BaseModel):
    action: str = Field(pattern="^(approve|resolve|archive)$")
    note: str | None = Field(default=None, max_length=1000)


class TimelineEvidenceCreate(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=36)
