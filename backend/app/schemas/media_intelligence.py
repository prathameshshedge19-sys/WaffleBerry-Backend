"""Strict, noncanonical DTOs for source analysis and owner review."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.memory import MEMORY_CATEGORIES, MemoryEntityCandidate


class SourceEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=3, max_length=32)
    text: str | None = Field(default=None, max_length=4000)
    locator: dict = Field(default_factory=dict)
    language: str | None = Field(default=None, max_length=80)
    confidence: float | None = Field(default=None, ge=0, le=1)
    origin: dict = Field(default_factory=dict)


class SourceCandidateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_text: str = Field(min_length=3, max_length=1200)
    category: str
    confidence: float = Field(ge=0, le=1)
    evidence_indexes: list[Annotated[int, Field(strict=True, ge=0, le=31)]] = Field(min_length=1, max_length=8)
    uncertainty: str | None = Field(default=None, max_length=500)
    source_language: str = Field(default="english", min_length=2, max_length=80)
    entities: list[MemoryEntityCandidate] = Field(default_factory=list, max_length=16)

    @field_validator("canonical_text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return " ".join(value.split()).strip()

    @field_validator("category")
    @classmethod
    def valid_category(cls, value: str) -> str:
        if value not in MEMORY_CATEGORIES:
            raise ValueError("Unsupported memory category.")
        return value


class SourceAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_language: str = Field(min_length=2, max_length=80)
    candidates: list[SourceCandidateProposal] = Field(default_factory=list, max_length=8)
    summary: str | None = Field(default=None, max_length=1000)


class ReviewDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_text: str = Field(min_length=3, max_length=1200)
    category: str | None = None
    expected_version: int = Field(ge=1)

    @field_validator("canonical_text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return " ".join(value.split()).strip()

    @field_validator("category")
    @classmethod
    def valid_category(cls, value: str | None) -> str | None:
        if value is not None and value not in MEMORY_CATEGORIES:
            raise ValueError("Unsupported memory category.")
        return value


class CandidateReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    expected_version: int = Field(ge=1)
    review_request_key: str


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    text: str | None
    locator: dict
    language: str | None
    confidence: float | None
    origin: dict


class CandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    legacy_id: int
    source_id: str
    generation: int
    proposal: dict
    review_draft: dict | None
    version: int
    review_state: str
    reviewed_at: datetime | None
    reviewed_by_user_id: int | None
    review_action: str | None
    canonical_memory_id: int | None
    promotion_outcome: str | None
    evidence: list[EvidenceResponse]
