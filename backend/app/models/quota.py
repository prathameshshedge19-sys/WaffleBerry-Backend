"""Persistent, local-calendar-day usage counters."""

from sqlalchemy import CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db import Base


class UserDailyUsage(Base):
    __tablename__ = "user_daily_usage"
    __table_args__ = (
        UniqueConstraint("user_id", "usage_date", name="uq_user_daily_usage_user_date"),
        CheckConstraint("chat_turns >= 0", name="ck_daily_usage_chat_nonnegative"),
        CheckConstraint("live_call_seconds >= 0", name="ck_daily_usage_live_nonnegative"),
        CheckConstraint("voice_plays >= 0", name="ck_daily_usage_voice_nonnegative"),
    )

    usage_id = Column(Integer, primary_key=True)
    user_id = Column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    usage_date = Column(Date, nullable=False)
    chat_turns = Column(Integer, nullable=False, default=0, server_default="0")
    live_call_seconds = Column(Integer, nullable=False, default=0, server_default="0")
    voice_plays = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="daily_usage")
