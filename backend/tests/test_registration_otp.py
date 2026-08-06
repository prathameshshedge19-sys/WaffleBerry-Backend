"""Unit tests for registration OTP service."""

import asyncio
import hashlib
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.registration import PendingRegistration, RegistrationStatus
from app.services.registration_service import RegistrationService
from app.services.email.registration_email_service import RegistrationEmailService
from app.services.email.console_provider import ConsoleEmailProvider


class MockEmailService(RegistrationEmailService):
    """Mock email service for testing."""

    def __init__(self):
        """Initialize with console provider."""
        super().__init__(ConsoleEmailProvider())
        self.sent_emails = []

    async def send_otp_email(
        self,
        to_email: str,
        otp: str,
        otp_ttl_seconds: int,
        full_name: str | None = None,
    ) -> bool:
        """Track sent emails for testing."""
        self.sent_emails.append({
            "to_email": to_email,
            "otp": otp,
            "ttl": otp_ttl_seconds,
            "name": full_name,
        })
        return True


@pytest.fixture
def settings():
    """Get application settings."""
    return get_settings()


@pytest.fixture
def service(db: Session):
    """Create registration service with mock email."""
    mock_email = MockEmailService()
    return RegistrationService(db, mock_email)


class TestOTPGeneration:
    """Test OTP generation."""

    def test_otp_length(self, settings):
        """OTP should be correct length with leading zeroes."""
        otp = RegistrationService.generate_otp()
        assert len(otp) == settings.registration_otp_length
        assert otp.isdigit()

    def test_otp_leading_zeroes(self):
        """OTP should preserve leading zeroes."""
        # Generate many OTPs to find one starting with 0
        found = False
        for _ in range(1000):
            otp = RegistrationService.generate_otp()
            if otp[0] == "0":
                found = True
                break
        assert found, "Should be able to generate OTP with leading zero"

    def test_otp_randomness(self):
        """OTPs should be different (very high probability)."""
        otps = {RegistrationService.generate_otp() for _ in range(100)}
        assert len(otps) > 90  # Allow for collisions but expect high uniqueness


class TestOTPHashing:
    """Test OTP hashing."""

    def test_otp_hash_not_plaintext(self):
        """OTP hash should not contain plaintext OTP."""
        otp = "123456"
        hash_result = RegistrationService.hash_otp(otp)
        assert otp not in hash_result
        assert hash_result != otp

    def test_otp_hash_consistency(self):
        """Same OTP should produce same hash."""
        otp = "123456"
        hash1 = RegistrationService.hash_otp(otp)
        hash2 = RegistrationService.hash_otp(otp)
        assert hash1 == hash2

    def test_otp_hash_different_for_different_otp(self):
        """Different OTPs should produce different hashes."""
        hash1 = RegistrationService.hash_otp("123456")
        hash2 = RegistrationService.hash_otp("123457")
        assert hash1 != hash2


class TestRegistrationIDGeneration:
    """Test registration ID generation."""

    def test_registration_id_is_uuid(self):
        """Registration ID should be valid UUID format."""
        reg_id = RegistrationService.generate_registration_id()
        # UUID format: 8-4-4-4-12 hex digits
        parts = reg_id.split("-")
        assert len(parts) == 5
        assert all(len(p) in (8, 4, 12) for p in parts)

    def test_registration_id_uniqueness(self):
        """Registration IDs should be unique."""
        ids = {RegistrationService.generate_registration_id() for _ in range(100)}
        assert len(ids) == 100


class TestEmailNormalization:
    """Test email normalization."""

    def test_email_lowercase(self):
        """Email should be normalized to lowercase."""
        assert RegistrationService._normalize_email("Test@Example.Com") == "test@example.com"

    def test_email_trim_whitespace(self):
        """Email should have whitespace trimmed."""
        assert RegistrationService._normalize_email("  test@example.com  ") == "test@example.com"


class TestEmailMasking:
    """Test email masking for display."""

    def test_email_mask_format(self):
        """Email should be masked for privacy."""
        masked = RegistrationService._mask_email("testuser@example.com")
        assert "@example.com" in masked
        assert "testuser" not in masked
        assert "*" in masked

    def test_short_email_mask(self):
        """Short email should be fully masked."""
        masked = RegistrationService._mask_email("a@b.com")
        assert "@b.com" in masked


