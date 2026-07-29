"""Validation contracts for legacy and Memory Engine persistence."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.memory import (
    LegacyStatus,
    MemoryReviewStatus,
    MemoryExtractionRunStatus,
    MemoryType,
    StoryMessageRole,
    StorySessionStatus,
)


MEMORY_CATEGORIES = frozenset(
    {
        "personal_detail",
        "relationship",
        "place",
        "life_event",
        "preference",
        "tradition",
        "habit",
        "value",
        "achievement",
        "challenge",
        "lesson",
        "expression",
        "story",
    }
)

MemorySourceType = Literal[
    "conversation",
    "story_session",
    "voice",
    "photo",
    "video",
    "document",
    "manual",
]


def _strip_required(value: object) -> object:
    """Normalize required user-facing text before field validation."""
    if isinstance(value, str):
        return value.strip()
    return value


class LegacyCreate(BaseModel):
    """Fields required to persist an owner-scoped legacy."""

    display_name: str = Field(..., min_length=1, max_length=255)
    relationship: str = Field(..., min_length=1, max_length=100)
    client_correlation_id: str | None = Field(
        default=None, min_length=8, max_length=100
    )

    _normalize_display_name = field_validator(
        "display_name",
        mode="before",
    )(_strip_required)
    _normalize_relationship = field_validator(
        "relationship",
        mode="before",
    )(_strip_required)


class StorySessionCreate(BaseModel):
    """Fields required to begin a persisted Guided Story session."""

    chapter_key: str = Field(..., min_length=1, max_length=120)
    title: str | None = Field(default=None, min_length=1, max_length=255)

    _normalize_chapter = field_validator(
        "chapter_key",
        mode="before",
    )(_strip_required)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value):
        """Normalize optional titles while rejecting supplied blanks."""
        return _strip_required(value)


class LegacyResponse(BaseModel):
    legacy_id: int
    display_name: str
    relationship: str
    client_correlation_id: str | None
    status: LegacyStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StorySessionResponse(BaseModel):
    story_session_id: int
    legacy_id: int
    chapter_key: str
    title: str | None
    status: StorySessionStatus
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class PersistedStoryStreamRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=12000)
    client_message_id: str = Field(..., min_length=8, max_length=100)

    @field_validator("content", mode="before")
    @classmethod
    def normalize_stream_content(cls, value):
        return _strip_required(value)


class ExtractionRunResponse(BaseModel):
    extraction_run_id: int
    legacy_id: int
    story_session_id: int
    message_boundary: int
    trigger_type: str
    status: MemoryExtractionRunStatus
    attempt_count: int
    candidate_count: int | None
    memories_created: int | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StorySessionCompletionResponse(BaseModel):
    story_session: StorySessionResponse
    extraction_run: ExtractionRunResponse


class StoryMessageCreate(BaseModel):
    """Application-visible content appended to a Story Session."""

    role: StoryMessageRole
    content: str = Field(..., min_length=1, max_length=12000)

    _normalize_content = field_validator(
        "content",
        mode="before",
    )(_strip_required)


class TemporalReference(BaseModel):
    """A partial, approximate, or exact source-grounded time reference."""

    text: str = Field(..., min_length=1, max_length=255)
    start_date: str | None = Field(default=None, max_length=32)
    end_date: str | None = Field(default=None, max_length=32)
    precision: Literal[
        "day",
        "month",
        "season",
        "year",
        "decade",
        "range",
        "unknown",
    ] = "unknown"
    is_approximate: bool = False
    certainty: Literal[
        "stated",
        "approximate",
        "uncertain",
        "disputed",
    ] = "stated"

    _normalize_text = field_validator("text", mode="before")(_strip_required)


class PlaceReference(BaseModel):
    """A source-grounded place with optional uncertainty."""

    name: str = Field(..., min_length=1, max_length=255)
    region: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, max_length=255)
    certainty: Literal[
        "stated",
        "approximate",
        "uncertain",
        "disputed",
        "possible",
    ] = "stated"

    _normalize_name = field_validator("name", mode="before")(_strip_required)


class MemoryDetails(BaseModel):
    """Validated extensible details for non-universal memory attributes."""

    temporal_references: list[TemporalReference] = Field(default_factory=list)
    places: list[PlaceReference] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class MemoryParticipantCreate(BaseModel):
    """A person or relationship explicitly grounded in source material."""

    name: str = Field(..., min_length=1, max_length=255)
    relationship: str | None = Field(default=None, max_length=100)
    role: Literal["subject", "witness", "mentioned_person"] | None = None

    _normalize_name = field_validator("name", mode="before")(_strip_required)


class MemoryProvenanceCreate(BaseModel):
    """One minimal source reference supporting a candidate memory."""

    source_type: MemorySourceType
    conversation_id: int | None = Field(default=None, gt=0)
    message_id: int | None = Field(default=None, gt=0)
    story_session_id: int | None = Field(default=None, gt=0)
    story_message_id: int | None = Field(default=None, gt=0)
    source_locator: dict[str, Any] | None = None
    excerpt: str | None = Field(default=None, min_length=1)
    speaker: str | None = Field(default=None, max_length=80)
    chapter: str | None = Field(default=None, max_length=120)
    extracted_at: datetime | None = None
    extractor_version: str | None = Field(default=None, max_length=100)

    @field_validator("excerpt", "speaker", "chapter", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        """Normalize optional provenance labels and excerpts."""
        return _strip_required(value)

    @model_validator(mode="after")
    def validate_source_shape(self):
        """Require coherent identifiers for each currently supported source."""
        if self.source_type == "conversation":
            if self.conversation_id is None or self.message_id is None:
                raise ValueError(
                    "Conversation provenance requires conversation_id "
                    "and message_id."
                )
        elif self.source_type == "story_session":
            if (
                self.story_session_id is None
                or self.story_message_id is None
            ):
                raise ValueError(
                    "Story provenance requires story_session_id "
                    "and story_message_id."
                )
        elif self.source_type == "manual":
            if any(
                value is not None
                for value in (
                    self.conversation_id,
                    self.message_id,
                    self.story_session_id,
                    self.story_message_id,
                )
            ):
                raise ValueError(
                    "Manual provenance must not reference conversation "
                    "or story message identifiers."
                )
        elif self.source_locator is None:
            raise ValueError(
                "Future media and document sources require source_locator."
            )
        return self


class MemoryCandidateCreate(BaseModel):
    """A structured candidate plus all source-grounded supporting data."""

    memory_type: MemoryType
    category: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=255)
    summary: str = Field(..., min_length=1)
    details: MemoryDetails | None = None
    emotional_significance: str | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    extraction_confidence: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("1"),
        max_digits=4,
        decimal_places=3,
    )
    uncertainty_note: str | None = None
    contradiction_group_id: int | None = Field(default=None, gt=0)
    superseded_by_memory_id: int | None = Field(default=None, gt=0)
    participants: list[MemoryParticipantCreate] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list, max_length=30)
    provenance: list[MemoryProvenanceCreate] = Field(
        ...,
        min_length=1,
        max_length=100,
    )

    @field_validator("category", "title", "summary", mode="before")
    @classmethod
    def normalize_required_text(cls, value):
        """Normalize required candidate text."""
        return _strip_required(value)

    @field_validator(
        "emotional_significance",
        "uncertainty_note",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value):
        """Normalize optional candidate text."""
        return _strip_required(value)

    @field_validator("category")
    @classmethod
    def validate_category(cls, value):
        """Reject extractor categories outside the versioned registry."""
        if value not in MEMORY_CATEGORIES:
            raise ValueError("Unsupported memory category.")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values):
        """Normalize and deduplicate legacy-scoped tag labels."""
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


class MemoryReviewUpdate(BaseModel):
    """An explicit human review transition."""

    review_status: MemoryReviewStatus


class MemoryResponse(BaseModel):
    """Minimal ORM-compatible memory response foundation."""

    memory_id: int
    legacy_id: int
    memory_type: MemoryType
    category: str
    title: str
    summary: str
    review_status: MemoryReviewStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryReviewParticipant(BaseModel):
    name: str
    relationship: str | None = None
    role: str | None = None


class MemoryReviewProvenance(BaseModel):
    source_type: MemorySourceType
    excerpt: str | None = None
    speaker: str | None = None
    chapter: str | None = None
    captured_at: datetime
    conversation_id: int | None = None
    story_session_id: int | None = None
    story_session_title: str | None = None


class RelatedMemoryReview(BaseModel):
    memory_id: int
    title: str
    summary: str
    review_status: MemoryReviewStatus
    relationship: Literal["conflicting", "possible_enrichment"]
    provenance: list[MemoryReviewProvenance] = Field(default_factory=list)


class MemoryReviewResponse(BaseModel):
    """Safe reviewer projection; internal fingerprints are deliberately absent."""

    memory_id: int
    memory_type: MemoryType
    category: str
    title: str
    summary: str
    details: MemoryDetails | None = None
    emotional_significance: str | None = None
    importance: int | None = None
    extraction_confidence: Decimal | None = None
    uncertainty_note: str | None = None
    review_status: MemoryReviewStatus
    created_at: datetime
    updated_at: datetime
    participants: list[MemoryReviewParticipant] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    provenance: list[MemoryReviewProvenance] = Field(default_factory=list)
    related_memories: list[RelatedMemoryReview] = Field(default_factory=list)
    has_contradiction: bool = False
    has_possible_enrichment: bool = False


class MemoryReviewListResponse(BaseModel):
    items: list[MemoryReviewResponse]
    total: int = Field(..., ge=0)
    offset: int = Field(..., ge=0)
    limit: int = Field(..., ge=1, le=100)


class MemoryReviewActionRequest(BaseModel):
    expected_updated_at: datetime


class MemoryReviewEditRequest(BaseModel):
    expected_updated_at: datetime
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = Field(default=None, min_length=1)
    category: str | None = None
    memory_type: MemoryType | None = None
    details: MemoryDetails | None = None
    emotional_significance: str | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    uncertainty_note: str | None = None
    participants: list[MemoryParticipantCreate] | None = None
    tags: list[str] | None = Field(default=None, max_length=30)
    edit_reason: str | None = Field(default=None, max_length=500)

    @field_validator("title", "summary", mode="before")
    @classmethod
    def normalize_edit_required_text(cls, value):
        return _strip_required(value)

    @field_validator(
        "emotional_significance",
        "uncertainty_note",
        "edit_reason",
        mode="before",
    )
    @classmethod
    def normalize_edit_optional_text(cls, value):
        return _strip_required(value)

    @field_validator("category")
    @classmethod
    def validate_edit_category(cls, value):
        if value is not None and value not in MEMORY_CATEGORIES:
            raise ValueError("Unsupported memory category.")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_edit_tags(cls, values):
        if values is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            label = value.strip()
            key = label.casefold()
            if not label or len(label) > 80:
                raise ValueError("Memory tags must be 1–80 characters.")
            if key not in seen:
                normalized.append(label)
                seen.add(key)
        return normalized

    @model_validator(mode="after")
    def require_an_edit(self):
        editable = self.model_dump(
            exclude={"expected_updated_at", "edit_reason"},
            exclude_unset=True,
        )
        if not editable:
            raise ValueError("At least one editable field is required.")
        return self
