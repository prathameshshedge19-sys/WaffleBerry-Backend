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
    def create_verification(db, user_id: int, otp_hash: str):
        """Create a verification record."""

        expires = datetime.now(timezone.utc) + timedelta(minutes=10)

        print("Creating verification...")
        print("UTC now:", datetime.now(timezone.utc))
        print("Expires:", expires)

        verification = Verification(
            user_id=user_id,
            otp_hash=otp_hash,
            expires_at=expires,
        )

        db.add(verification)
        db.commit()
        db.refresh(verification)

        return verification
 
    @staticmethod
    def verify_otp(db, user_id: int, otp: str) -> bool:
        """Verify the latest OTP for a user."""

        verification = (
            db.query(Verification)
            .filter(
                Verification.user_id == user_id,
                Verification.is_used == False,
            )
            .order_by(Verification.created_at.desc())
            .first()
        )

        print("Verification record:", verification)

        if not verification:
            print("No verification record found")
            return False

        print("Stored hash:", verification.otp_hash)
        print("Entered hash:", EmailVerificationService.hash_otp(otp))
        print("Expires at:", verification.expires_at)

        current_time = datetime.utcnow()
        print("Current UTC:", current_time)

        expires_at = verification.expires_at

        if expires_at.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=None)

        if expires_at < current_time:
            print("OTP expired")
            return False

        if verification.otp_hash != EmailVerificationService.hash_otp(otp):
            print("OTP hash mismatch")
            return False

        print("OTP verified successfully")

        verification.is_used = True
        db.commit()

        return True
        