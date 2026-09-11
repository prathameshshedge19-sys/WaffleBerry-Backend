import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.services.realtime_sessions import RealtimeError, validate_origin


ANDROID_ORIGIN = "https://localhost"
WEB_ORIGIN = "https://www.waffleberry.app"


def settings(**overrides):
    values = {"jwt_secret_key": "x" * 64, "cors_origins": WEB_ORIGIN, "android_app_origin": ANDROID_ORIGIN}
    values.update(overrides)
    return Settings(**values)


def test_android_origin_is_exact_and_additive():
    configured = settings()
    assert configured.allowed_cors_origins == [WEB_ORIGIN, ANDROID_ORIGIN]
    assert validate_origin(ANDROID_ORIGIN, configured) == ANDROID_ORIGIN
    assert validate_origin(WEB_ORIGIN, configured) == WEB_ORIGIN
    with pytest.raises(RealtimeError):
        validate_origin("https://evil.example", configured)
    with pytest.raises(RealtimeError):
        validate_origin("capacitor://localhost", configured)
    with pytest.raises(RealtimeError):
        validate_origin("file://", configured)


def test_android_origin_setting_rejects_wildcards_and_alternates():
    with pytest.raises(ValidationError):
        settings(android_app_origin="https://localhost.evil.example")
    with pytest.raises(ValueError, match="explicit origins"):
        Settings(jwt_secret_key="x" * 64, cors_origins="*").allowed_cors_origins


def test_http_cors_preflight_allows_android_and_web_but_denies_foreign():
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings().allowed_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.get("/probe")(lambda: {"ok": True})
    client = TestClient(app)
    for origin in (ANDROID_ORIGIN, WEB_ORIGIN):
        response = client.options("/probe", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        })
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-credentials"] == "true"
    denied = client.options("/probe", headers={
        "Origin": "https://evil.example",
        "Access-Control-Request-Method": "GET",
    })
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers
