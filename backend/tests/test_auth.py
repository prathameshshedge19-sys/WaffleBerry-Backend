from http.cookies import SimpleCookie

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.security import decode_token
from tests.conftest import register_user


def refresh_cookie(response):
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    return cookies["legarya_refresh"]


def token_lifetime_seconds(token, purpose):
    payload = decode_token(token, purpose)
    return payload["exp"] - payload["iat"]


def test_registration_login_current_user_and_refresh(test_context):
    client, _sessions, codes, _provider = test_context
    registration = register_user(client, codes)
    token = registration["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "one@example.com"

    login = client.post("/api/v1/auth/login", json={"email": "ONE@example.com", "password": "strong-pass-123"})
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"

    refresh = client.post("/api/v1/auth/refresh")
    assert refresh.status_code == 200
    assert refresh.json()["access_token"]


def test_login_remember_me_controls_refresh_lifetime_and_cookie_persistence(test_context):
    client, _sessions, codes, _provider = test_context
    register_user(client, codes)
    settings = get_settings()

    remembered = client.post(
        "/api/v1/auth/login",
        json={"email": "one@example.com", "password": "strong-pass-123", "remember_me": True},
    )
    assert remembered.status_code == 200
    remembered_cookie = refresh_cookie(remembered)
    remembered_payload = decode_token(remembered_cookie.value, "refresh")
    assert remembered_payload["remember_me"] is True
    assert token_lifetime_seconds(remembered_cookie.value, "refresh") == settings.remembered_refresh_token_expire_days * 86400
    assert remembered_cookie["max-age"] == str(settings.remembered_refresh_token_expire_days * 86400)
    assert remembered_cookie["expires"]
    assert remembered_cookie["httponly"] is True
    assert remembered_cookie["samesite"] == "lax"
    assert remembered_cookie["path"] == "/api/v1/auth"

    normal = client.post(
        "/api/v1/auth/login",
        json={"email": "one@example.com", "password": "strong-pass-123", "remember_me": False},
    )
    assert normal.status_code == 200
    normal_cookie = refresh_cookie(normal)
    normal_payload = decode_token(normal_cookie.value, "refresh")
    assert normal_payload["remember_me"] is False
    assert token_lifetime_seconds(normal_cookie.value, "refresh") == settings.refresh_token_expire_days * 86400
    assert normal_cookie["max-age"] == ""
    assert normal_cookie["expires"] == ""
    assert token_lifetime_seconds(remembered.json()["access_token"], "access") == settings.access_token_expire_minutes * 60
    assert token_lifetime_seconds(normal.json()["access_token"], "access") == settings.access_token_expire_minutes * 60


def test_refresh_preserves_remembered_session_and_logout_clears_it(test_context):
    client, _sessions, codes, _provider = test_context
    register_user(client, codes)
    settings = get_settings()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "one@example.com", "password": "strong-pass-123", "remember_me": True},
    )
    assert login.status_code == 200

    refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    refreshed_cookie = refresh_cookie(refreshed)
    refreshed_payload = decode_token(refreshed_cookie.value, "refresh")
    assert refreshed_payload["remember_me"] is True
    assert refreshed_cookie["max-age"] == str(settings.remembered_refresh_token_expire_days * 86400)
    assert refreshed_cookie["expires"]

    logged_out = client.post("/api/v1/auth/logout")
    assert logged_out.status_code == 204
    cleared_cookie = refresh_cookie(logged_out)
    assert cleared_cookie["max-age"] == "0"
    assert cleared_cookie["httponly"] is True
    assert "legarya_refresh" not in client.cookies
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_refresh_sessions_remain_isolated_between_users(test_context):
    client, _sessions, codes, _provider = test_context
    register_user(client, codes, email="one@example.com", name="One User")
    client.post("/api/v1/auth/login", json={"email": "one@example.com", "password": "strong-pass-123", "remember_me": True})

    with TestClient(app) as other_client:
        register_user(other_client, codes, email="two@example.com", name="Two User")
        other_client.post("/api/v1/auth/login", json={"email": "two@example.com", "password": "strong-pass-123", "remember_me": True})
        assert client.post("/api/v1/auth/refresh").json()["user"]["email"] == "one@example.com"
        assert other_client.post("/api/v1/auth/refresh").json()["user"]["email"] == "two@example.com"


def test_password_reset_is_single_use_and_invalidates_old_password(test_context):
    client, _sessions, codes, _provider = test_context
    register_user(client, codes)
    assert client.post("/api/v1/auth/forgot-password", json={"email": "one@example.com"}).status_code == 200
    code = codes[("one@example.com", "password_reset")]
    verified = client.post("/api/v1/auth/verify-reset-otp", json={"email": "one@example.com", "otp": code})
    reset_token = verified.json()["authorization"]
    payload = {"email": "one@example.com", "reset_token": reset_token, "password": "new-strong-pass-456"}
    assert client.post("/api/v1/auth/reset-password", json=payload).status_code == 200
    assert client.post("/api/v1/auth/reset-password", json=payload).status_code == 400
    assert client.post("/api/v1/auth/login", json={"email": "one@example.com", "password": "strong-pass-123"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "one@example.com", "password": "new-strong-pass-456"}).status_code == 200


def test_registration_requires_terms_and_auth_is_required(test_context):
    client, _sessions, _codes, _provider = test_context
    denied = client.post("/api/v1/auth/register", json={"full_name": "No Terms", "email": "no@example.com", "accepted_terms": False})
    assert denied.status_code == 400
    assert client.get("/api/v1/conversations").status_code == 401


def test_registration_resend_replaces_code_and_remains_verifiable(test_context):
    client, sessions, codes, _provider = test_context
    email = "resend@example.com"
    started = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Resend User", "email": email, "accepted_terms": True},
    )
    assert started.status_code == 202

    resent = client.post(
        "/api/v1/auth/resend-otp",
        json={"email": email, "purpose": "registration"},
    )
    assert resent.status_code == 200
    latest_code = codes[(email, "registration")]
    verified = client.post(
        "/api/v1/auth/verify-email",
        json={"email": email, "otp": latest_code},
    )
    assert verified.status_code == 200

    from app.models.auth_challenge import AuthChallenge
    from sqlalchemy import select

    with sessions() as db:
        challenges = db.scalars(
            select(AuthChallenge)
            .where(AuthChallenge.email == email)
            .order_by(AuthChallenge.id)
        ).all()
        assert len(challenges) == 2
        assert all(challenge.is_consumed for challenge in challenges)
