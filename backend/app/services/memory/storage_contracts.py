"""Public, privacy-safe contracts returned by the Memory Storage Pipeline."""

import enum
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.services.memory.validation_contracts import (
    MemoryValidationAction,
    MemoryValidationStatus,
)


class MemoryPipelineSourceType(str, enum.Enum):
    STORY_SESSION = "story_session"
    CONVERSATION = "conversation"
    LIVE_CALL = "live_call"


class MemoryPipelineItem(BaseModel):
    candidate_index: int = Field(..., ge=0)
    validation_status: MemoryValidationStatus
    recommended_action: MemoryValidationAction
    persisted: bool = False
    memory_id: int | None = None
    related_memory_ids: list[int] = Field(default_factory=list)
    contradiction_group_id: int | None = None
    explanation: str
    extraction_confidence: Decimal | None = None
    validation_confidence: Decimal
    error_code: str | None = None

    model_config = ConfigDict(extra="forbid")


class MemoryPipelineErrorDetail(BaseModel):
    code: str
    candidate_index: int | None = None
    message: str

    model_config = ConfigDict(extra="forbid")


class MemoryStorageReport(BaseModel):
    legacy_id: int
    source_type: MemoryPipelineSourceType
    source_id: int
    candidates_extracted: int = 0
    candidates_accepted_for_persistence: int = 0
    memories_created: int = 0
    duplicates_skipped: int = 0
    possible_duplicates_skipped: int = 0
    possible_enrichments_persisted: int = 0
    contradictions_persisted: int = 0
    invalid_candidates_skipped: int = 0
    insufficient_candidates_skipped: int = 0
    validation_status_counts: dict[str, int] = Field(default_factory=dict)
    created_memory_ids: list[int] = Field(default_factory=list)
    items: list[MemoryPipelineItem] = Field(default_factory=list)
    errors: list[MemoryPipelineErrorDetail] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid")
