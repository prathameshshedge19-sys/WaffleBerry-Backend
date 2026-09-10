"""Shadow-only accounting. No content or transport credentials belong here."""
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PlanEntitlement(Base):
    __tablename__ = "plan_entitlements"
    __table_args__ = (CheckConstraint("plan IN ('free','plus','pro')", name="ck_entitlement_plan"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    plan: Mapped[str] = mapped_column(String(8), default="free", server_default="free")
    quota_exempt: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanUsage(Base):
    __tablename__ = "plan_usage"
    __table_args__ = (
        CheckConstraint("amount >= 0 AND reserved >= 0 AND released >= 0", name="ck_plan_usage_amounts"),
        CheckConstraint("state IN ('pending','completed','failed','interrupted')", name="ck_plan_usage_state"),
        Index("ix_plan_usage_user_day_feature", "user_id", "usage_day", "feature"),
    )
    operation_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    feature: Mapped[str] = mapped_column(String(40))
    usage_day: Mapped[date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved: Mapped[int] = mapped_column(BigInteger, default=0)
    released: Mapped[int] = mapped_column(BigInteger, default=0)
    state: Mapped[str] = mapped_column(String(16))
    plan_version: Mapped[str] = mapped_column(String(32), default="2026-09-10-v1")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanVoiceInterval(Base):
    __tablename__ = "plan_voice_intervals"
    __table_args__ = (
        CheckConstraint("generation >= 0", name="ck_plan_voice_generation"),
        CheckConstraint("observed_until >= started_at", name="ck_plan_voice_order"),
        Index("ix_plan_voice_user_started", "user_id", "started_at"),
    )
    # No session FK: deleting a conversation must not restore today's usage.
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    feature: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uncertain_tail: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class PlanTrackingState(Base):
    __tablename__ = "plan_tracking_state"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor_turn_id: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
