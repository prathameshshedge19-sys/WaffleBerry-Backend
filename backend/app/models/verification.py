"""SQLAlchemy ORM model for Email Verification OTP."""

from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.db import Base


class Verification(Base):
    """Email Verification OTP model."""

    __tablename__ = "verification"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)

    otp_hash = Column(String(255), nullable=False)

    purpose = Column(
        String(50),
        nullable=False,
        default="email_verification"
    )

    expires_at = Column(DateTime(timezone=True), nullable=False)

    attempt_count = Column(Integer, default=0, nullable=False)

    is_used = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")

    def __repr__(self):
        return f"<Verification(user_id={self.user_id})>"
