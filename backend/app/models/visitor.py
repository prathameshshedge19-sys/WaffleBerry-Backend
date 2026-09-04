from datetime import datetime
from enum import Enum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class VisitorRelationshipStatus(str, Enum):
    CLAIMED = "claimed"
    VERIFIED_FROM_MEMORY = "verified_from_memory"
    UNVERIFIED = "unverified"


class LegacyVisitorProfile(Base):
    __tablename__ = "legacy_visitor_profiles"
    __table_args__ = (
        UniqueConstraint("legacy_id", "viewer_user_id", name="uq_legacy_visitor_profiles_legacy_viewer"),
        CheckConstraint(
            "relationship_status IN ('claimed', 'verified_from_memory', 'unverified')",
            name="ck_legacy_visitor_profiles_relationship_status",
        ),
        Index("ix_legacy_visitor_profiles_legacy_status", "legacy_id", "relationship_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False, index=True)
    viewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    preferred_name: Mapped[str | None] = mapped_column(String(255))
    claimed_relationship: Mapped[str | None] = mapped_column(String(80))
    matched_entity_id: Mapped[int | None] = mapped_column(ForeignKey("memory_entities.id", ondelete="SET NULL"), index=True)
    relationship_status: Mapped[str] = mapped_column(String(32), nullable=False, default=VisitorRelationshipStatus.CLAIMED.value, server_default="claimed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    legacy = relationship("Legacy", back_populates="visitor_profiles")
    viewer = relationship("User", back_populates="legacy_visitor_profiles")
    matched_entity = relationship("MemoryEntity")
