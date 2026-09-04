import enum
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PromptStatus(str, enum.Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    SKIPPED = "skipped"


class BuilderActivity(Base):
    __tablename__ = "builder_activities"
    __table_args__ = (
        UniqueConstraint("legacy_id", "activity_date", name="uq_builder_activities_legacy_date"),
        Index("ix_builder_activities_user_date", "user_id", "activity_date"),
        Index("ix_builder_activities_legacy_date", "legacy_id", "activity_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False, index=True)
    activity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    activity_date: Mapped[date] = mapped_column(Date, nullable=False)
    memory_id: Mapped[int | None] = mapped_column(ForeignKey("memories.id", ondelete="SET NULL"), index=True)
    contribution_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    first_contributor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    last_contributor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    first_contribution_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_contribution_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class DailyPrompt(Base):
    __tablename__ = "daily_prompts"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'answered', 'skipped')", name="ck_daily_prompts_status"),
        Index("ix_daily_prompts_legacy_date", "legacy_id", "shown_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False, index=True)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    shown_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PromptStatus.PENDING.value, server_default=PromptStatus.PENDING.value)
    answered_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
