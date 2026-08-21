"""Phase 11.2 Chat-only quota enforcement tests."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import user as user_api
from app.crud.user import ConversationCRUD, UserCRUD
from app.db import Base, get_db
from app.dependencies.auth import get_current_user
from app.main import app
from app.models.memory import Legacy
from app.models.quota import UserDailyUsage
from app.models.user import Message, PlanTier
from app.services.ai.exceptions import AIQuotaExceededError, AITimeoutError
from app.services.quota import QuotaService, get_plan_limits


class FakeChatService:
    def __init__(self, *, failure=None, chunks=("Hello", " there")):
        self.failure = failure
        self.chunks = chunks
        self.calls = 0

    async def generate_response_with_provenance(self, *_args, **_kwargs):
        self.calls += 1
        if self.failure:
            raise self.failure
        return SimpleNamespace(content="Hello there", memory_ids=(), retrieved_at=None)

    def stream_response_with_provenance(self, *_args, **_kwargs):
        self.calls += 1

        async def stream():
            for chunk in self.chunks:
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk

        return SimpleNamespace(stream=stream(), memory_ids=(), retrieved_at=None)


@pytest.fixture()
def chat_context(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    user = UserCRUD.create_user(
        db, full_name="Chat User", email="chat-quota@example.com", password="Fixture123"
    )
    legacy = Legacy(owner_user_id=user.user_id, display_name="A", relationship="Parent")
    db.add(legacy); db.commit(); db.refresh(legacy)
    conversation = ConversationCRUD.create_conversation(db, user.user_id, legacy_id=legacy.legacy_id)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(user_api, "SessionLocal", factory)

    async def normalized(_self, content):
        return SimpleNamespace(normalized_english_text=content)

    monkeypatch.setattr(user_api.LanguageNormalizationService, "normalize_semantically", normalized)
    try:
        yield TestClient(app), db, user, conversation, monkeypatch
    finally:
        app.dependency_overrides.clear()
        db.close()


def set_usage(db, user, used):
    row = QuotaService(db).get_daily_usage(user)
    row.chat_turns = used
    db.commit()
    return row


@pytest.mark.parametrize(
    ("plan", "penultimate"),
    [(PlanTier.FREE, 39), (PlanTier.PLUS, 119), (PlanTier.PRO, 399)],
)
def test_plan_final_turn_succeeds_then_next_is_blocked_before_ai(
    chat_context, plan, penultimate
):
    client, db, user, conversation, monkeypatch = chat_context
    user.plan = plan; db.commit()
    set_usage(db, user, penultimate)
    service = FakeChatService()
    monkeypatch.setattr(user_api, "get_chat_service", lambda: service)

    success = client.post(
        f"/api/v1/conversations/{conversation.conversation_id}/messages",
        json={"content": "Last allowed"},
    )
    assert success.status_code == 201
    assert QuotaService(db).get_daily_usage(user).chat_turns == penultimate + 1

    blocked = client.post(
        f"/api/v1/conversations/{conversation.conversation_id}/messages",
        json={"content": "Blocked"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == {
        "error": "quota_exceeded", "feature": "chat", "plan": plan.value,
        "resets_at": blocked.json()["detail"]["resets_at"],
        "upgrade_available": False,
    }
    assert datetime.fromisoformat(blocked.json()["detail"]["resets_at"]).tzinfo
    assert service.calls == 1
    assert db.query(Message).count() == 2


def test_provider_failure_refunds_then_next_success_uses_final_slot(chat_context):
    client, db, user, conversation, monkeypatch = chat_context
    set_usage(db, user, 39)
    failed_service = FakeChatService(failure=AITimeoutError("timeout"))
    monkeypatch.setattr(user_api, "get_chat_service", lambda: failed_service)
    failed = client.post(
        f"/api/v1/conversations/{conversation.conversation_id}/messages",
        json={"content": "Try"},
    )
    assert failed.status_code == 504
    assert QuotaService(db).get_daily_usage(user).chat_turns == 39

    success_service = FakeChatService()
    monkeypatch.setattr(user_api, "get_chat_service", lambda: success_service)
    assert client.post(
        f"/api/v1/conversations/{conversation.conversation_id}/messages",
        json={"content": "Retry"},
    ).status_code == 201
    assert QuotaService(db).get_daily_usage(user).chat_turns == 40


def test_provider_billing_failure_uses_safe_contract_and_refunds(chat_context):
    client, db, user, conversation, monkeypatch = chat_context
    set_usage(db, user, 39)
    monkeypatch.setattr(
        user_api, "get_chat_service",
        lambda: FakeChatService(failure=AIQuotaExceededError("provider billing detail")),
    )
    response = client.post(
        f"/api/v1/conversations/{conversation.conversation_id}/messages",
        json={"content": "Try"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "ai_service_unavailable",
        "message": "WaffleBerry is temporarily unavailable. Please try again later.",
    }
    assert "billing" not in response.text.lower()
    assert QuotaService(db).get_daily_usage(user).chat_turns == 39
    assert db.query(Message).count() == 0


def test_successful_stream_counts_once_and_failed_stream_refunds(chat_context):
    client, db, user, conversation, monkeypatch = chat_context
    service = FakeChatService(chunks=("One", " two", " three"))
    monkeypatch.setattr(user_api, "get_chat_service", lambda: service)
    response = client.post(
        f"/api/v1/conversations/{conversation.conversation_id}/messages/stream",
        json={"content": "Stream"},
    )
    assert response.status_code == 200 and "event: complete" in response.text
    assert QuotaService(db).get_daily_usage(user).chat_turns == 1

    failed_service = FakeChatService(chunks=("Partial", AITimeoutError("timeout")))
    monkeypatch.setattr(user_api, "get_chat_service", lambda: failed_service)
    failed = client.post(
        f"/api/v1/conversations/{conversation.conversation_id}/messages/stream",
        json={"content": "Fails"},
    )
    assert failed.status_code == 200 and "event: error" in failed.text
    assert QuotaService(db).get_daily_usage(user).chat_turns == 1


def test_quota_exempt_chat_is_unlimited_and_not_counted(chat_context):
    client, db, user, conversation, monkeypatch = chat_context
    user.quota_exempt = True; db.commit()
    set_usage(db, user, 999)
    service = FakeChatService()
    monkeypatch.setattr(user_api, "get_chat_service", lambda: service)
    assert client.post(
        f"/api/v1/conversations/{conversation.conversation_id}/messages",
        json={"content": "Unlimited"},
    ).status_code == 201
    assert QuotaService(db).get_daily_usage(user).chat_turns == 999


def test_exhausted_yesterday_does_not_block_new_local_day(chat_context, monkeypatch):
    _client, db, user, _conversation, _ = chat_context
    user.timezone = "Asia/Kolkata"; db.commit()
    service = QuotaService(db)
    yesterday = datetime(2026, 8, 20, 20, tzinfo=timezone.utc)
    row = service.get_daily_usage(user, yesterday)
    row.chat_turns = get_plan_limits(user.plan).chat_daily; db.commit()
    today = datetime(2026, 8, 21, 20, tzinfo=timezone.utc)
    assert service.check_chat(user, today).allowed
    assert service.check_chat(user, today).used == 0


def test_chat_exhaustion_does_not_block_other_features(chat_context):
    _client, db, user, conversation, _ = chat_context
    set_usage(db, user, get_plan_limits(user.plan).chat_daily)
    service = QuotaService(db)
    assert not service.check_chat(user).allowed
    assert service.get_live_call_remaining_seconds(user).allowed
    assert service.check_voice_play(user).allowed
    assert service.check_memory_capacity(user, conversation.legacy_id).allowed


def test_two_concurrent_attempts_cannot_both_reserve_final_slot(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-quota.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        user = UserCRUD.create_user(
            db, full_name="Concurrent", email="concurrent@example.com",
            password="Fixture123",
        )
        row = QuotaService(db).get_daily_usage(user)
        row.chat_turns = 39
        user_id = user.user_id
        db.commit()

    def reserve():
        with factory() as thread_db:
            thread_user = thread_db.get(type(user), user_id)
            return QuotaService(thread_db).reserve_chat(thread_user).decision.allowed

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: reserve(), range(2)))

    assert sorted(results) == [False, True]
    with factory() as db:
        assert db.query(UserDailyUsage).one().chat_turns == 40
