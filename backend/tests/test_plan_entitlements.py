"""Phase 11.6 centralized plan entitlement and write-security tests."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.memory import Legacy, Memory, MemoryReviewStatus, MemoryType
from app.models.user import PlanTier, User
from app.schemas.user import UserCreate, UserResponse, UserSettingsCreate
from app.services.quota import QuotaService, get_plan_limits


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def owner(db, plan=PlanTier.FREE, *, exempt=False, suffix="user"):
    user = User(
        full_name="Plan User", email=f"{suffix}@example.test",
        password_hash="hash", plan=plan, quota_exempt=exempt,
    )
    db.add(user); db.flush()
    legacy = Legacy(
        owner_user_id=user.user_id, display_name="Legacy", relationship="Parent"
    )
    db.add(legacy); db.commit()
    return user, legacy


@pytest.mark.parametrize("plan", list(PlanTier))
def test_every_feature_uses_the_same_plan_entitlement(db, plan):
    user, legacy = owner(db, plan, suffix=plan.value)
    limits = get_plan_limits(plan)
    quota = QuotaService(db)
    usage = quota.get_daily_usage(user)
    usage.chat_turns = limits.chat_daily - 1
    usage.live_call_seconds = limits.live_call_seconds_daily - 1
    usage.voice_plays = limits.voice_plays_daily - 1
    db.commit()

    assert quota.consume_chat(user).used == limits.chat_daily
    assert not quota.consume_chat(user).allowed
    assert quota.consume_live_call_seconds(user, 1).used == limits.live_call_seconds_daily
    assert not quota.consume_live_call_seconds(user, 1).allowed
    assert quota.consume_voice_play(user).used == limits.voice_plays_daily
    assert not quota.consume_voice_play(user).allowed

    db.add_all([
        Memory(
            legacy_id=legacy.legacy_id, memory_type=MemoryType.ATOMIC,
            category="life", title=f"Memory {index}", summary="Canonical",
            normalized_fingerprint=f"{plan.value}-{index}",
            review_status=MemoryReviewStatus.APPROVED,
        )
        for index in range(limits.memories_per_legacy)
    ])
    db.commit()
    decision = quota.check_memory_capacity(user, legacy.legacy_id)
    assert decision.limit == limits.memories_per_legacy
    assert decision.used == limits.memories_per_legacy
    assert not decision.allowed


def test_upgrade_mid_day_preserves_usage_and_unlocks_all_daily_features(db):
    user, _ = owner(db)
    quota = QuotaService(db)
    usage = quota.get_daily_usage(user)
    usage.chat_turns = 40
    usage.live_call_seconds = 180
    usage.voice_plays = 10
    usage_id = usage.usage_id
    db.commit()
    assert not quota.check_chat(user).allowed
    assert not quota.get_live_call_remaining_seconds(user).allowed
    assert not quota.check_voice_play(user).allowed

    user.plan = PlanTier.PLUS
    db.commit()
    assert quota.get_daily_usage(user).usage_id == usage_id
    assert (usage.chat_turns, usage.live_call_seconds, usage.voice_plays) == (40, 180, 10)
    assert quota.check_chat(user).allowed
    assert quota.get_live_call_remaining_seconds(user).allowed
    assert quota.check_voice_play(user).allowed


def test_downgrade_mid_day_preserves_usage_and_blocks_until_reset(db):
    user, _ = owner(db, PlanTier.PLUS)
    quota = QuotaService(db)
    usage = quota.get_daily_usage(user)
    usage.chat_turns = 80
    usage.live_call_seconds = 400
    usage.voice_plays = 20
    usage_id = usage.usage_id
    db.commit()
    user.plan = PlanTier.FREE
    db.commit()
    assert quota.get_daily_usage(user).usage_id == usage_id
    assert (usage.chat_turns, usage.live_call_seconds, usage.voice_plays) == (80, 400, 20)
    assert not quota.check_chat(user).allowed
    assert not quota.get_live_call_remaining_seconds(user).allowed
    assert not quota.check_voice_play(user).allowed


def test_memory_downgrade_keeps_existing_rows_and_blocks_only_new_capacity(db):
    user, legacy = owner(db, PlanTier.PLUS)
    db.add_all([
        Memory(
            legacy_id=legacy.legacy_id, memory_type=MemoryType.ATOMIC,
            category="life", title=f"Memory {index}", summary="Canonical",
            normalized_fingerprint=f"downgrade-{index}",
            review_status=MemoryReviewStatus.APPROVED,
        ) for index in range(200)
    ])
    db.commit()
    assert QuotaService(db).check_memory_capacity(user, legacy.legacy_id).allowed
    user.plan = PlanTier.FREE
    db.commit()
    decision = QuotaService(db).check_memory_capacity(user, legacy.legacy_id)
    assert not decision.allowed and decision.used == 200 and decision.limit == 100
    assert db.query(Memory).filter_by(legacy_id=legacy.legacy_id).count() == 200


def test_quota_exempt_wins_over_plan_and_over_limit_counts(db):
    user, legacy = owner(db, PlanTier.FREE, exempt=True)
    quota = QuotaService(db)
    usage = quota.get_daily_usage(user)
    usage.chat_turns = 9999
    usage.live_call_seconds = 9999
    usage.voice_plays = 9999
    db.add_all([
        Memory(
            legacy_id=legacy.legacy_id, memory_type=MemoryType.ATOMIC,
            category="life", title=f"Memory {index}", summary="Canonical",
            normalized_fingerprint=f"admin-{index}",
            review_status=MemoryReviewStatus.APPROVED,
        ) for index in range(101)
    ])
    db.commit()
    assert quota.check_chat(user).allowed
    assert quota.get_live_call_remaining_seconds(user).allowed
    assert quota.check_voice_play(user).allowed
    assert quota.check_memory_capacity(user, legacy.legacy_id).allowed


@pytest.mark.parametrize("schema", [UserCreate, UserSettingsCreate])
@pytest.mark.parametrize("field,value", [("plan", "pro"), ("quota_exempt", True)])
def test_public_user_inputs_reject_server_controlled_entitlements(schema, field, value):
    values = (
        {
            "full_name": "Ordinary User",
            "email": "ordinary@example.test",
            "accepted_terms": True,
        }
        if schema is UserCreate else {}
    )
    values[field] = value
    with pytest.raises(ValidationError):
        schema.model_validate(values)


def test_authenticated_user_response_reads_plan_but_never_exemption():
    response = UserResponse.model_validate({
        "user_id": 1, "full_name": "Plan User", "email": "plan@example.com",
        "plan": "plus",
        "created_at": datetime.now(timezone.utc),
    })
    assert response.plan == PlanTier.PLUS
    assert "quota_exempt" not in response.model_dump()
