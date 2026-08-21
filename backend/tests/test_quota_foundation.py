"""Phase 11.1 quota foundation coverage."""

from datetime import date, datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.crud.user import UserCRUD, verify_password
from app.db import Base
from app.models.memory import Legacy, Memory, MemoryReviewStatus, MemoryType
from app.models.quota import UserDailyUsage
from app.models.user import PlanTier
from app.services.quota import PLAN_LIMITS, QuotaFeature, QuotaService, get_plan_limits
from scripts.seed_dev_admin import ADMIN_EMAIL, seed_dev_admin


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def make_user(db, email="quota@example.com", **values):
    user = UserCRUD.create_user(
        db, full_name="Quota User", email=email, password="Fixture123"
    )
    for key, value in values.items():
        setattr(user, key, value)
    db.commit()
    return user


def test_exact_central_plan_limits_and_multipliers():
    assert get_plan_limits(PlanTier.FREE) == PLAN_LIMITS[PlanTier.FREE]
    assert tuple(PLAN_LIMITS[PlanTier.FREE].__dict__.values()) == (40, 180, 10, 100)
    assert tuple(PLAN_LIMITS[PlanTier.PLUS].__dict__.values()) == (120, 540, 30, 300)
    assert tuple(PLAN_LIMITS[PlanTier.PRO].__dict__.values()) == (400, 1800, 100, 1000)
    for field in PLAN_LIMITS[PlanTier.FREE].__dict__:
        assert getattr(PLAN_LIMITS[PlanTier.PLUS], field) == 3 * getattr(PLAN_LIMITS[PlanTier.FREE], field)
        assert getattr(PLAN_LIMITS[PlanTier.PRO], field) == 10 * getattr(PLAN_LIMITS[PlanTier.FREE], field)


@pytest.mark.parametrize(
    ("zone", "instant", "local_day", "reset"),
    [
        ("UTC", "2026-08-21T23:30:00+00:00", date(2026, 8, 21), "2026-08-22T00:00:00+00:00"),
        ("Europe/Berlin", "2026-08-21T22:30:00+00:00", date(2026, 8, 22), "2026-08-23T00:00:00+02:00"),
        ("Asia/Kolkata", "2026-08-21T19:00:00+00:00", date(2026, 8, 22), "2026-08-23T00:00:00+05:30"),
        ("America/New_York", "2026-08-22T03:30:00+00:00", date(2026, 8, 21), "2026-08-22T00:00:00-04:00"),
    ],
)
def test_local_day_and_reset(db, zone, instant, local_day, reset):
    user = make_user(db, timezone=zone)
    now = datetime.fromisoformat(instant)
    assert QuotaService.local_date(user, now) == local_day
    assert QuotaService.next_reset_at(user, now).isoformat() == reset


def test_invalid_timezone_falls_back_to_utc(db):
    user = make_user(db, timezone="Not/A_Timezone")
    now = datetime.fromisoformat("2026-08-21T23:30:00+00:00")
    assert QuotaService.local_date(user, now) == date(2026, 8, 21)
    assert QuotaService.next_reset_at(user, now).isoformat() == "2026-08-22T00:00:00+00:00"


def test_dst_reset_is_next_local_midnight_not_24_hours(db):
    user = make_user(db, timezone="Europe/Berlin")
    now = datetime.fromisoformat("2026-03-28T23:30:00+01:00")
    reset = QuotaService.next_reset_at(user, now)
    assert reset.isoformat() == "2026-03-29T00:00:00+01:00"
    following = QuotaService.next_reset_at(user, datetime.fromisoformat("2026-03-29T00:30:00+01:00"))
    assert following.isoformat() == "2026-03-30T00:00:00+02:00"
    assert (following.astimezone(timezone.utc) - datetime.fromisoformat("2026-03-29T00:00:00+01:00").astimezone(timezone.utc)).total_seconds() == 23 * 3600


