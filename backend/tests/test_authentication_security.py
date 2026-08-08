"""Security regression tests for OTP-backed authentication flows."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.auth_challenge import AuthChallenge
from app.models.user import User
from app.services.auth_challenge_service import (
    AuthChallengeService, EMAIL_VERIFICATION, PASSWORD_RESET,
)
from app.crud.user import UserCRUD, hash_password, verify_password


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_registration_challenge_is_purpose_bound_and_single_use(db):
    _, otp = AuthChallengeService.issue(
        db, email=" New@Example.com ", full_name="New User",
        purpose=EMAIL_VERIFICATION, enforce_cooldown=False,
    )
    assert AuthChallengeService.verify(
        db, email="new@example.com", otp=otp, purpose=PASSWORD_RESET,
    )[0] == "invalid"
    status, token = AuthChallengeService.verify(
        db, email="new@example.com", otp=otp, purpose=EMAIL_VERIFICATION,
    )
    assert status == "verified"
    challenge = AuthChallengeService.consume_authorization(
        db, authorization=token, purpose=EMAIL_VERIFICATION,
    )
    assert challenge.full_name == "New User"
    db.commit()
    assert AuthChallengeService.consume_authorization(
        db, authorization=token, purpose=EMAIL_VERIFICATION,
    ) is None


def test_wrong_expired_and_superseded_otps_fail(db):
    old_challenge, old_otp = AuthChallengeService.issue(
        db, email="person@example.com", purpose=PASSWORD_RESET,
        enforce_cooldown=False,
    )
    assert AuthChallengeService.verify(
        db, email="person@example.com", otp="000000", purpose=PASSWORD_RESET,
    )[0] == "invalid"
    _, new_otp = AuthChallengeService.issue(
        db, email="person@example.com", purpose=PASSWORD_RESET,
        enforce_cooldown=False,
    )
    assert AuthChallengeService.verify(
        db, email="person@example.com", otp=old_otp, purpose=PASSWORD_RESET,
    )[0] == "invalid"
    latest = db.query(AuthChallenge).order_by(AuthChallenge.challenge_id.desc()).first()
    latest.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert AuthChallengeService.verify(
        db, email="person@example.com", otp=new_otp, purpose=PASSWORD_RESET,
    )[0] == "expired"
    assert old_challenge.is_consumed


def test_passwords_are_salted_and_legacy_hashes_upgrade(db):
    first = hash_password("Secure123")
    second = hash_password("Secure123")
    assert first != second
    assert verify_password("Secure123", first) == (True, False)
    user = User(
        full_name="Legacy", email="legacy@example.com",
        password_hash=__import__("hashlib").sha256(b"Secure123").hexdigest(),
        is_verified=True,
    )
    db.add(user)
    db.commit()
    assert UserCRUD.authenticate_user(db, user.email, "Secure123") is user
    assert user.password_hash.startswith("scrypt$")
    assert UserCRUD.authenticate_user(db, user.email, "wrong") is None

