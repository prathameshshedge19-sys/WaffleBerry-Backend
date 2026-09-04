from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LegacySetupStatus(str, Enum):
    COLLECTING_IDENTITY = "collecting_identity"
    ACTIVE = "active"
    ARCHIVED = "archived"


class Legacy(Base):
    __tablename__ = "legacies"
    __table_args__ = (
        CheckConstraint(
            "setup_status IN ('collecting_identity', 'active', 'archived')",
            name="ck_legacies_setup_status",
        ),
        Index("ix_legacies_owner_status", "owner_user_id", "setup_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_name: Mapped[str | None] = mapped_column(String(255))
    relationship_to_owner: Mapped[str | None] = mapped_column(String(80))
    is_self: Mapped[bool | None] = mapped_column(Boolean)
    setup_status: Mapped[str] = mapped_column(String(32), default=LegacySetupStatus.COLLECTING_IDENTITY.value)
    collaborator_code_digest: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    collaborator_code_ciphertext: Mapped[str | None] = mapped_column(Text)
    collaborator_code_hint: Mapped[str | None] = mapped_column(String(20))
    collaborator_code_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    collaborator_code_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    viewer_code_digest: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    viewer_code_ciphertext: Mapped[str | None] = mapped_column(Text)
    viewer_code_hint: Mapped[str | None] = mapped_column(String(20))
    viewer_code_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    viewer_code_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner = relationship("User", back_populates="legacies", foreign_keys=[owner_user_id])
    conversations = relationship("Conversation", back_populates="legacy")
    memories = relationship("Memory", back_populates="legacy", cascade="all, delete-orphan")
    collaborators = relationship("LegacyCollaborator", back_populates="legacy", cascade="all, delete-orphan")
    viewer_accesses = relationship("LegacyViewerAccess", back_populates="legacy", cascade="all, delete-orphan")
    visitor_profiles = relationship("LegacyVisitorProfile", back_populates="legacy", cascade="all, delete-orphan")
