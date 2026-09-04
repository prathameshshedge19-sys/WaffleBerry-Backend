import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MemoryStatus(str, enum.Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class MemoryOperation(str, enum.Enum):
    NEW = "new"
    ENRICH = "enrich"
    CORRECT = "correct"
    SUPERSEDE = "supersede"
    DELETE = "delete"
    EXPLICIT_SAVE = "explicit_save"
    EDIT = "edit"


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("length(trim(canonical_text)) > 0", name="ck_memories_canonical_text_not_blank"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memories_confidence_range"),
        CheckConstraint("status IN ('active', 'superseded', 'deleted')", name="ck_memories_status"),
        Index("ix_memories_legacy_status", "legacy_id", "status"),
        Index("ix_memories_legacy_category", "legacy_id", "category"),
        Index("ix_memories_legacy_fingerprint", "legacy_id", "normalized_fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False, index=True)
    canonical_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_reference: Mapped[str | None] = mapped_column(String(255))
    source_conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), index=True)
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), index=True)
    contributor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    last_contributor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    source_language: Mapped[str] = mapped_column(String(80), nullable=False)
    source_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=MemoryStatus.ACTIVE.value, server_default=MemoryStatus.ACTIVE.value)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False, default=MemoryOperation.NEW.value, server_default=MemoryOperation.NEW.value)
    explicit_save: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    normalized_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    superseded_by_memory_id: Mapped[int | None] = mapped_column(ForeignKey("memories.id", ondelete="SET NULL"), index=True)
    story_key: Mapped[str | None] = mapped_column(String(120), index=True)
    embedding: Mapped[list[float] | None] = mapped_column(JSON)
    embedding_model: Mapped[str | None] = mapped_column(String(120))
    embedding_version: Mapped[str | None] = mapped_column(String(40))
    embedding_dimensions: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    legacy = relationship("Legacy", back_populates="memories")
    revisions = relationship("MemoryRevision", back_populates="memory", cascade="all, delete-orphan", order_by="MemoryRevision.id")
    entity_links = relationship("MemoryEntityLink", back_populates="memory", cascade="all, delete-orphan")
    superseded_by = relationship("Memory", remote_side=[id], foreign_keys=[superseded_by_memory_id])
    contributor = relationship("User", foreign_keys=[contributor_user_id])
    last_contributor = relationship("User", foreign_keys=[last_contributor_user_id])


class MemoryRevision(Base):
    __tablename__ = "memory_revisions"
    __table_args__ = (Index("ix_memory_revisions_memory_changed", "memory_id", "changed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    memory_id: Mapped[int] = mapped_column(ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True)
    previous_text: Mapped[str | None] = mapped_column(Text)
    new_text: Mapped[str | None] = mapped_column(Text)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    source_conversation_id: Mapped[int | None] = mapped_column(index=True)
    source_message_id: Mapped[int | None] = mapped_column(index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    memory = relationship("Memory", back_populates="revisions")


class MemoryEntity(Base):
    __tablename__ = "memory_entities"
    __table_args__ = (
        UniqueConstraint("legacy_id", "normalized_name", name="uq_memory_entities_legacy_name"),
        Index("ix_memory_entities_legacy_type", "legacy_id", "entity_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    links = relationship("MemoryEntityLink", back_populates="entity", cascade="all, delete-orphan")


class MemoryEntityLink(Base):
    __tablename__ = "memory_entity_links"
    __table_args__ = (UniqueConstraint("memory_id", "entity_id", "role", name="uq_memory_entity_links_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    memory_id: Mapped[int] = mapped_column(ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("memory_entities.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(80), nullable=False, default="mentioned")

    memory = relationship("Memory", back_populates="entity_links")
    entity = relationship("MemoryEntity", back_populates="links")
