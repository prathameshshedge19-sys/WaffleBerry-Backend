"""Persistence models for legacy stories and reviewable memories."""

import enum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship as orm_relationship, validates
from sqlalchemy.sql import func

from app.db import Base


def _enum_column(enum_class: type[enum.Enum], name: str) -> Enum:
    """Create a portable constrained string enum."""
    return Enum(
        enum_class,
        values_callable=lambda values: [value.value for value in values],
        native_enum=False,
        validate_strings=True,
        create_constraint=True,
        name=name,
    )


class LegacyStatus(str, enum.Enum):
    """Lifecycle of a persisted legacy."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class StorySessionStatus(str, enum.Enum):
    """User-controlled progress state for a Guided Story session."""

    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"


class StoryMessageRole(str, enum.Enum):
    """Application-visible speakers in a Guided Story session."""

    USER = "user"
    ASSISTANT = "assistant"


class MemoryExtractionRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MemoryType(str, enum.Enum):
    """Supported forms of structured memory."""

    ATOMIC = "atomic"
    NARRATIVE = "narrative"


class MemoryReviewStatus(str, enum.Enum):
    """Human review lifecycle for a memory."""

    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class Legacy(Base):
    """A person whose contributed life information is being preserved."""

    __tablename__ = "legacies"
    __table_args__ = (
        CheckConstraint(
            "length(trim(display_name)) > 0",
            name="ck_legacies_display_name_not_blank",
        ),
        CheckConstraint(
            "length(trim(relationship)) > 0",
            name="ck_legacies_relationship_not_blank",
        ),
        Index("ix_legacies_owner_status", "owner_user_id", "status"),
        UniqueConstraint(
            "owner_user_id",
            "client_correlation_id",
            name="uq_legacies_owner_client_correlation",
        ),
    )

    legacy_id = Column(Integer, primary_key=True, index=True)
    owner_user_id = Column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    display_name = Column(String(255), nullable=False)
    relationship = Column(String(100), nullable=False)
    client_correlation_id = Column(String(100), nullable=True)
    status = Column(
        _enum_column(LegacyStatus, "legacy_status"),
        nullable=False,
        default=LegacyStatus.ACTIVE,
        server_default=LegacyStatus.ACTIVE.value,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    owner = orm_relationship("User", back_populates="legacies")
    conversations = orm_relationship("Conversation", back_populates="legacy")
    story_sessions = orm_relationship(
        "StorySession",
        back_populates="legacy",
        cascade="all, delete-orphan",
    )
    memories = orm_relationship(
        "Memory",
        back_populates="legacy",
        cascade="all, delete-orphan",
        foreign_keys="Memory.legacy_id",
    )
    contradiction_groups = orm_relationship(
        "MemoryContradictionGroup",
        back_populates="legacy",
        cascade="all, delete-orphan",
    )
    tags = orm_relationship(
        "Tag",
        back_populates="legacy",
        cascade="all, delete-orphan",
    )
    extraction_runs = orm_relationship(
        "MemoryExtractionRun",
        back_populates="legacy",
        cascade="all, delete-orphan",
    )


class StorySession(Base):
    """One persisted Guided Story conversation for one legacy."""

    __tablename__ = "story_sessions"
    __table_args__ = (
        CheckConstraint(
            "length(trim(chapter_key)) > 0",
            name="ck_story_sessions_chapter_key_not_blank",
        ),
        Index(
            "ix_story_sessions_legacy_chapter_status",
            "legacy_id",
            "chapter_key",
            "status",
        ),
    )

    story_session_id = Column(Integer, primary_key=True, index=True)
    legacy_id = Column(
        ForeignKey("legacies.legacy_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_key = Column(String(120), nullable=False)
    title = Column(String(255), nullable=True)
    status = Column(
        _enum_column(StorySessionStatus, "story_session_status"),
        nullable=False,
        default=StorySessionStatus.IN_PROGRESS,
        server_default=StorySessionStatus.IN_PROGRESS.value,
    )
    created_by_user_id = Column(
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    legacy = orm_relationship("Legacy", back_populates="story_sessions")
    created_by = orm_relationship("User")
    messages = orm_relationship(
        "StoryMessage",
        back_populates="story_session",
        cascade="all, delete-orphan",
        order_by="StoryMessage.sequence",
    )
    extraction_runs = orm_relationship(
        "MemoryExtractionRun",
        back_populates="story_session",
        cascade="all, delete-orphan",
    )


class StoryMessage(Base):
    """One application-visible message in a Guided Story session."""

    __tablename__ = "story_messages"
    __table_args__ = (
        UniqueConstraint(
            "story_session_id",
            "sequence",
            name="uq_story_messages_session_sequence",
        ),
        UniqueConstraint(
            "story_session_id",
            "client_message_id",
            name="uq_story_messages_session_client_message",
        ),
        CheckConstraint(
            "sequence > 0",
            name="ck_story_messages_sequence_positive",
        ),
        CheckConstraint(
            "length(trim(content)) > 0",
            name="ck_story_messages_content_not_blank",
        ),
        Index(
            "ix_story_messages_session_sequence",
            "story_session_id",
            "sequence",
        ),
    )

    story_message_id = Column(Integer, primary_key=True, index=True)
    story_session_id = Column(
        ForeignKey("story_sessions.story_session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(
        _enum_column(StoryMessageRole, "story_message_role"),
        nullable=False,
    )
    content = Column(Text, nullable=False)
    client_message_id = Column(String(120), nullable=True)
    sequence = Column(Integer, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    story_session = orm_relationship("StorySession", back_populates="messages")

    @validates("content")
    def validate_content(self, key, value):
        """Reject empty application-visible story messages."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Story message content must not be empty.")
        return value