def test_daily_rows_are_reused_per_user_and_local_day(db):
    first = make_user(db, "first@example.com", timezone="UTC")
    second = make_user(db, "second@example.com", timezone="UTC")
    service = QuotaService(db)
    day_one = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    assert service.get_daily_usage(first, day_one) is service.get_daily_usage(first, day_one)
    tomorrow = service.get_daily_usage(first, datetime(2026, 8, 22, 12, tzinfo=timezone.utc))
    assert tomorrow.usage_date == date(2026, 8, 22)
    assert (tomorrow.chat_turns, tomorrow.live_call_seconds, tomorrow.voice_plays) == (0, 0, 0)
    assert service.get_daily_usage(second, day_one).usage_id != service.get_daily_usage(first, day_one).usage_id


def test_atomic_updates_accumulate_stop_at_limit_and_reject_negative(db):
    user = make_user(db)
    service = QuotaService(db)
    now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    assert service.consume_live_call_seconds(user, 100, now).allowed
    assert service.consume_live_call_seconds(user, 80, now).used == 180
    blocked = service.consume_live_call_seconds(user, 1, now)
    assert not blocked.allowed and blocked.used == 180
    with pytest.raises(ValueError):
        service.consume_live_call_seconds(user, -1, now)
    row = service.get_daily_usage(user, now)
    duplicate = UserDailyUsage(user_id=user.user_id, usage_date=row.usage_date)
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_chat_reservation_refund_is_atomic_and_cannot_underflow(db):
    user = make_user(db)
    service = QuotaService(db)
    reservation = service.reserve_chat(user)
    assert reservation.reserved and reservation.decision.used == 1
    assert service.refund_chat(reservation)
    assert service.get_daily_usage(user).chat_turns == 0
    assert not service.refund_chat(reservation)
    assert service.get_daily_usage(user).chat_turns == 0


def test_live_call_reserves_remaining_and_refunds_only_unused_seconds(db):
    user = make_user(db)
    service = QuotaService(db)
    assert service.consume_live_call_seconds(user, 170).allowed
    reservation = service.reserve_live_call(user)
    assert reservation.reserved_seconds == 10
    assert service.get_daily_usage(user).live_call_seconds == 180
    assert not service.reserve_live_call(user).decision.allowed
    assert service.finalize_live_call(
        reservation.usage_id, reservation.reserved_seconds, 4,
    )
    assert service.get_daily_usage(user).live_call_seconds == 174


def test_live_call_failed_start_refunds_all_and_exempt_never_consumes(db):
    normal = make_user(db, "normal-call@example.com")
    exempt = make_user(db, "exempt-call@example.com", quota_exempt=True)
    service = QuotaService(db)
    reservation = service.reserve_live_call(normal)
    assert service.finalize_live_call(
        reservation.usage_id, reservation.reserved_seconds, 0,
    )
    assert service.get_daily_usage(normal).live_call_seconds == 0
    exempt_reservation = service.reserve_live_call(exempt)
    assert exempt_reservation.decision.allowed
    assert exempt_reservation.reserved_seconds == 0
    assert service.get_daily_usage(exempt).live_call_seconds == 0


@pytest.mark.parametrize(("plan", "limit"), [(PlanTier.FREE, 10), (PlanTier.PLUS, 30), (PlanTier.PRO, 100)])
def test_voice_reservation_honors_plan_limit_and_never_exceeds_it(db, plan, limit):
    user = make_user(db, f"voice-{plan.value}@example.com", plan=plan)
    service = QuotaService(db)
    row = service.get_daily_usage(user)
    row.voice_plays = limit - 1
    db.commit()
    final = service.reserve_voice_play(user)
    rejected = service.reserve_voice_play(user)
    assert final.decision.allowed and final.decision.used == limit
    assert not rejected.decision.allowed
    assert service.get_daily_usage(user).voice_plays == limit


def test_voice_refund_and_feature_isolation(db):
    user = make_user(db, "voice-isolation@example.com")
    service = QuotaService(db)
    reservation = service.reserve_voice_play(user)
    assert service.refund_voice_play(reservation)
    row = service.get_daily_usage(user)
    row.voice_plays = get_plan_limits(user.plan).voice_plays_daily
    db.commit()
    assert not service.check_voice_play(user).allowed
    assert service.check_chat(user).allowed
    assert service.get_live_call_remaining_seconds(user).allowed


