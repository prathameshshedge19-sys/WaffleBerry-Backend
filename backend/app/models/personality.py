"""Non-authoritative, one-per-Legacy personality projection and durable work item."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LegacyPersonalityProfile(Base):
    __tablename__ = "legacy_personality_profiles"
    __table_args__ = (
        CheckConstraint("source_generation >= 1 AND built_generation >= 0 AND built_generation <= source_generation", name="ck_personality_generations"),
        CheckConstraint("build_status IN ('pending', 'building', 'ready', 'failed')", name="ck_personality_build_status"),
        CheckConstraint("attempts >= 0", name="ck_personality_attempts"),
    )

    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="CASCADE"), primary_key=True)
    source_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    built_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    profile_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False, default="l13-conservative-v1", server_default="l13-conservative-v1")
    builder_id: Mapped[str] = mapped_column(String(64), nullable=False, default="deterministic-evidence-v1", server_default="deterministic-evidence-v1")
    build_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
