"""Focused Google identity verification and account-resolution regressions."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.crud.user import UserCRUD, verify_password
from app.db import Base, get_db
from app.main import app
from app.models.user import PlanTier, User
from app.models.memory import Legacy
from app.services import google_identity_service
from app.services.google_auth_service import resolve_google_account
from app.services.google_identity_service import GoogleIdentity, GoogleIdentityError


@pytest.fixture()
def client_and_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setattr(get_settings(), "google_web_client_id", "web-client.test")
    try:
        yield TestClient(app), session
    finally:
        app.dependency_overrides.clear()
        session.close()


def identity(sub="google-1", email="person@example.com", **kwargs):
    return GoogleIdentity(sub=sub, email=email, **kwargs)


def mock_identity(monkeypatch, value=None, error=None):
    def verify(_credential):
        if error:
            raise error
        return value or identity()
    monkeypatch.setattr(google_identity_service, "verify_google_credential", verify)


def test_verifier_accepts_valid_verified_identity(monkeypatch):
    monkeypatch.setattr(get_settings(), "google_web_client_id", "client-id")
    monkeypatch.setattr(google_identity_service.id_token, "verify_oauth2_token", lambda token, request, audience: {
        "iss": "https://accounts.google.com", "sub": " stable-sub ",
        "email": " Person@Example.com ", "email_verified": True, "name": "Person Name",
    })
    result = google_identity_service.verify_google_credential("credential")
    assert result == identity(sub="stable-sub", email="person@example.com", name="Person Name")


@pytest.mark.parametrize("claims", [
    {"iss": "https://accounts.google.com", "sub": "s", "email": "a@example.com", "email_verified": False},
    {"iss": "https://accounts.google.com", "sub": "", "email": "a@example.com", "email_verified": True},
    {"iss": "wrong", "sub": "s", "email": "a@example.com", "email_verified": True},
    {"iss": "accounts.google.com", "sub": "s", "email": "invalid", "email_verified": True},
])
def test_verifier_rejects_unverified_missing_sub_wrong_issuer_and_invalid_email(monkeypatch, claims):
    monkeypatch.setattr(get_settings(), "google_web_client_id", "client-id")
    monkeypatch.setattr(google_identity_service.id_token, "verify_oauth2_token", lambda *args: claims)
    with pytest.raises(GoogleIdentityError):
        google_identity_service.verify_google_credential("credential")


@pytest.mark.parametrize("provider_error", [ValueError("invalid"), ValueError("expired"), ValueError("audience")])
def test_verifier_maps_invalid_expired_and_wrong_audience_to_generic_error(monkeypatch, provider_error):
    monkeypatch.setattr(get_settings(), "google_web_client_id", "client-id")
    def fail(*args):
        raise provider_error
    monkeypatch.setattr(google_identity_service.id_token, "verify_oauth2_token", fail)
    with pytest.raises(GoogleIdentityError):
        google_identity_service.verify_google_credential("secret-token")


def test_missing_client_id_fails_safely(client_and_db, monkeypatch):
    client, _ = client_and_db
    monkeypatch.setattr(get_settings(), "google_web_client_id", None)
    response = client.post("/api/v1/auth/google", json={"credential": "token"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "google_auth_unavailable"


def test_new_account_requires_terms_and_rejects_extra_identity_fields(client_and_db, monkeypatch):
    client, db = client_and_db
    mock_identity(monkeypatch)
    response = client.post("/api/v1/auth/google", json={"credential": "token"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "terms_required"
    assert db.query(User).count() == 0
    rejected = client.post("/api/v1/auth/google", json={"credential": "token", "email": "fake@example.test"})
    assert rejected.status_code == 422


@pytest.mark.parametrize("malformed_consent", ["true", "false", 1, 0, None])
def test_new_account_rejects_non_boolean_terms_without_creation(
    client_and_db, monkeypatch, malformed_consent,
):
    client, db = client_and_db
    mock_identity(monkeypatch)
    response = client.post(
        "/api/v1/auth/google",
        json={"credential": "token", "accepted_terms": malformed_consent},
    )
    assert response.status_code == 422
    assert db.query(User).count() == 0


def test_new_google_user_has_normal_session_defaults_and_unknown_password(client_and_db, monkeypatch):
    client, db = client_and_db
    mock_identity(monkeypatch, identity(name="Google Person"))
    response = client.post("/api/v1/auth/google", json={"credential": "token", "accepted_terms": True})
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"access_token", "token_type", "user"}
    assert payload["token_type"] == "bearer"
    assert response.cookies.get("waffleberry_refresh")
    user = db.query(User).one()
    assert (user.plan, user.quota_exempt, user.timezone, user.is_verified, user.google_sub) == (
        PlanTier.FREE, False, "UTC", True, "google-1",
    )
    assert user.password_hash.startswith("scrypt$")
    assert verify_password("token", user.password_hash)[0] is False


def test_existing_sub_logs_into_same_account_without_mutation(client_and_db, monkeypatch):
    client, db = client_and_db
    user = UserCRUD.create_user(db, full_name="Existing", email="old@example.com", password="Secure123")
    user.google_sub = "google-1"
    user.plan, user.quota_exempt, user.timezone = PlanTier.PRO, True, "Asia/Kolkata"
    db.commit()
    original = (user.user_id, user.password_hash, user.email)
    mock_identity(monkeypatch, identity(email="new@example.com"))
    response = client.post("/api/v1/auth/google", json={"credential": "token"})
    assert response.status_code == 200
    db.refresh(user)
    assert (user.user_id, user.password_hash, user.email) == original
    assert (user.plan, user.quota_exempt, user.timezone) == (PlanTier.PRO, True, "Asia/Kolkata")


def test_matching_email_links_and_preserves_account(client_and_db, monkeypatch):
    client, db = client_and_db
    user = UserCRUD.create_user(db, full_name="Existing", email="PERSON@example.com", password="Secure123")
    user.plan, user.quota_exempt, user.timezone = PlanTier.PLUS, True, "Europe/London"
    db.commit()
    before = (user.user_id, user.password_hash, user.plan, user.quota_exempt, user.timezone)
    mock_identity(monkeypatch)
    response = client.post("/api/v1/auth/google", json={"credential": "token"})
    assert response.status_code == 200
    db.refresh(user)
    assert user.google_sub == "google-1"
    assert (user.user_id, user.password_hash, user.plan, user.quota_exempt, user.timezone) == before


def test_same_email_link_preserves_legacy_password_and_subsequent_login(client_and_db, monkeypatch):
    client, db = client_and_db
    user = UserCRUD.create_user(
        db, full_name="Password Account", email="Mixed.Case@Example.com",
        password="Secure123",
    )
    user.plan = PlanTier.PRO
    user.quota_exempt = True
    user.timezone = "Asia/Kolkata"
    legacy = Legacy(
        owner_user_id=user.user_id, display_name="Aaji",
        relationship="Grandmother", client_correlation_id="phase-12-5",
    )
    db.add(legacy)
    db.commit()
    original = {
        "user_id": user.user_id, "password_hash": user.password_hash,
        "plan": user.plan, "quota_exempt": user.quota_exempt,
        "timezone": user.timezone, "legacy_id": legacy.legacy_id,
    }

    mock_identity(
        monkeypatch,
        identity(sub="linked-sub", email="  MIXED.CASE@EXAMPLE.COM  "),
    )
    linked = client.post(
        "/api/v1/auth/google", json={"credential": "first-google-token"},
    )
    assert linked.status_code == 200
    assert linked.json()["user"]["user_id"] == original["user_id"]
    assert db.query(User).count() == 1
    db.refresh(user)
    assert user.google_sub == "linked-sub"
    assert user.password_hash == original["password_hash"]
    assert (user.plan, user.quota_exempt, user.timezone) == (
        original["plan"], original["quota_exempt"], original["timezone"],
    )
    assert db.query(Legacy).filter_by(legacy_id=original["legacy_id"]).one().owner_user_id == user.user_id

    password_login = client.post(
        "/api/v1/login",
        json={"email": "mixed.case@example.com", "password": "Secure123"},
    )
    assert password_login.status_code == 200
    assert password_login.json()["user"]["user_id"] == original["user_id"]

    mock_identity(monkeypatch, identity(sub="linked-sub", email="changed@example.com"))
    repeated = client.post(
        "/api/v1/auth/google", json={"credential": "second-google-token"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["user"]["user_id"] == original["user_id"]
    assert db.query(User).count() == 1
    db.refresh(user)
    assert user.email == "mixed.case@example.com"
    assert user.google_sub == "linked-sub"


def test_unverified_matching_account_becomes_verified(client_and_db, monkeypatch):
    client, db = client_and_db
    user = User(full_name="Pending", email="person@example.com", password_hash="hash", is_verified=False)
    db.add(user); db.commit()
    mock_identity(monkeypatch)
    assert client.post("/api/v1/auth/google", json={"credential": "token"}).status_code == 200
    db.refresh(user)
    assert user.is_verified is True


def test_different_sub_and_cross_account_conflicts(client_and_db, monkeypatch):
    client, db = client_and_db
    email_user = UserCRUD.create_user(db, full_name="Email", email="person@example.com", password="Secure123")
    email_user.google_sub = "other-sub"; db.commit()
    mock_identity(monkeypatch)
    response = client.post("/api/v1/auth/google", json={"credential": "token"})
    assert response.status_code == 409
    sub_user = UserCRUD.create_user(db, full_name="Sub", email="sub@example.com", password="Secure123")
    sub_user.google_sub = "google-1"; db.commit()
    response = client.post("/api/v1/auth/google", json={"credential": "token"})
    assert response.status_code == 409


def test_integrity_race_is_controlled(client_and_db, monkeypatch):
    client, db = client_and_db
    mock_identity(monkeypatch)
    original_flush = db.flush
    calls = 0
    def racing_flush(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise IntegrityError("insert", {}, Exception("unique"))
        return original_flush(*args, **kwargs)
    monkeypatch.setattr(db, "flush", racing_flush)
    response = client.post("/api/v1/auth/google", json={"credential": "token", "accepted_terms": True})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "google_identity_conflict"


def test_ordinary_password_login_still_works(client_and_db):
    client, db = client_and_db
    UserCRUD.create_user(db, full_name="Password", email="password@example.com", password="Secure123")
    assert client.post("/api/v1/login", json={"email": "password@example.com", "password": "Secure123"}).status_code == 200
