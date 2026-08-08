"""Single-use OTP and authorization state for authentication flows."""

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.db import Base


class AuthChallenge(Base):
    """Persist one purpose-bound authentication challenge."""

    __tablename__ = "auth_challenges"

    challenge_id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    full_name = Column(String(255), nullable=True)
    purpose = Column(String(50), nullable=False, index=True)
    otp_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    is_consumed = Column(Boolean, nullable=False, default=False)
    authorization_hash = Column(String(255), nullable=True, unique=True)
    authorization_expires_at = Column(DateTime(timezone=True), nullable=True)
    authorization_used = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

