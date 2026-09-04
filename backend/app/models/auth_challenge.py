from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    purpose: Mapped[str] = mapped_column(String(40), index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    otp_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_consumed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    authorization_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    authorization_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authorization_used: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