class MemoryExtractionRun(Base):
    """Durable background extraction state for one Story Message boundary."""

    __tablename__ = "memory_extraction_runs"
    __table_args__ = (
        UniqueConstraint(
            "story_session_id",
            "message_boundary",
            "trigger_type",
            name="uq_extraction_runs_session_boundary_trigger",
        ),
        CheckConstraint(
            "message_boundary > 0",
            name="ck_extraction_runs_boundary_positive",
        ),
        Index(
            "ix_extraction_runs_legacy_status",
            "legacy_id",
            "status",
        ),
    )

    extraction_run_id = Column(Integer, primary_key=True, index=True)
    legacy_id = Column(
        ForeignKey("legacies.legacy_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    story_session_id = Column(
        ForeignKey("story_sessions.story_session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_boundary = Column(Integer, nullable=False)
    trigger_type = Column(String(40), nullable=False)
    status = Column(
        _enum_column(
            MemoryExtractionRunStatus,
            "memory_extraction_run_status",
        ),
        nullable=False,
        default=MemoryExtractionRunStatus.PENDING,
        server_default=MemoryExtractionRunStatus.PENDING.value,
    )
    attempt_count = Column(
        Integer, nullable=False, default=0, server_default="0"
    )
    candidate_count = Column(Integer, nullable=True)
    memories_created = Column(Integer, nullable=True)
    last_error_code = Column(String(80), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    legacy = orm_relationship("Legacy", back_populates="extraction_runs")
    story_session = orm_relationship(
        "StorySession", back_populates="extraction_runs"
    )


class MemoryContradictionGroup(Base):
    """A legacy-scoped set of mutually inconsistent memory accounts."""

    __tablename__ = "memory_contradiction_groups"
    __table_args__ = (
        CheckConstraint(
            "length(trim(topic)) > 0",
            name="ck_memory_contradiction_groups_topic_not_blank",
        ),
    )

    contradiction_group_id = Column(Integer, primary_key=True, index=True)
    legacy_id = Column(
        ForeignKey("legacies.legacy_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    topic = Column(String(255), nullable=False)
    resolution_status = Column(
        String(40),
        nullable=False,
        default="unresolved",
        server_default="unresolved",
    )
    resolution_note = Column(Text, nullable=True)
    resolved_by_user_id = Column(
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    legacy = orm_relationship("Legacy", back_populates="contradiction_groups")
    memories = orm_relationship(
        "Memory",
        back_populates="contradiction_group",
        foreign_keys="Memory.contradiction_group_id",
    )
    resolved_by = orm_relationship("User")


class Memory(Base):
    """A structured, source-grounded and human-reviewable memory."""

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_memories_title_not_blank",
        ),
        CheckConstraint(
            "length(trim(summary)) > 0",
            name="ck_memories_summary_not_blank",
        ),
        CheckConstraint(
            "importance IS NULL OR (importance >= 1 AND importance <= 5)",
            name="ck_memories_importance_range",
        ),
        CheckConstraint(
            "extraction_confidence IS NULL OR "
            "(extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name="ck_memories_extraction_confidence_range",
        ),
        CheckConstraint(
            "superseded_by_memory_id IS NULL OR "
            "superseded_by_memory_id <> memory_id",
            name="ck_memories_not_self_superseded",
        ),
        CheckConstraint(
            "review_status <> 'superseded' OR "
            "superseded_by_memory_id IS NOT NULL",
            name="ck_memories_superseded_requires_replacement",
        ),
        Index(
            "ix_memories_legacy_review_status",
            "legacy_id",
            "review_status",
        ),
        Index(
            "ix_memories_legacy_category_type",
            "legacy_id",
            "category",
            "memory_type",
        ),
        UniqueConstraint(
            "legacy_id",
            "normalized_fingerprint",
            name="uq_memories_legacy_fingerprint",
        ),
    )

    memory_id = Column(Integer, primary_key=True, index=True)
    legacy_id = Column(
        ForeignKey("legacies.legacy_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    memory_type = Column(
        _enum_column(MemoryType, "memory_type"),
        nullable=False,
    )
    category = Column(String(80), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False)
    normalized_fingerprint = Column(String(64), nullable=True)
    details = Column(JSON, nullable=True)
    emotional_significance = Column(Text, nullable=True)
    importance = Column(Integer, nullable=True)
    extraction_confidence = Column(Numeric(4, 3), nullable=True)
    review_status = Column(
        _enum_column(MemoryReviewStatus, "memory_review_status"),
        nullable=False,
        default=MemoryReviewStatus.CANDIDATE,
        server_default=MemoryReviewStatus.CANDIDATE.value,
        index=True,
    )
    uncertainty_note = Column(Text, nullable=True)
    contradiction_group_id = Column(
        ForeignKey(
            "memory_contradiction_groups.contradiction_group_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    superseded_by_memory_id = Column(
        ForeignKey("memories.memory_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by_user_id = Column(
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    embedding = Column(JSON, nullable=True)
    embedding_model = Column(String(120), nullable=True)
    embedding_version = Column(String(40), nullable=True)
    embedding_dimensions = Column(Integer, nullable=True)
    embedded_at = Column(DateTime(timezone=True), nullable=True)

    legacy = orm_relationship(
        "Legacy",
        back_populates="memories",
        foreign_keys=[legacy_id],
    )
    contradiction_group = orm_relationship(
        "MemoryContradictionGroup",
        back_populates="memories",
        foreign_keys=[contradiction_group_id],
    )
    superseded_by = orm_relationship(
        "Memory",
        remote_side=[memory_id],
        foreign_keys=[superseded_by_memory_id],
        back_populates="supersedes",
    )
    supersedes = orm_relationship(
        "Memory",
        foreign_keys=[superseded_by_memory_id],
        back_populates="superseded_by",
    )
    reviewed_by = orm_relationship("User")
    provenance = orm_relationship(
        "MemoryProvenance",
        back_populates="memory",
        cascade="all, delete-orphan",
    )
    revisions = orm_relationship(
        "MemoryRevision",
        back_populates="memory",
        cascade="all, delete-orphan",
        order_by="MemoryRevision.revision_number",
    )
    participants = orm_relationship(
        "MemoryParticipant",
        back_populates="memory",
        cascade="all, delete-orphan",
    )
    tag_links = orm_relationship(
        "MemoryTag",
        back_populates="memory",
        cascade="all, delete-orphan",
    )
    outgoing_links = orm_relationship(
        "MemoryLink",
        foreign_keys="MemoryLink.source_memory_id",
        back_populates="source_memory",
        cascade="all, delete-orphan",
    )
    incoming_links = orm_relationship(
        "MemoryLink",
        foreign_keys="MemoryLink.target_memory_id",
        back_populates="target_memory",
        cascade="all, delete-orphan",
    )


class CompanionMemoryProvenance(Base):
    """Internal record of an approved memory supplied for one reply."""

    __tablename__ = "companion_memory_provenance"
    __table_args__ = (
        UniqueConstraint(
            "assistant_message_id",
            "retrieval_order",
            name="uq_companion_provenance_message_order",
        ),
        CheckConstraint(
            "retrieval_order >= 0",
            name="ck_companion_provenance_order_nonnegative",
        ),
        Index(
            "ix_companion_memory_provenance_memory_id",
            "memory_id",
        ),
    )

    assistant_message_id = Column(
        ForeignKey("messages.message_id", ondelete="CASCADE"),
        primary_key=True,
    )
    memory_id = Column(
        ForeignKey("memories.memory_id", ondelete="CASCADE"),
        primary_key=True,
    )
    retrieval_order = Column(Integer, nullable=False)
    retrieved_at = Column(DateTime(timezone=True), nullable=False)

    assistant_message = orm_relationship("Message")
    memory = orm_relationship("Memory")


class MemoryProvenance(Base):
    """A minimal, traceable source excerpt supporting one memory."""

    __tablename__ = "memory_provenance"
    __table_args__ = (
        CheckConstraint(
            "length(trim(source_type)) > 0",
            name="ck_memory_provenance_source_type_not_blank",
        ),
        CheckConstraint(
            "excerpt IS NULL OR length(trim(excerpt)) > 0",
            name="ck_memory_provenance_excerpt_not_blank",
        ),
        Index(
            "ix_memory_provenance_conversation_source",
            "conversation_id",
            "message_id",
        ),
        Index(
            "ix_memory_provenance_story_source",
            "story_session_id",
            "story_message_id",
        ),
    )

    provenance_id = Column(Integer, primary_key=True, index=True)
    memory_id = Column(
        ForeignKey("memories.memory_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type = Column(String(40), nullable=False, index=True)
    conversation_id = Column(
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    message_id = Column(
        ForeignKey("messages.message_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    story_session_id = Column(
        ForeignKey("story_sessions.story_session_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    story_message_id = Column(
        ForeignKey("story_messages.story_message_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_locator = Column(JSON, nullable=True)
    excerpt = Column(Text, nullable=True)
    speaker = Column(String(80), nullable=True)
    chapter = Column(String(120), nullable=True)
    extracted_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    extractor_version = Column(String(100), nullable=True)

    memory = orm_relationship("Memory", back_populates="provenance")
    conversation = orm_relationship("Conversation")
    message = orm_relationship("Message")
    story_session = orm_relationship("StorySession")
    story_message = orm_relationship("StoryMessage")


class MemoryRevision(Base):
    """Audit snapshot created before editable memory content changes."""

    __tablename__ = "memory_revisions"
    __table_args__ = (
        UniqueConstraint(
            "memory_id",
            "revision_number",
            name="uq_memory_revisions_memory_number",
        ),
        CheckConstraint(
            "revision_number > 0",
            name="ck_memory_revisions_number_positive",
        ),
    )

    memory_revision_id = Column(Integer, primary_key=True, index=True)
    memory_id = Column(
        ForeignKey("memories.memory_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision_number = Column(Integer, nullable=False)
    edited_by_user_id = Column(
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    previous_content = Column(JSON, nullable=False)
    edit_reason = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    memory = orm_relationship("Memory", back_populates="revisions")
    edited_by = orm_relationship("User")


class MemoryParticipant(Base):
    """A source-grounded person or relationship mentioned in a memory."""

    __tablename__ = "memory_participants"
    __table_args__ = (
        CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_memory_participants_name_not_blank",
        ),
    )

    memory_participant_id = Column(Integer, primary_key=True, index=True)
    memory_id = Column(
        ForeignKey("memories.memory_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    relationship = Column(String(100), nullable=True)
    role = Column(String(40), nullable=True)

    memory = orm_relationship("Memory", back_populates="participants")


class Tag(Base):
    """A legacy-scoped organizational memory tag."""

    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint(
            "legacy_id",
            "normalized_name",
            name="uq_tags_legacy_normalized_name",
        ),
        CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_tags_name_not_blank",
        ),
        CheckConstraint(
            "length(trim(normalized_name)) > 0",
            name="ck_tags_normalized_name_not_blank",
        ),
    )

    tag_id = Column(Integer, primary_key=True, index=True)
    legacy_id = Column(
        ForeignKey("legacies.legacy_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(80), nullable=False)
    normalized_name = Column(String(80), nullable=False)

    legacy = orm_relationship("Legacy", back_populates="tags")
    memory_links = orm_relationship(
        "MemoryTag",
        back_populates="tag",
        cascade="all, delete-orphan",
    )


class MemoryTag(Base):
    """Many-to-many association between memories and legacy-scoped tags."""

    __tablename__ = "memory_tags"

    memory_id = Column(
        ForeignKey("memories.memory_id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id = Column(
        ForeignKey("tags.tag_id", ondelete="CASCADE"),
        primary_key=True,
    )

    memory = orm_relationship("Memory", back_populates="tag_links")
    tag = orm_relationship("Tag", back_populates="memory_links")


class MemoryLink(Base):
    """A reviewable relationship between two memories in one legacy."""

    __tablename__ = "memory_links"
    __table_args__ = (
        UniqueConstraint(
            "source_memory_id",
            "target_memory_id",
            "link_type",
            name="uq_memory_links_source_target_type",
        ),
        CheckConstraint(
            "source_memory_id <> target_memory_id",
            name="ck_memory_links_not_self_linked",
        ),
        Index(
            "ix_memory_links_legacy_type",
            "legacy_id",
            "link_type",
        ),
    )

    memory_link_id = Column(Integer, primary_key=True, index=True)
    legacy_id = Column(
        ForeignKey("legacies.legacy_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_memory_id = Column(
        ForeignKey("memories.memory_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_memory_id = Column(
        ForeignKey("memories.memory_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    link_type = Column(String(40), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    source_memory = orm_relationship(
        "Memory",
        foreign_keys=[source_memory_id],
        back_populates="outgoing_links",
    )
    target_memory = orm_relationship(
        "Memory",
        foreign_keys=[target_memory_id],
        back_populates="incoming_links",
    )
    legacy = orm_relationship("Legacy")
