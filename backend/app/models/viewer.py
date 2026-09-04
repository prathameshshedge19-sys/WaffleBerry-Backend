from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ViewerAccessStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class LegacyViewerAccess(Base):
    __tablename__ = "legacy_viewer_accesses"
    __table_args__ = (
        UniqueConstraint("legacy_id", "user_id", name="uq_legacy_viewer_accesses_legacy_user"),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_legacy_viewer_accesses_status"),
        Index("ix_legacy_viewer_accesses_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=ViewerAccessStatus.ACTIVE.value, server_default="active")
    granted_via_code: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    legacy = relationship("Legacy", back_populates="viewer_accesses")
    user = relationship("User", back_populates="viewer_accesses")
