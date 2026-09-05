from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("voice_preference IN ('marin', 'cedar')", name="ck_users_voice_preference"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    active_legacy_id: Mapped[int | None] = mapped_column(ForeignKey("legacies.id", use_alter=True, name="fk_users_active_legacy_id"), index=True)
    voice_preference: Mapped[str] = mapped_column(String(16), nullable=False, default="marin", server_default="marin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    legacies = relationship("Legacy", back_populates="owner", cascade="all, delete-orphan", foreign_keys="Legacy.owner_user_id")
    active_legacy = relationship("Legacy", foreign_keys=[active_legacy_id], post_update=True)
    collaborations = relationship("LegacyCollaborator", foreign_keys="LegacyCollaborator.user_id", back_populates="user", cascade="all, delete-orphan")
    viewer_accesses = relationship("LegacyViewerAccess", back_populates="user", cascade="all, delete-orphan")
    legacy_visitor_profiles = relationship("LegacyVisitorProfile", back_populates="viewer", cascade="all, delete-orphan")
