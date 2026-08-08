"""Provider-neutral structured output contract for memory extraction."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.memory import MemoryType
from app.schemas.memory import (
    MEMORY_CATEGORIES,
    MemoryDetails,
    MemoryParticipantCreate,
)


class ExtractionEvidence(BaseModel):
    """One exact excerpt cited from an eligible source message."""

    source_message_id: int = Field(..., gt=0)
    excerpt: str = Field(..., min_length=1, max_length=600)
    model_config = ConfigDict(extra="forbid")

    @field_validator("excerpt", mode="before")
    @classmethod
    def normalize_excerpt(cls, value):
        """Reject blank excerpts without changing quoted source text."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("Evidence excerpts must not be blank.")
        return value


class ExtractedMemory(BaseModel):
    """Untrusted model output before server-built provenance is attached."""

    memory_type: MemoryType
    category: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=255)
    summary: str = Field(..., min_length=1, max_length=2000)
    details: MemoryDetails = Field(default_factory=MemoryDetails)
    emotional_significance: str | None = None
    importance: int = Field(..., ge=1, le=5)
    extraction_confidence: Decimal = Field(
        ...,
        ge=Decimal("0"),
        le=Decimal("1"),
        max_digits=4,
        decimal_places=3,
    )
    uncertainty_note: str | None = None
    participants: list[MemoryParticipantCreate] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=30)
    evidence: list[ExtractionEvidence] = Field(
        ...,
        min_length=1,
        max_length=20,
    )
    model_config = ConfigDict(extra="forbid")

    @field_validator("category", "title", "summary", mode="before")
    @classmethod
    def normalize_required_text(cls, value):
        """Normalize model-produced required text."""
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "emotional_significance",
        "uncertainty_note",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value):
        """Normalize optional text and collapse blank values."""
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("category")
    @classmethod
    def validate_category(cls, value):
        """Keep extraction aligned with the persisted category registry."""
        if value not in MEMORY_CATEGORIES:
            raise ValueError("Unsupported memory category.")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values):
        """Normalize, bound, and deduplicate model-produced tags."""
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            label = value.strip()
            key = label.casefold()
            if not label:
                raise ValueError("Memory tags must not be blank.")
            if len(label) > 80:
                raise ValueError("Memory tags must be at most 80 characters.")
            if key not in seen:
                normalized.append(label)
                seen.add(key)
        return normalized


class MemoryExtractionResult(BaseModel):
    """Complete structured output from one extraction request."""

    memories: list[ExtractedMemory] = Field(
        default_factory=list,
        max_length=50,
    )
    model_config = ConfigDict(extra="forbid")
