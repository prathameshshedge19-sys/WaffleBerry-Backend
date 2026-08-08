"""Email verification service."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from app.models.verification import Verification


class EmailVerificationService:
    """Handles email verification operations."""

    @staticmethod
    def generate_otp() -> str:
        """Generate a secure 6-digit OTP."""
        return f"{secrets.randbelow(900000) + 100000}"

    @staticmethod
    def hash_otp(otp: str) -> str:
        """Hash OTP using SHA-256."""
        return hashlib.sha256(otp.encode()).hexdigest()

    @staticmethod
    def create_verification(
        db,
        user_id: int,
        otp_hash: str,
        purpose: str = "email_verification"
    ):
        """Create a verification record."""

        expires = datetime.now(timezone.utc) + timedelta(minutes=10)

        verification = Verification(
            user_id=user_id,
            otp_hash=otp_hash,
            expires_at=expires,
            purpose=purpose,
        )

        db.add(verification)
        db.commit()
        db.refresh(verification)

        return verification
 
    @staticmethod
    def verify_otp(db, user_id: int, otp: str) -> bool:
        """Verify the latest OTP for a user."""

        return (
            EmailVerificationService.verify_otp_status(
                db,
                user_id,
                otp
            ) == "verified"
        )

    @staticmethod
    def verify_otp_status(
        db,
        user_id: int,
        otp: str,
        purpose: str = "email_verification"
    ) -> str:
        """Verify the latest OTP for a purpose and return its status."""

        verification = (
            db.query(Verification)
            .filter(
                Verification.user_id == user_id,
                Verification.purpose == purpose,
            )
            .order_by(Verification.created_at.desc())
            .first()
        )

        if not verification:
            return "invalid"

        if verification.is_used:
            return "used"

        current_time = datetime.utcnow()

        expires_at = verification.expires_at

        if expires_at.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=None)

        if expires_at < current_time:
            return "expired"

        if verification.otp_hash != EmailVerificationService.hash_otp(otp):
            return "invalid"

        verification.is_used = True
        db.commit()

        return "verified"
    
    @staticmethod
    def resend_otp(
        db,
        user_id: int,
        purpose: str = "email_verification"
    ) -> str:
        """Generate a new OTP and invalidate previous ones."""

        db.query(Verification).filter(
            Verification.user_id == user_id,
            Verification.purpose == purpose,
            Verification.is_used == False,
        ).update({"is_used": True})

        otp = EmailVerificationService.generate_otp()
        otp_hash = EmailVerificationService.hash_otp(otp)

        EmailVerificationService.create_verification(
            db=db,
            user_id=user_id,
            otp_hash=otp_hash,
            purpose=purpose,
        )

        return otp
    
        
