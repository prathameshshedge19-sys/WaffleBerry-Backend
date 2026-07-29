"""Structured, non-persistent outcomes from memory validation."""

import enum
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.memory import MemoryCandidateCreate


class MemoryValidationStatus(str, enum.Enum):
    """Deterministic classification assigned to one candidate."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    POSSIBLE_ENRICHMENT = "possible_enrichment"
    CONTRADICTION = "contradiction"
    INVALID = "invalid"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class MemoryValidationAction(str, enum.Enum):
    """Non-binding recommendation for a later persistence/review phase."""

    ACCEPT_CANDIDATE = "accept_candidate"
    DO_NOT_PERSIST = "do_not_persist"
    REVIEW_LINK = "review_link"
    REVIEW_ENRICHMENT = "review_enrichment"
    REVIEW_CONTRADICTION = "review_contradiction"
    REJECT_CANDIDATE = "reject_candidate"
    REQUEST_MORE_INFORMATION = "request_more_information"


class MemoryValidationIssue(BaseModel):
    """One safe, human-readable validation finding."""

    code: str = Field(..., min_length=1, max_length=80)
    message: str = Field(..., min_length=1, max_length=500)
    provenance_index: int | None = Field(default=None, ge=0)

    model_config = ConfigDict(extra="forbid")


class MemoryValidationResult(BaseModel):
    """Complete validation result without any persistence side effect."""

    status: MemoryValidationStatus
    recommended_action: MemoryValidationAction
    explanation: str = Field(..., min_length=1, max_length=1000)
    validation_confidence: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("1"),
        max_digits=4,
        decimal_places=3,
    )
    normalized_candidate: MemoryCandidateCreate | None = None
    related_memory_ids: list[int] = Field(default_factory=list)
    issues: list[MemoryValidationIssue] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")
