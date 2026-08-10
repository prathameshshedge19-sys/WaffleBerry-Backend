"""API regressions for canonical authentication after registration."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models.auth_challenge import AuthChallenge
from app.crud.user import UserCRUD
from app.services.auth_challenge_service import AuthChallengeService, EMAIL_VERIFICATION


@pytest.fixture()
def client_and_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app), session
    finally:
        app.dependency_overrides.clear()
        session.close()


def registration_authorization(db, email="new@example.com"):
    _, otp = AuthChallengeService.issue(
        db, email=email, full_name="New User", purpose=EMAIL_VERIFICATION,
        enforce_cooldown=False,
    )
    status, authorization = AuthChallengeService.verify(
        db, email=email, otp=otp, purpose=EMAIL_VERIFICATION,
    )
    assert status == "verified"
    return authorization


def test_completed_registration_returns_login_contract_and_persisting_token(client_and_db):
    client, db = client_and_db
    response = client.post(
        "/api/v1/complete-registration",
        json={"verification_token": registration_authorization(db), "password": "Secure123"},
    )
    assert response.status_code == 201
    payload = response.json()
    assert set(payload) == {"access_token", "token_type", "user"}
    assert payload["token_type"] == "bearer"
    assert set(payload["user"]) == {"user_id", "full_name", "email", "created_at"}

    refresh = client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert refresh.status_code == 200
    assert refresh.json() == payload["user"]

    login = client.post(
        "/api/v1/login", json={"email": "new@example.com", "password": "Secure123"},
    )
    assert login.status_code == 200
    assert set(login.json()) == set(payload)
    assert login.json()["token_type"] == payload["token_type"]
    assert login.json()["user"] == payload["user"]


def test_registration_failures_never_return_authentication(client_and_db):
    client, db = client_and_db
    _, otp = AuthChallengeService.issue(
        db, email="blocked@example.com", full_name="Blocked User",
        purpose=EMAIL_VERIFICATION, enforce_cooldown=False,
    )
    wrong = client.post(
        "/api/v1/verify-email", json={"email": "blocked@example.com", "otp": "000000"},
    )
    assert wrong.status_code == 400
    assert "access_token" not in wrong.json()

    challenge = db.query(AuthChallenge).order_by(AuthChallenge.challenge_id.desc()).first()
    challenge.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    expired = client.post(
        "/api/v1/verify-email", json={"email": "blocked@example.com", "otp": otp},
    )
    assert expired.status_code == 400
    assert "access_token" not in expired.json()

    invalid_password = client.post(
        "/api/v1/complete-registration",
        json={"verification_token": "x" * 32, "password": "weakpass"},
    )
    assert invalid_password.status_code == 422
    assert "access_token" not in invalid_password.json()

    expired_authorization = registration_authorization(db, "expired-auth@example.com")
    authorized_challenge = db.query(AuthChallenge).filter(
        AuthChallenge.email == "expired-auth@example.com",
    ).one()
    authorized_challenge.authorization_expires_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    db.commit()
    expired_completion = client.post(
        "/api/v1/complete-registration",
        json={"verification_token": expired_authorization, "password": "Secure123"},
    )
    assert expired_completion.status_code == 400
    assert "access_token" not in expired_completion.json()
    assert UserCRUD.get_user_by_email(db, "blocked@example.com") is None
    assert UserCRUD.get_user_by_email(db, "expired-auth@example.com") is None


def test_duplicate_email_cannot_receive_registration_authentication(client_and_db):
    client, db = client_and_db
    UserCRUD.create_user(
        db, full_name="Existing User", email="existing@example.com", password="Secure123",
    )
    response = client.post(
        "/api/v1/complete-registration",
        json={
            "verification_token": registration_authorization(db, "existing@example.com"),
            "password": "Secure456",
        },
    )
    assert response.status_code == 409
    assert "access_token" not in response.json()
    login = client.post(
        "/api/v1/login", json={"email": "existing@example.com", "password": "Secure123"},
    )
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"


def test_password_reset_still_returns_no_authentication(client_and_db):
    client, db = client_and_db
    UserCRUD.create_user(
        db, full_name="Reset User", email="reset@example.com", password="Before123",
    )
    _, otp = AuthChallengeService.issue(
        db, email="reset@example.com", purpose="password_reset", enforce_cooldown=False,
    )
    verified = client.post(
        "/api/v1/verify-reset-otp", json={"email": "reset@example.com", "otp": otp},
    )
    reset = client.post(
        "/api/v1/reset-password",
        json={
            "email": "reset@example.com",
            "reset_token": verified.json()["authorization"],
            "password": "After123",
        },
    )
    assert reset.status_code == 200
    assert reset.json() == {"message": "Password reset successfully."}
    assert "access_token" not in reset.json()
