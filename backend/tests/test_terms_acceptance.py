"""Registration Terms & Conditions contract regressions."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.schemas.user import UserCreate
from app.services.email_service import EmailService


@pytest.fixture()
def registration_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        session.close()


@pytest.mark.parametrize("payload", [
    {"full_name": "Terms User", "email": "terms@example.test"},
    {"full_name": "Terms User", "email": "terms@example.test", "accepted_terms": False},
])
def test_user_create_rejects_missing_or_false_terms(payload):
    with pytest.raises(ValidationError):
        UserCreate.model_validate(payload)


def test_registration_endpoint_rejects_missing_or_false_terms(registration_client):
    base = {"full_name": "Terms User", "email": "terms@example.test"}
    assert registration_client.post("/api/v1/users", json=base).status_code == 422
    assert registration_client.post(
        "/api/v1/users", json={**base, "accepted_terms": False},
    ).status_code == 422


def test_registration_with_accepted_terms_uses_existing_flow(registration_client, monkeypatch):
    send_otp = AsyncMock(return_value=None)
    monkeypatch.setattr(EmailService, "send_otp", send_otp)
    response = registration_client.post(
        "/api/v1/users",
        json={
            "full_name": "Terms User",
            "email": "accepted@example.com",
            "accepted_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"message": "Verification code sent."}
    send_otp.assert_awaited_once()
