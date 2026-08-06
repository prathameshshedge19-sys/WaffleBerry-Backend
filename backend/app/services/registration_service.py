"""Service for managing email-verified registration flow."""

import hashlib
import logging
import secrets
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.registration import PendingRegistration, RegistrationStatus
from app.models.user import User
from app.services.email.registration_email_service import RegistrationEmailService
from app.services.token_service import create_access_token

logger = logging.getLogger(__name__)


class RegistrationService:
    """Service for managing email-verified registration."""

    def __init__(self, db: Session, email_service: RegistrationEmailService | None = None):
        """Initialize registration service."""
        self.db = db
        self.settings = get_settings()
        self.email_service = email_service or RegistrationEmailService()

    @staticmethod
    def generate_otp() -> str:
        """Generate a random OTP with specified length, preserving leading zeroes."""
        length = get_settings().registration_otp_length
        # Generate random integer and pad with leading zeroes
        otp_int = secrets.randbelow(10**length)
        return str(otp_int).zfill(length)

    @staticmethod
    def hash_otp(otp: str) -> str:
        """Hash OTP using SHA256 for storage."""
        return hashlib.sha256(otp.encode()).hexdigest()

    @staticmethod
    def generate_registration_id() -> str:
        """Generate opaque registration ID as UUID."""
        return str(uuid4())

    @staticmethod
    def _normalize_email(email: str) -> str:
        """Normalize email to lowercase for comparison."""
        return email.lower().strip()

    async def request_otp(
        self,
        full_name: str,
        email: str,
    ) -> dict:
        """
        Request OTP for email verification.
        
        Creates or updates a pending registration and sends OTP email.
        Returns registration ID and timing info.
        """
        email_normalized = self._normalize_email(email)
        now = datetime.now(timezone.utc)

        # Check if email is already registered
        existing_user = self.db.query(User).filter(
            User.email == email_normalized
        ).first()
        if existing_user:
            # Don't reveal that email exists - return generic response
            logger.info(f"Registration attempt for existing email: {email_normalized}")
            return {
                "status": "success",
                "registration_id": self.generate_registration_id(),
                "masked_email": self._mask_email(email),
                "expires_in_seconds": self.settings.registration_session_ttl_seconds,
                "resend_available_in_seconds": 0,
            }

        # Check if there's an existing pending registration
        pending = self.db.query(PendingRegistration).filter(
            PendingRegistration.email_normalized == email_normalized
        ).first()

        if pending:
            # Check if we can resend (cooldown)
            if pending.last_otp_sent_at:
                time_since_last_send = (now - pending.last_otp_sent_at).total_seconds()
                if time_since_last_send < self.settings.registration_otp_resend_cooldown_seconds:
                    resend_available_in = (
                        self.settings.registration_otp_resend_cooldown_seconds - time_since_last_send
                    )
                    return {
                        "status": "success",
                        "registration_id": pending.id,
                        "masked_email": self._mask_email(email),
                        "expires_in_seconds": self.settings.registration_session_ttl_seconds,
                        "resend_available_in_seconds": int(resend_available_in),
                    }

            # Check hourly send limit
            one_hour_ago = now - timedelta(hours=1)
            if pending.otp_send_count >= self.settings.registration_otp_max_sends_per_hour:
                if pending.last_otp_sent_at and pending.last_otp_sent_at > one_hour_ago:
                    logger.warning(f"Max OTP sends reached for {email_normalized}")
                    pending.status = RegistrationStatus.BLOCKED
                    self.db.commit()
                    raise ValueError("Too many OTP requests. Please try again later.")
                else:
                    # Reset counter
                    pending.otp_send_count = 0

            # Update existing pending registration
            otp = self.generate_otp()
            pending.full_name = full_name
            pending.otp_hash = self.hash_otp(otp)
            pending.otp_expires_at = now + timedelta(seconds=self.settings.registration_otp_ttl_seconds)
            pending.otp_attempt_count = 0
            pending.otp_send_count += 1
            pending.last_otp_sent_at = now
            pending.status = RegistrationStatus.PENDING_EMAIL_VERIFICATION
            registration_id = pending.id
        else:
            # Create new pending registration
            otp = self.generate_otp()
            pending = PendingRegistration(
                id=self.generate_registration_id(),
                full_name=full_name,
                email=email,
                email_normalized=email_normalized,
                otp_hash=self.hash_otp(otp),
                otp_expires_at=now + timedelta(seconds=self.settings.registration_otp_ttl_seconds),
                otp_attempt_count=0,
                otp_send_count=1,
                last_otp_sent_at=now,
                status=RegistrationStatus.PENDING_EMAIL_VERIFICATION,
            )
            self.db.add(pending)
            registration_id = pending.id

        self.db.commit()

        # Send OTP email
        try:
            await self.email_service.send_otp_email(
                to_email=email,
                otp=otp,
                otp_ttl_seconds=self.settings.registration_otp_ttl_seconds,
                full_name=full_name,
            )
        except Exception as e:
            logger.error(f"Failed to send OTP email to {email}: {e}")
            raise

        return {
            "status": "success",
            "registration_id": registration_id,
            "masked_email": self._mask_email(email),
            "expires_in_seconds": self.settings.registration_session_ttl_seconds,
            "resend_available_in_seconds": 0,
        }

    def verify_otp(self, registration_id: str, otp: str) -> dict:
        """
        Verify OTP and mark email as verified.
        
        Returns registration token for password creation step.
        """
        now = datetime.now(timezone.utc)

        pending = self.db.query(PendingRegistration).filter(
            PendingRegistration.id == registration_id
        ).first()

        if not pending:
            raise ValueError("Invalid registration ID")

        # Check if expired
        if pending.status == RegistrationStatus.COMPLETED:
            raise ValueError("Registration already completed")
        if pending.status == RegistrationStatus.EXPIRED or pending.status == RegistrationStatus.BLOCKED:
            raise ValueError("Registration is no longer valid")

        # Check if OTP is still valid
        if not pending.otp_expires_at or now > pending.otp_expires_at:
            pending.status = RegistrationStatus.EXPIRED
            self.db.commit()
            raise ValueError("OTP has expired")

        # Check attempt limit
        if pending.otp_attempt_count >= self.settings.registration_otp_max_attempts:
            pending.status = RegistrationStatus.BLOCKED
            self.db.commit()
            logger.warning(f"Max OTP attempts reached for registration {registration_id}")
            raise ValueError("Too many failed OTP attempts. Please request a new OTP.")

        # Verify OTP
        otp_hash = self.hash_otp(otp)
        if otp_hash != pending.otp_hash:
            pending.otp_attempt_count += 1
            self.db.commit()
            raise ValueError("Invalid OTP")

        # Mark email as verified
        pending.email_verified_at = now
        pending.status = RegistrationStatus.EMAIL_VERIFIED
        pending.otp_hash = None  # Clear OTP after successful verification
        pending.otp_expires_at = None

        # Generate registration token for next step
        registration_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(registration_token.encode()).hexdigest()
        pending.registration_token_hash = token_hash
        pending.registration_token_expires_at = now + timedelta(
            seconds=self.settings.registration_session_ttl_seconds
        )

        self.db.commit()

        return {
            "status": "success",
            "registration_token": registration_token,
            "expires_in_seconds": self.settings.registration_session_ttl_seconds,
        }

    async def resend_otp(self, registration_id: str) -> dict:
        """
        Resend OTP with rate limiting.
        
        Invalidates previous OTP and sends new one.
        """
        now = datetime.now(timezone.utc)

        pending = self.db.query(PendingRegistration).filter(
            PendingRegistration.id == registration_id
        ).first()

        if not pending:
            raise ValueError("Invalid registration ID")

        if pending.status != RegistrationStatus.PENDING_EMAIL_VERIFICATION:
            raise ValueError("Cannot resend OTP at this stage")

        # Check cooldown
        if pending.last_otp_sent_at:
            time_since_last_send = (now - pending.last_otp_sent_at).total_seconds()
            if time_since_last_send < self.settings.registration_otp_resend_cooldown_seconds:
                resend_available_in = (
                    self.settings.registration_otp_resend_cooldown_seconds - time_since_last_send
                )
                return {
                    "status": "cooldown",
                    "resend_available_in_seconds": int(resend_available_in),
                    "expires_in_seconds": self.settings.registration_session_ttl_seconds,
                }

        # Check hourly send limit
        one_hour_ago = now - timedelta(hours=1)
        if pending.otp_send_count >= self.settings.registration_otp_max_sends_per_hour:
            if pending.last_otp_sent_at and pending.last_otp_sent_at > one_hour_ago:
                logger.warning(f"Max OTP sends reached for {pending.email_normalized}")
                pending.status = RegistrationStatus.BLOCKED
                self.db.commit()
                raise ValueError("Too many OTP requests. Please try again later.")
            else:
                pending.otp_send_count = 0

        # Generate and send new OTP
        otp = self.generate_otp()
        pending.otp_hash = self.hash_otp(otp)
        pending.otp_expires_at = now + timedelta(seconds=self.settings.registration_otp_ttl_seconds)
        pending.otp_attempt_count = 0
        pending.otp_send_count += 1
        pending.last_otp_sent_at = now

        self.db.commit()

        try:
            await self.email_service.send_otp_email(
                to_email=pending.email,
                otp=otp,
                otp_ttl_seconds=self.settings.registration_otp_ttl_seconds,
                full_name=pending.full_name,
            )
        except Exception as e:
            logger.error(f"Failed to resend OTP to {pending.email}: {e}")
            raise

        return {
            "status": "success",
            "resend_available_in_seconds": 0,
            "expires_in_seconds": self.settings.registration_session_ttl_seconds,
        }

    def complete_registration(
        self,
        registration_token: str,
        password: str,
    ) -> dict:
        """
        Complete registration by creating user account.
        
        Validates registration token and creates user atomically.
        """
        now = datetime.now(timezone.utc)

        # Find pending registration by token hash
        token_hash = hashlib.sha256(registration_token.encode()).hexdigest()
        pending = self.db.query(PendingRegistration).filter(
            PendingRegistration.registration_token_hash == token_hash
        ).first()

        if not pending:
            raise ValueError("Invalid registration token")

        # Check token expiry
        if not pending.registration_token_expires_at or now > pending.registration_token_expires_at:
            raise ValueError("Registration token has expired")

        # Check email was verified
        if pending.status != RegistrationStatus.EMAIL_VERIFIED:
            raise ValueError("Email has not been verified")

        # Check if email already registered (final safety check)
        existing_user = self.db.query(User).filter(
            User.email == pending.email_normalized
        ).first()
        if existing_user:
            raise ValueError("Email is already registered")

        # Create user account
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        user = User(
            full_name=pending.full_name,
            email=pending.email_normalized,
            password_hash=password_hash,
        )
        self.db.add(user)

        # Mark registration as completed
        pending.status = RegistrationStatus.COMPLETED
        pending.completed_at = now
        pending.registration_token_hash = None
        pending.registration_token_expires_at = None

        self.db.commit()
        self.db.refresh(user)

        # Generate access token
        access_token = create_access_token(
            data={"sub": str(user.user_id)},
            expires_delta=timedelta(minutes=self.settings.access_token_expire_minutes),
        )

        return {
            "status": "success",
            "user": {
                "user_id": user.user_id,
                "full_name": user.full_name,
                "email": user.email,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            },
            "access_token": access_token,
            "token_type": "bearer",
        }

    @staticmethod
    def _mask_email(email: str) -> str:
        """Mask email for display."""
        parts = email.split("@")
        if len(parts) == 2:
            local, domain = parts
            if len(local) > 2:
                masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
            else:
                masked_local = "*" * len(local)
            return f"{masked_local}@{domain}"
        return "*" * len(email)
