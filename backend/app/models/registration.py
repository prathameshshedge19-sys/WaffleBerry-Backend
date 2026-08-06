"""SQLAlchemy ORM models for email-verified registration."""

import enum
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, String, DateTime, Integer, Enum, Index
from sqlalchemy.sql import func
from app.db import Base


class RegistrationStatus(str, enum.Enum):
    """Status of a pending registration."""
    PENDING_EMAIL_VERIFICATION = "pending_email_verification"
    EMAIL_VERIFIED = "email_verified"
    COMPLETED = "completed"
    EXPIRED = "expired"
    BLOCKED = "blocked"


class PendingRegistration(Base):
    """Pending registration waiting for email verification."""

    __tablename__ = "pending_registrations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    
    # Registration info
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    email_normalized = Column(String(255), nullable=False, index=True)
    
    # OTP management
    otp_hash = Column(String(255), nullable=True)
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)
    otp_attempt_count = Column(Integer, default=0)
    otp_send_count = Column(Integer, default=0)
    last_otp_sent_at = Column(DateTime(timezone=True), nullable=True)
    
    # Email verification
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    
    # Registration completion
    registration_token_hash = Column(String(255), nullable=True)
    registration_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    
    # Status tracking
    status = Column(
        Enum(
            RegistrationStatus,
            values_callable=lambda status_enum: [s.value for s in status_enum],
            native_enum=False,
            validate_strings=True,
        ),
        default=RegistrationStatus.PENDING_EMAIL_VERIFICATION,
        nullable=False,
        index=True,
    )
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<PendingRegistration(id={self.id}, email={self.email}, status={self.status})>"
