"""Versioned, privacy-conscious Legacy export contract."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class ExportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LegacyProfileExport(ExportModel):
    legacy_id: int
    display_name: str
    relationship: str
    status: str
    created_at: datetime
    updated_at: datetime


class StoryMessageExport(ExportModel):
    story_message_id: int
    role: str
    content: str
    sequence: int
    created_at: datetime


class StorySessionExport(ExportModel):
    story_session_id: int
    chapter_key: str
    title: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    messages: list[StoryMessageExport]


class MemoryProvenanceExport(ExportModel):
    provenance_id: int
    source_type: str
    conversation_id: int | None
    message_id: int | None
    story_session_id: int | None
    story_message_id: int | None
    source_locator: Any | None
    excerpt: str | None
    speaker: str | None
    chapter: str | None
    extracted_at: datetime


class MemoryParticipantExport(ExportModel):
    name: str
    relationship: str | None
    role: str | None


class MemoryRevisionExport(ExportModel):
    revision_number: int
    previous_content: Any
    edit_reason: str | None
    created_at: datetime


class MemoryRelationshipExport(ExportModel):
    target_memory_id: int
    link_type: str


class MemoryContradictionExport(ExportModel):
    contradiction_group_id: int
    topic: str
    resolution_status: str
    resolution_note: str | None
    resolved_at: datetime | None


class MemoryExport(ExportModel):
    memory_id: int
    memory_type: str
    category: str
    title: str
    summary: str
    details: Any | None
    emotional_significance: str | None
    importance: int | None
    extraction_confidence: Decimal | None
    review_status: str
    uncertainty_note: str | None
    superseded_by_memory_id: int | None
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None
    participants: list[MemoryParticipantExport]
    tags: list[str]
    revisions: list[MemoryRevisionExport]
    provenance: list[MemoryProvenanceExport]
    contradiction: MemoryContradictionExport | None
    relationships: list[MemoryRelationshipExport]


class ExtractionRunExport(ExportModel):
    extraction_run_id: int
    story_session_id: int
    message_boundary: int
    trigger_type: str
    status: str
    candidate_count: int | None
    memories_created: int | None
    failure_category: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ConversationMessageExport(ExportModel):
    message_id: int
    role: str
    content: str
    created_at: datetime


class CompanionGroundingExport(ExportModel):
    assistant_message_id: int
    grounded_memory_ids: list[int]


class ConversationExport(ExportModel):
    conversation_id: int
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[ConversationMessageExport]
    companion_grounding: list[CompanionGroundingExport]


class LegacyExport(ExportModel):
    export_format: str = "waffleberry_legacy"
    export_version: int = 1
    exported_at: datetime
    legacy: LegacyProfileExport
    stories: list[StorySessionExport]
    memories: list[MemoryExport]
    extraction_history: list[ExtractionRunExport]
    conversations: list[ConversationExport]
