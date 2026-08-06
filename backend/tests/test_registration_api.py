"""Integration tests for registration API endpoints."""

import hashlib
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db import get_db
from app.models.user import User
from app.models.registration import PendingRegistration, RegistrationStatus


@pytest.fixture
def client(db_session):
    """Create test client with in-memory database."""
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


class TestRequestOTPEndpoint:
    """Test POST /registration/email/request-otp endpoint."""

    def test_request_otp_success(self, client: TestClient, db_session: Session):
        """Request OTP with valid data should succeed."""
        response = client.post(
            "/api/v1/registration/email/request-otp",
            json={
                "full_name": "John Doe",
                "email": "john@example.com",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "registration_id" in data
        assert data["masked_email"] == "*****ohn@example.com"
        assert data["expires_in_seconds"] > 0

        # Check pending registration was created
        pending = db_session.query(PendingRegistration).filter(
            PendingRegistration.email_normalized == "john@example.com"
        ).first()
        assert pending is not None
        assert pending.full_name == "John Doe"

    def test_request_otp_invalid_email(self, client: TestClient):
        """Request OTP with invalid email should fail."""
        response = client.post(
            "/api/v1/registration/email/request-otp",
            json={
                "full_name": "John Doe",
                "email": "invalid-email",
            },
        )

        assert response.status_code == 422

    def test_request_otp_missing_name(self, client: TestClient):
        """Request OTP without name should fail."""
        response = client.post(
            "/api/v1/registration/email/request-otp",
            json={
                "email": "john@example.com",
            },
        )

        assert response.status_code == 422

    def test_request_otp_existing_email(self, client: TestClient, db_session: Session):
        """Request OTP for existing user should return generic response."""
        # Create existing user
        user = User(
            full_name="Existing",
            email="existing@example.com",
            password_hash="hash",
        )
        db_session.add(user)
        db_session.commit()

        response = client.post(
            "/api/v1/registration/email/request-otp",
            json={
                "full_name": "John Doe",
                "email": "existing@example.com",
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"


class TestVerifyOTPEndpoint:
    """Test POST /registration/email/verify-otp endpoint."""

    def test_verify_otp_success(self, client: TestClient, db_session: Session):
        """Verify OTP with correct code should succeed."""
        # Create pending registration with OTP
        pending = PendingRegistration(
            id="test-id-1",
            full_name="John Doe",
            email="john@example.com",
            email_normalized="john@example.com",
            otp_hash=hashlib.sha256("123456".encode()).hexdigest(),
            otp_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            otp_attempt_count=0,
            status=RegistrationStatus.PENDING_EMAIL_VERIFICATION,
        )
        db_session.add(pending)
        db_session.commit()

        response = client.post(
            "/api/v1/registration/email/verify-otp",
            json={
                "registration_id": "test-id-1",
                "otp": "123456",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "registration_token" in data

    def test_verify_otp_invalid_code(self, client: TestClient, db_session: Session):
        """Verify OTP with wrong code should fail."""
        pending = PendingRegistration(
            id="test-id-2",
            full_name="John Doe",
            email="john@example.com",
            email_normalized="john@example.com",
            otp_hash=hashlib.sha256("123456".encode()).hexdigest(),
            otp_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            otp_attempt_count=0,
            status=RegistrationStatus.PENDING_EMAIL_VERIFICATION,
        )
        db_session.add(pending)
        db_session.commit()

        response = client.post(
            "/api/v1/registration/email/verify-otp",
            json={
                "registration_id": "test-id-2",
                "otp": "000000",
            },
        )

        assert response.status_code == 400

    def test_verify_otp_expired(self, client: TestClient, db_session: Session):
        """Verify expired OTP should fail."""
        pending = PendingRegistration(
            id="test-id-3",
            full_name="John Doe",
            email="john@example.com",
            email_normalized="john@example.com",
            otp_hash=hashlib.sha256("123456".encode()).hexdigest(),
            otp_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            otp_attempt_count=0,
            status=RegistrationStatus.PENDING_EMAIL_VERIFICATION,
        )
        db_session.add(pending)
        db_session.commit()

        response = client.post(
            "/api/v1/registration/email/verify-otp",
            json={
                "registration_id": "test-id-3",
                "otp": "123456",
            },
        )

        assert response.status_code == 400

    def test_verify_otp_max_attempts(self, client: TestClient, db_session: Session):
        """Max OTP attempts should block verification."""
        pending = PendingRegistration(
            id="test-id-4",
            full_name="John Doe",
            email="john@example.com",
            email_normalized="john@example.com",
            otp_hash=hashlib.sha256("123456".encode()).hexdigest(),
            otp_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            otp_attempt_count=5,  # Max attempts
            status=RegistrationStatus.PENDING_EMAIL_VERIFICATION,
        )
        db_session.add(pending)
        db_session.commit()

        response = client.post(
            "/api/v1/registration/email/verify-otp",
            json={
                "registration_id": "test-id-4",
                "otp": "123456",
            },
        )

        assert response.status_code == 400

    def test_verify_otp_invalid_registration(self, client: TestClient):
        """Verify OTP with invalid registration ID should fail."""
        response = client.post(
            "/api/v1/registration/email/verify-otp",
            json={
                "registration_id": "invalid-id",
                "otp": "123456",
            },
        )

        assert response.status_code == 400


class TestResendOTPEndpoint:
    """Test POST /registration/email/resend-otp endpoint."""

    def test_resend_otp_success(self, client: TestClient, db_session: Session):
        """Resend OTP with valid registration should succeed."""
        now = datetime.now(timezone.utc)
        pending = PendingRegistration(
            id="test-id-5",
            full_name="John Doe",
            email="john@example.com",
            email_normalized="john@example.com",
            otp_hash=hashlib.sha256("123456".encode()).hexdigest(),
            otp_expires_at=now + timedelta(minutes=10),
            otp_attempt_count=0,
            otp_send_count=1,
            last_otp_sent_at=now - timedelta(seconds=100),  # Past cooldown
            status=RegistrationStatus.PENDING_EMAIL_VERIFICATION,
        )
        db_session.add(pending)
        db_session.commit()

        response = client.post(
            "/api/v1/registration/email/resend-otp",
            json={"registration_id": "test-id-5"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    def test_resend_otp_cooldown(self, client: TestClient, db_session: Session):
        """Resend OTP within cooldown should fail."""
        now = datetime.now(timezone.utc)
        pending = PendingRegistration(
            id="test-id-6",
            full_name="John Doe",
            email="john@example.com",
            email_normalized="john@example.com",
            otp_hash=hashlib.sha256("123456".encode()).hexdigest(),
            otp_expires_at=now + timedelta(minutes=10),
            otp_attempt_count=0,
            otp_send_count=1,
            last_otp_sent_at=now - timedelta(seconds=30),  # Within cooldown
            status=RegistrationStatus.PENDING_EMAIL_VERIFICATION,
        )
        db_session.add(pending)
        db_session.commit()

        response = client.post(
            "/api/v1/registration/email/resend-otp",
            json={"registration_id": "test-id-6"},
        )

        # Should indicate cooldown status
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cooldown"
        assert data["resend_available_in_seconds"] > 0

    def test_resend_otp_invalid_id(self, client: TestClient):
        """Resend OTP with invalid ID should fail."""
        response = client.post(
            "/api/v1/registration/email/resend-otp",
            json={"registration_id": "invalid-id"},
        )

        assert response.status_code == 400


class TestCompleteRegistrationEndpoint:
    """Test POST /registration/complete endpoint."""

    def test_complete_registration_success(self, client: TestClient, db_session: Session):
        """Complete registration with valid token should create user."""
        # Create verified pending registration
        token = "test-token"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        pending = PendingRegistration(
            id="test-id-7",
            full_name="John Doe",
            email="john@example.com",
            email_normalized="john@example.com",
            otp_hash=None,
            otp_expires_at=None,
            email_verified_at=now,
            registration_token_hash=token_hash,
            registration_token_expires_at=now + timedelta(minutes=30),
            status=RegistrationStatus.EMAIL_VERIFIED,
        )
        db_session.add(pending)
        db_session.commit()

        response = client.post(
            "/api/v1/registration/complete",
            json={
                "registration_token": token,
                "password": "SecurePassword123",
                "confirm_password": "SecurePassword123",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["user"]["full_name"] == "John Doe"
        assert data["user"]["email"] == "john@example.com"
        assert "access_token" in data

        # Check user was created
        user = db_session.query(User).filter(
            User.email == "john@example.com"
        ).first()
        assert user is not None

    def test_complete_registration_password_mismatch(self, client: TestClient, db_session: Session):
        """Complete registration with mismatched passwords should fail."""
        token = "test-token"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        pending = PendingRegistration(
            id="test-id-8",
            full_name="John Doe",
            email="john@example.com",
            email_normalized="john@example.com",
            email_verified_at=now,
            registration_token_hash=token_hash,
            registration_token_expires_at=now + timedelta(minutes=30),
            status=RegistrationStatus.EMAIL_VERIFIED,
        )
        db_session.add(pending)
        db_session.commit()

        response = client.post(
            "/api/v1/registration/complete",
            json={
                "registration_token": token,
                "password": "SecurePassword123",
                "confirm_password": "DifferentPassword123",
            },
        )

        assert response.status_code == 400

    def test_complete_registration_invalid_token(self, client: TestClient):
        """Complete registration with invalid token should fail."""
        response = client.post(
            "/api/v1/registration/complete",
            json={
                "registration_token": "invalid-token",
                "password": "SecurePassword123",
                "confirm_password": "SecurePassword123",
            },
        )

        assert response.status_code == 400

    def test_complete_registration_expired_token(self, client: TestClient, db_session: Session):
        """Complete registration with expired token should fail."""
        token = "test-token"
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        pending = PendingRegistration(
            id="test-id-9",
            full_name="John Doe",
            email="john@example.com",
            email_normalized="john@example.com",
            email_verified_at=now,
            registration_token_hash=token_hash,
            registration_token_expires_at=now - timedelta(seconds=1),  # Expired
            status=RegistrationStatus.EMAIL_VERIFIED,
        )
        db_session.add(pending)
        db_session.commit()

        response = client.post(
            "/api/v1/registration/complete",
            json={
                "registration_token": token,
                "password": "SecurePassword123",
                "confirm_password": "SecurePassword123",
            },
        )

        assert response.status_code == 400