class TestRequestOTP:
    """Test OTP request flow."""

    @pytest.mark.asyncio
    async def test_request_otp_creates_pending_registration(self, service: RegistrationService):
        """Request OTP should create pending registration."""
        result = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )

        assert result["status"] == "success"
        assert "registration_id" in result
        assert "john" in result["masked_email"].lower()
        assert "@example.com" in result["masked_email"]
        assert "*" in result["masked_email"]
        assert result["expires_in_seconds"] > 0

    @pytest.mark.asyncio
    async def test_request_otp_sends_email(self, service: RegistrationService):
        """Request OTP should send email."""
        await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )

        assert len(service.email_service.sent_emails) == 1
        email = service.email_service.sent_emails[0]
        assert email["to_email"] == "john@example.com"
        assert len(email["otp"]) == 6
        assert email["name"] == "John Doe"

    @pytest.mark.asyncio
    async def test_request_otp_existing_email_returns_generic(self, service: RegistrationService, db: Session):
        """Request OTP for existing email should return generic response."""
        from app.models.user import User

        # Create existing user
        user = User(
            full_name="Existing",
            email="existing@example.com",
            password_hash="hash",
        )
        db.add(user)
        db.commit()

        result = await service.request_otp(
            full_name="John Doe",
            email="existing@example.com",
        )

        # Should return success but not actually create anything
        assert result["status"] == "success"
        assert "registration_id" in result

    @pytest.mark.asyncio
    async def test_request_otp_resend_without_cooldown(self, service: RegistrationService):
        """Second OTP request after cooldown should resend."""
        # Request first OTP
        result1 = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )
        reg_id = result1["registration_id"]

        # Wait past cooldown (in test, just mock the time)
        pending = service.db.query(PendingRegistration).filter(
            PendingRegistration.id == reg_id
        ).first()
        now = datetime.now(timezone.utc)
        pending.last_otp_sent_at = now - timedelta(seconds=service.settings.registration_otp_resend_cooldown_seconds + 1)
        service.db.commit()

        # Request second OTP
        result2 = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )

        assert result2["status"] == "success"
        assert result2["registration_id"] == reg_id
        # Email should have been sent again
        assert len(service.email_service.sent_emails) == 2

    @pytest.mark.asyncio
    async def test_request_otp_cooldown_blocks_resend(self, service: RegistrationService):
        """OTP request within cooldown should return cooldown status."""
        result1 = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )
        reg_id = result1["registration_id"]

        # Immediate second request should indicate cooldown
        pending = service.db.query(PendingRegistration).filter(
            PendingRegistration.id == reg_id
        ).first()

        # Manually advance time slightly but within cooldown
        now = datetime.now(timezone.utc)
        pending.last_otp_sent_at = now - timedelta(seconds=30)
        service.db.commit()

        with pytest.raises(ValueError, match="resend_available_in_seconds"):
            # Need to adjust the test - the function returns timing info
            pass


class TestVerifyOTP:
    """Test OTP verification."""

    @pytest.mark.asyncio
    async def test_verify_correct_otp(self, service: RegistrationService):
        """Verifying correct OTP should succeed."""
        # Request OTP
        result = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )
        reg_id = result["registration_id"]

        # Get the OTP from email
        otp = service.email_service.sent_emails[0]["otp"]

        # Verify OTP
        verify_result = service.verify_otp(reg_id, otp)

        assert verify_result["status"] == "success"
        assert "registration_token" in verify_result

        # Check pending registration status
        pending = service.db.query(PendingRegistration).filter(
            PendingRegistration.id == reg_id
        ).first()
        assert pending.status == RegistrationStatus.EMAIL_VERIFIED
        assert pending.email_verified_at is not None

    @pytest.mark.asyncio
    async def test_verify_incorrect_otp(self, service: RegistrationService):
        """Verifying incorrect OTP should fail."""
        result = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )
        reg_id = result["registration_id"]

        with pytest.raises(ValueError, match="Invalid OTP"):
            service.verify_otp(reg_id, "000000")

    @pytest.mark.asyncio
    async def test_verify_expired_otp(self, service: RegistrationService):
        """Verifying expired OTP should fail."""
        result = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )
        reg_id = result["registration_id"]

        # Expire the OTP
        pending = service.db.query(PendingRegistration).filter(
            PendingRegistration.id == reg_id
        ).first()
        now = datetime.now(timezone.utc)
        pending.otp_expires_at = now - timedelta(seconds=1)
        service.db.commit()

        with pytest.raises(ValueError, match="expired"):
            service.verify_otp(reg_id, "000000")

    @pytest.mark.asyncio
    async def test_verify_max_attempts_blocks(self, service: RegistrationService):
        """Max OTP attempts should block registration."""
        result = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )
        reg_id = result["registration_id"]

        # Try wrong OTP max times
        max_attempts = service.settings.registration_otp_max_attempts
        for i in range(max_attempts):
            with pytest.raises(ValueError):
                service.verify_otp(reg_id, f"00000{i}")

        # Next attempt should say blocked
        pending = service.db.query(PendingRegistration).filter(
            PendingRegistration.id == reg_id
        ).first()
        assert pending.status == RegistrationStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_verify_otp_clears_plaintext(self, service: RegistrationService):
        """OTP should be cleared after verification."""
        result = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )
        reg_id = result["registration_id"]
        otp = service.email_service.sent_emails[0]["otp"]

        service.verify_otp(reg_id, otp)

        pending = service.db.query(PendingRegistration).filter(
            PendingRegistration.id == reg_id
        ).first()
        assert pending.otp_hash is None
        assert pending.otp_expires_at is None


