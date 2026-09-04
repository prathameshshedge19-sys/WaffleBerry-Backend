from datetime import datetime
from enum import Enum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CollaboratorStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class LegacyCollaborator(Base):
    __tablename__ = "legacy_collaborators"
    __table_args__ = (
        UniqueConstraint("legacy_id", "user_id", name="uq_legacy_collaborators_legacy_user"),
        CheckConstraint("role = 'collaborator'", name="ck_legacy_collaborators_role"),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_legacy_collaborators_status"),
        Index("ix_legacy_collaborators_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="collaborator", server_default="collaborator")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=CollaboratorStatus.ACTIVE.value, server_default=CollaboratorStatus.ACTIVE.value)
    added_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    legacy = relationship("Legacy", back_populates="collaborators")
    user = relationship("User", foreign_keys=[user_id], back_populates="collaborations")
    added_by = relationship("User", foreign_keys=[added_by_user_id])
