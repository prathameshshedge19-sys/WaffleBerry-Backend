"""L18 narrative artifacts; never a second canonical-memory authority."""

import enum
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StoryScope(str, enum.Enum):
    FULL_BIOGRAPHY = "full_biography"
    CHILDHOOD = "childhood"
    EDUCATION = "education"
    CAREER = "career"
    FAMILY = "family"
    RELATIONSHIP = "relationship"
    PLACE = "place"
    EVENT = "event"
    CUSTOM = "custom"


class StoryPerspective(str, enum.Enum):
    LEGACY_FIRST_PERSON = "legacy_first_person"
    BIOGRAPHY_THIRD_PERSON = "biography_third_person"


class StoryVisibility(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class StoryLifecycle(str, enum.Enum):
    ACTIVE = "active"
    DELETED = "deleted"


class StoryStaleness(str, enum.Enum):
    CURRENT = "current"
    STALE = "stale"
    NEEDS_REVIEW = "needs_review"


class StoryVersionStatus(str, enum.Enum):
    DRAFT = "draft"
    GENERATING = "generating"
    AUDIT_FAILED = "audit_failed"
    READY = "ready"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class StorySupportKind(str, enum.Enum):
    MEMORY = "memory"
    TIMELINE_EVENT = "timeline_event"
    SOURCE_EVIDENCE = "source_evidence"


class StorySupportState(str, enum.Enum):
    AVAILABLE = "available"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    REMOVED = "removed"


class Story(Base):
    __tablename__ = "stories"
    __table_args__ = (
        UniqueConstraint("legacy_id", "id", name="uq_stories_legacy_id_id"),
        CheckConstraint("scope IN ('full_biography','childhood','education','career','family','relationship','place','event','custom')", name="ck_stories_scope"),
        CheckConstraint("narrative_perspective IN ('legacy_first_person','biography_third_person')", name="ck_stories_perspective"),
        CheckConstraint("visibility IN ('draft','published','archived')", name="ck_stories_visibility"),
        CheckConstraint("lifecycle_state IN ('active','deleted')", name="ck_stories_lifecycle"),
        CheckConstraint("staleness_state IN ('current','stale','needs_review')", name="ck_stories_staleness"),
        CheckConstraint("length(trim(title)) > 0 AND length(title) <= 255", name="ck_stories_title_bound"),
        Index("ix_stories_legacy_visibility", "legacy_id", "visibility", "updated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    narrative_perspective: Mapped[str] = mapped_column(String(32), nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default=StoryVisibility.DRAFT.value, server_default="draft")
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default=StoryLifecycle.ACTIVE.value, server_default="active")
    current_version_id: Mapped[str | None] = mapped_column(String(36))
    staleness_state: Mapped[str] = mapped_column(String(16), nullable=False, default=StoryStaleness.CURRENT.value, server_default="current")
    staleness_reason: Mapped[str | None] = mapped_column(String(80))
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class StoryVersion(Base):
    __tablename__ = "story_versions"
    __table_args__ = (
        UniqueConstraint("legacy_id", "id", name="uq_story_versions_legacy_id_id"),
        UniqueConstraint("legacy_id", "story_id", "version_number", name="uq_story_versions_number"),
        UniqueConstraint("legacy_id", "story_id", "generation_request_key", name="uq_story_versions_request"),
        CheckConstraint("status IN ('draft','generating','audit_failed','ready','accepted','superseded','failed')", name="ck_story_versions_status"),
        CheckConstraint("version_number >= 1", name="ck_story_versions_number"),
        CheckConstraint("generation_attempts >= 0 AND generation_attempts <= 3", name="ck_story_versions_attempts"),
        ForeignKeyConstraint(("legacy_id", "story_id"), ("stories.legacy_id", "stories.id"), ondelete="CASCADE", name="fk_story_versions_story_scope"),
        Index("ix_story_versions_generation", "legacy_id", "status", "updated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    story_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=StoryVersionStatus.DRAFT.value, server_default="draft")
    generation_request_key: Mapped[str | None] = mapped_column(String(128))
    provider_model: Mapped[str | None] = mapped_column(String(120))
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False, default="l18-grounded-v1", server_default="l18-grounded-v1")
    input_snapshot: Mapped[dict | None] = mapped_column(JSON)
    audit_summary: Mapped[dict | None] = mapped_column(JSON)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    generation_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    human_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class StoryChapter(Base):
    __tablename__ = "story_chapters"
    __table_args__ = (
        UniqueConstraint("legacy_id", "id", name="uq_story_chapters_legacy_id_id"),
        UniqueConstraint("legacy_id", "story_version_id", "ordinal", name="uq_story_chapters_ordinal"),
        CheckConstraint("length(trim(title)) > 0 AND length(title) <= 255", name="ck_story_chapters_title_bound"),
        CheckConstraint("length(narrative_text) <= 12000", name="ck_story_chapters_text_bound"),
        CheckConstraint("generation_status IN ('draft','generating','audit_failed','ready','accepted','superseded','failed')", name="ck_story_chapters_status"),
        ForeignKeyConstraint(("legacy_id", "story_version_id"), ("story_versions.legacy_id", "story_versions.id"), ondelete="CASCADE", name="fk_story_chapters_version_scope"),
        Index("ix_story_chapters_version_order", "legacy_id", "story_version_id", "ordinal"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    story_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    narrative_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    generation_status: Mapped[str] = mapped_column(String(16), nullable=False, default=StoryVersionStatus.DRAFT.value, server_default="draft")
    human_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    audit_summary: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class StorySupportLink(Base):
    __tablename__ = "story_support_links"
    __table_args__ = (
        ForeignKeyConstraint(("legacy_id", "story_version_id"), ("story_versions.legacy_id", "story_versions.id"), ondelete="CASCADE", name="fk_story_support_version_scope"),
        ForeignKeyConstraint(("legacy_id", "chapter_id"), ("story_chapters.legacy_id", "story_chapters.id"), ondelete="CASCADE", name="fk_story_support_chapter_scope"),
        ForeignKeyConstraint(("legacy_id", "memory_id"), ("memories.legacy_id", "memories.id"), ondelete="CASCADE", name="fk_story_support_memory_scope"),
        ForeignKeyConstraint(("legacy_id", "life_event_id"), ("life_events.legacy_id", "life_events.id"), ondelete="CASCADE", name="fk_story_support_event_scope"),
        ForeignKeyConstraint(("legacy_id", "evidence_id"), ("source_evidence.legacy_id", "source_evidence.id"), ondelete="RESTRICT", name="fk_story_support_evidence_scope"),
        CheckConstraint("support_kind IN ('memory','timeline_event','source_evidence')", name="ck_story_support_kind"),
        CheckConstraint("support_state IN ('available','stale','unavailable','removed')", name="ck_story_support_state"),
        CheckConstraint("(CASE WHEN memory_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN life_event_id IS NOT NULL THEN 1 ELSE 0 END) + (CASE WHEN evidence_id IS NOT NULL THEN 1 ELSE 0 END) = 1", name="ck_story_support_one_target"),
        UniqueConstraint("legacy_id", "chapter_id", "support_kind", "memory_id", "life_event_id", "evidence_id", name="uq_story_support_target"),
        Index("ix_story_support_target", "legacy_id", "memory_id", "life_event_id", "evidence_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    story_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(36), nullable=False)
    support_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    memory_id: Mapped[int | None] = mapped_column(Integer)
    life_event_id: Mapped[str | None] = mapped_column(String(36))
    evidence_id: Mapped[str | None] = mapped_column(String(36))
    support_state: Mapped[str] = mapped_column(String(16), nullable=False, default=StorySupportState.AVAILABLE.value, server_default="available")
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