def test_concurrent_voice_requests_share_only_one_final_slot(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'voice-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as setup:
        user = make_user(setup, "voice-race@example.com")
        row = QuotaService(setup).get_daily_usage(user)
        row.voice_plays = 9
        setup.commit()
        user_id = user.user_id
    barrier = Barrier(2)

    def reserve() -> bool:
        with sessions() as db_session:
            race_user = db_session.get(type(user), user_id)
            barrier.wait()
            return QuotaService(db_session).reserve_voice_play(race_user).decision.allowed

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    with sessions() as verify:
        assert sorted(results) == [False, True]
        assert QuotaService(verify).get_daily_usage(verify.get(type(user), user_id)).voice_plays == 10


def test_exhausted_voice_quota_resets_on_the_next_user_local_day(db):
    user = make_user(db, "voice-new-day@example.com", timezone="Europe/Berlin")
    service = QuotaService(db)
    yesterday = datetime.fromisoformat("2026-08-21T20:00:00+00:00")
    today = datetime.fromisoformat("2026-08-22T20:00:00+00:00")
    row = service.get_daily_usage(user, yesterday)
    row.voice_plays = get_plan_limits(user.plan).voice_plays_daily
    db.commit()
    assert not service.check_voice_play(user, yesterday).allowed
    assert service.reserve_voice_play(user, today).decision.allowed
    assert service.get_daily_usage(user, today).voice_plays == 1


def test_quota_exempt_allows_all_features_and_memory(db):
    user = make_user(db, quota_exempt=True)
    legacy = Legacy(owner_user_id=user.user_id, display_name="A", relationship="Parent")
    db.add(legacy)
    db.commit()
    service = QuotaService(db)
    now = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    assert service.consume_chat(user, now).quota_exempt
    assert service.get_daily_usage(user, now).chat_turns == 0
    assert service.consume_live_call_seconds(user, 10_000, now).allowed
    assert service.reserve_voice_play(user, now).decision.allowed
    assert service.get_daily_usage(user, now).voice_plays == 0
    assert service.check_memory_capacity(user, legacy.legacy_id).allowed


def test_memory_capacity_counts_approved_canonical_memories_per_legacy(db):
    user = make_user(db)
    a = Legacy(owner_user_id=user.user_id, display_name="A", relationship="Parent")
    b = Legacy(owner_user_id=user.user_id, display_name="B", relationship="Friend")
    db.add_all([a, b]); db.flush()
    for legacy, count in ((a, 100), (b, 50)):
        db.add_all([
            Memory(
                legacy_id=legacy.legacy_id, memory_type=MemoryType.ATOMIC,
                category="life", title=f"Memory {index}", summary="Canonical",
                review_status=MemoryReviewStatus.APPROVED,
            ) for index in range(count)
        ])
    db.add(Memory(
        legacy_id=a.legacy_id, memory_type=MemoryType.ATOMIC, category="life",
        title="Candidate", summary="Not canonical", review_status=MemoryReviewStatus.CANDIDATE,
    ))
    db.commit()
    service = QuotaService(db)
    full = service.check_memory_capacity(user, a.legacy_id)
    available = service.check_memory_capacity(user, b.legacy_id)
    assert not full.allowed and full.used == 100 and full.resets_at is None
    assert available.allowed and available.used == 50


def test_admin_seed_is_secure_and_idempotent(db):
    password = "DummyFixture123"
    user, created = seed_dev_admin(db, password)
    assert created and user.email == ADMIN_EMAIL and user.is_verified and user.quota_exempt
    assert user.password_hash != password
    assert verify_password(password, user.password_hash)[0]
    same, created_again = seed_dev_admin(db, "DifferentFixture456")
    assert not created_again and same.user_id == user.user_id
    assert verify_password(password, same.password_hash)[0]