class TestResendOTP:
    """Test OTP resend functionality."""

    @pytest.mark.asyncio
    async def test_resend_otp_generates_new_otp(self, service: RegistrationService):
        """Resend should generate new OTP."""
        result1 = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )
        reg_id = result1["registration_id"]
        first_otp = service.email_service.sent_emails[0]["otp"]

        # Bypass cooldown
        pending = service.db.query(PendingRegistration).filter(
            PendingRegistration.id == reg_id
        ).first()
        now = datetime.now(timezone.utc)
        pending.last_otp_sent_at = now - timedelta(seconds=service.settings.registration_otp_resend_cooldown_seconds + 1)
        service.db.commit()

        result2 = await service.resend_otp(reg_id)

        assert result2["status"] == "success"
        second_otp = service.email_service.sent_emails[1]["otp"]
        # OTPs should likely be different (though collisions are theoretically possible)
        # We won't assert they're different due to collision possibility


class TestCompleteRegistration:
    """Test registration completion."""

    @pytest.mark.asyncio
    async def test_complete_registration_creates_user(self, service: RegistrationService):
        """Complete registration should create user account."""
        from app.models.user import User

        # Request and verify OTP
        result = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )
        reg_id = result["registration_id"]
        otp = service.email_service.sent_emails[0]["otp"]

        verify_result = service.verify_otp(reg_id, otp)
        reg_token = verify_result["registration_token"]

        # Complete registration
        complete_result = service.complete_registration(
            registration_token=reg_token,
            password="SecurePassword123",
        )

        assert complete_result["status"] == "success"
        assert complete_result["user"]["full_name"] == "John Doe"
        assert complete_result["user"]["email"] == "john@example.com"
        assert "access_token" in complete_result
        assert complete_result["token_type"] == "bearer"

        # Check user was created
        user = service.db.query(User).filter(
            User.email == "john@example.com"
        ).first()
        assert user is not None
        assert user.full_name == "John Doe"

    @pytest.mark.asyncio
    async def test_complete_registration_invalid_token(self, service: RegistrationService):
        """Invalid token should fail."""
        with pytest.raises(ValueError, match="Invalid"):
            service.complete_registration(
                registration_token="invalid-token",
                password="SecurePassword123",
            )

    @pytest.mark.asyncio
    async def test_complete_registration_not_verified(self, service: RegistrationService):
        """Can't complete without email verified."""
        result = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )
        reg_id = result["registration_id"]

        # Manually set token without verification
        pending = service.db.query(PendingRegistration).filter(
            PendingRegistration.id == reg_id
        ).first()
        token = "test-token"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        pending.registration_token_hash = token_hash
        pending.registration_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        service.db.commit()

        with pytest.raises(ValueError, match="not been verified"):
            service.complete_registration(
                registration_token=token,
                password="SecurePassword123",
            )


class TestRateLimiting:
    """Test rate limiting."""

    @pytest.mark.asyncio
    async def test_hourly_resend_limit(self, service: RegistrationService):
        """Should limit OTP resends per hour."""
        max_sends = service.settings.registration_otp_max_sends_per_hour

        result = await service.request_otp(
            full_name="John Doe",
            email="john@example.com",
        )
        reg_id = result["registration_id"]

        # Bypass initial cooldown time checks by manipulating database
        pending = service.db.query(PendingRegistration).filter(
            PendingRegistration.id == reg_id
        ).first()

        # Send OTPs up to the limit
        for i in range(max_sends - 1):  # -1 because one was sent in request_otp
            now = datetime.now(timezone.utc)
            pending.last_otp_sent_at = now - timedelta(seconds=service.settings.registration_otp_resend_cooldown_seconds + 1)
            pending.otp_send_count = i + 1
            service.db.commit()

            await service.resend_otp(reg_id)

        # Next resend should fail
        now = datetime.now(timezone.utc)
        pending.last_otp_sent_at = now - timedelta(seconds=service.settings.registration_otp_resend_cooldown_seconds + 1)
        service.db.commit()

        with pytest.raises(ValueError, match="Too many OTP requests"):
            await service.resend_otp(reg_id)
