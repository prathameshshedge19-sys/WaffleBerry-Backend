from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.memory import MemoryOperation, MemoryStatus
from app.services.memory import MEMORY_CATEGORIES


class MemoryEntityResponse(BaseModel):
    name: str
    entity_type: str
    role: str
    aliases: list[str]


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    legacy_id: int
    canonical_text: str
    category: str
    subject_reference: str | None
    source_conversation_id: int | None
    source_message_id: int | None
    contributor_user_id: int | None
    contributor_name: str | None
    last_contributor_user_id: int | None
    last_contributor_name: str | None
    source_language: str
    source_excerpt: str
    confidence: float
    status: MemoryStatus
    operation_type: MemoryOperation
    explicit_save: bool
    superseded_by_memory_id: int | None
    story_key: str | None
    created_at: datetime
    updated_at: datetime
    entities: list[MemoryEntityResponse] = Field(default_factory=list)


class MemoryUpdate(BaseModel):
    canonical_text: str = Field(min_length=3, max_length=1200)
    category: str | None = None

    @field_validator("canonical_text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = " ".join(value.split()).strip()
        if len(value) < 3:
            raise ValueError("Memory text is too short.")
        return value

    @field_validator("category")
    @classmethod
    def valid_category(cls, value: str | None) -> str | None:
        if value is not None and value not in MEMORY_CATEGORIES:
            raise ValueError("Unsupported memory category.")
        return value


class MemoryRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    memory_id: int
    previous_text: str | None
    new_text: str | None
    change_type: str
    source: str
    changed_by_user_id: int | None
    source_conversation_id: int | None
    source_message_id: int | None
    changed_at: datetime
