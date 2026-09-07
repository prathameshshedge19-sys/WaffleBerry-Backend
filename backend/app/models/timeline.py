"""L17 Legacy-scoped chronological organization over canonical memories."""

import enum
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TimelinePrecision(str, enum.Enum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    RANGE = "range"
    LIFE_PERIOD = "life_period"
    UNKNOWN = "unknown"


class TimelineOrigin(str, enum.Enum):
    CANONICAL_STRUCTURED = "canonical_structured"
    HUMAN_CREATED = "human_created"
    SOURCE_SUPPORTED = "source_supported"
    AI_PROPOSED = "ai_proposed"


class TimelineReviewState(str, enum.Enum):
    APPROVED = "approved"
    NEEDS_REVIEW = "needs_review"
    CONFLICT = "conflict"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class TimelineLifecycleState(str, enum.Enum):
    ACTIVE = "active"
    DELETED = "deleted"


class TimelineLinkState(str, enum.Enum):
    ACTIVE = "active"
    STALE = "stale"
    REMOVED = "removed"


class TimelineEvidenceState(str, enum.Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    REMOVED = "removed"


class LifeEvent(Base):
    __tablename__ = "life_events"
    __table_args__ = (
        UniqueConstraint("legacy_id", "id", name="uq_life_events_legacy_id_id"),
        UniqueConstraint("legacy_id", "admission_key", name="uq_life_events_admission_key"),
        CheckConstraint("length(trim(title)) > 0", name="ck_life_events_title_not_blank"),
        CheckConstraint("length(title) <= 255", name="ck_life_events_title_bound"),
        CheckConstraint("description IS NULL OR length(description) <= 2000", name="ck_life_events_description_bound"),
        CheckConstraint("date_precision IN ('day','month','year','range','life_period','unknown')", name="ck_life_events_precision"),
        CheckConstraint("origin IN ('canonical_structured','human_created','source_supported','ai_proposed')", name="ck_life_events_origin"),
        CheckConstraint("review_state IN ('approved','needs_review','conflict','superseded','archived')", name="ck_life_events_review"),
        CheckConstraint("lifecycle_state IN ('active','deleted')", name="ck_life_events_lifecycle"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_life_events_confidence"),
        CheckConstraint("date_end IS NULL OR date_start IS NULL OR date_end >= date_start", name="ck_life_events_date_order"),
        Index("ix_life_events_legacy_sort", "legacy_id", "lifecycle_state", "sort_date", "sequence_hint", "id"),
        Index("ix_life_events_legacy_review", "legacy_id", "review_state", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False)
    admission_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, default="other", server_default="other")
    date_start: Mapped[date | None] = mapped_column(Date)
    date_end: Mapped[date | None] = mapped_column(Date)
    sort_date: Mapped[date | None] = mapped_column(Date)
    date_precision: Mapped[str] = mapped_column(String(16), nullable=False, default=TimelinePrecision.UNKNOWN.value, server_default=TimelinePrecision.UNKNOWN.value)
    is_approximate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    date_label: Mapped[str | None] = mapped_column(String(255))
    sequence_hint: Mapped[int | None] = mapped_column(Integer)
    place_label: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[float | None] = mapped_column()
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default=TimelineOrigin.CANONICAL_STRUCTURED.value, server_default=TimelineOrigin.CANONICAL_STRUCTURED.value)
    review_state: Mapped[str] = mapped_column(String(16), nullable=False, default=TimelineReviewState.APPROVED.value, server_default=TimelineReviewState.APPROVED.value)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default=TimelineLifecycleState.ACTIVE.value, server_default=TimelineLifecycleState.ACTIVE.value)
    conflict_json: Mapped[dict | None] = mapped_column(JSON)
    conflict_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class LifeEventMemory(Base):
    __tablename__ = "life_event_memories"
    __table_args__ = (
        ForeignKeyConstraint(("legacy_id", "event_id"), ("life_events.legacy_id", "life_events.id"), ondelete="CASCADE", name="fk_life_event_memories_event_scope"),
        ForeignKeyConstraint(("legacy_id", "memory_id"), ("memories.legacy_id", "memories.id"), ondelete="CASCADE", name="fk_life_event_memories_memory_scope"),
        UniqueConstraint("legacy_id", "event_id", "memory_id", "link_role", name="uq_life_event_memory_role"),
        Index("ix_life_event_memories_memory", "legacy_id", "memory_id", "link_state"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    memory_id: Mapped[int] = mapped_column(Integer, nullable=False)
    link_role: Mapped[str] = mapped_column(String(32), nullable=False, default="additional_support", server_default="additional_support")
    link_state: Mapped[str] = mapped_column(String(16), nullable=False, default=TimelineLinkState.ACTIVE.value, server_default=TimelineLinkState.ACTIVE.value)
    linked_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    source_memory_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LifeEventEvidence(Base):
    __tablename__ = "life_event_evidence"
    __table_args__ = (
        ForeignKeyConstraint(("legacy_id", "event_id"), ("life_events.legacy_id", "life_events.id"), ondelete="CASCADE", name="fk_life_event_evidence_event_scope"),
        ForeignKeyConstraint(("legacy_id", "evidence_id"), ("source_evidence.legacy_id", "source_evidence.id"), ondelete="RESTRICT", name="fk_life_event_evidence_evidence_scope"),
        UniqueConstraint("legacy_id", "event_id", "evidence_id", name="uq_life_event_evidence"),
        Index("ix_life_event_evidence_evidence", "legacy_id", "evidence_id", "link_state"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(36), nullable=False)
    link_state: Mapped[str] = mapped_column(String(16), nullable=False, default=TimelineEvidenceState.AVAILABLE.value, server_default=TimelineEvidenceState.AVAILABLE.value)
    linked_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class LifeEventEntity(Base):
    __tablename__ = "life_event_entities"
    __table_args__ = (
        ForeignKeyConstraint(("legacy_id", "event_id"), ("life_events.legacy_id", "life_events.id"), ondelete="CASCADE", name="fk_life_event_entities_event_scope"),
        ForeignKeyConstraint(("legacy_id", "entity_id"), ("memory_entities.legacy_id", "memory_entities.id"), ondelete="CASCADE", name="fk_life_event_entities_entity_scope"),
        UniqueConstraint("legacy_id", "event_id", "entity_id", "role", name="uq_life_event_entity_role"),
        Index("ix_life_event_entities_entity", "legacy_id", "entity_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="mentioned", server_default="mentioned")
