"""Plan admission, separate from authentication and optional shadow telemetry.

Locks live only until the caller's admission commit, never during provider I/O.
The immutable cutover marker excludes unrestricted shadow-period usage.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib

from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.models.plan_usage import PlanTrackingState, PlanUsage
from app.services import plan_usage as usage


def enabled():
    settings = get_settings()
    return settings.plans_tracking_enabled and settings.plans_enforcement_enabled


def cutover(db):
    marker = db.get(PlanTrackingState, "enforcement")
    return usage.utc(marker.started_at) if marker else None


def unavailable():
    return HTTPException(503, detail={"code": "plan_check_unavailable",
        "message": "Usage could not be checked. Please try again; no allowance was used."},
        headers={"Retry-After": "5"})


def exceeded(feature, *, timestamp=None):
    timestamp = usage.utc(timestamp or usage.now())
    daily = feature.endswith(("_text", "_voice_ms"))
    reset = datetime.combine(timestamp.date()+timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    message = ("Your daily message allowance is used. It resets at midnight UTC." if feature.endswith("_text") else
               "Your daily call allowance is used. It resets at midnight UTC." if daily else
               "Your Legacy capacity is full. Existing Legacies remain accessible." if feature == "owned_legacies" else
               "The Legacy owner's storage allowance is full. Existing files remain accessible.")
    return HTTPException(429, detail={"code": "plan_limit_reached", "feature": feature,
        "message": message, "resets_at": reset.isoformat() if daily else None},
        headers={"Retry-After": str(max(1, int((reset-timestamp).total_seconds())))} if daily else None)


@contextmanager
def admission(db, user_id, feature):
    """Serialize competing admissions, with bounded database waits."""
    if not enabled():
        yield False
        return
    try:
        if cutover(db) is None:
            raise unavailable()
        previous = None
        if db.get_bind().dialect.name == "postgresql":
            previous = db.execute(text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")).one()
            db.execute(text("SELECT set_config('lock_timeout','2s',true), set_config('statement_timeout','3s',true)"))
            identity = int.from_bytes(hashlib.sha256(f"legarya-plan:{user_id}:{feature}".encode()).digest()[:8], "big", signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:identity)"), {"identity": identity})
        else:
            db.execute(update(PlanTrackingState).where(PlanTrackingState.name == "enforcement")
                       .values(cursor_turn_id=PlanTrackingState.cursor_turn_id))
        yield True
        if previous:
            db.execute(text("SELECT set_config('lock_timeout',:lock,true), set_config('statement_timeout',:statement,true)"),
                       {"lock": previous[0], "statement": previous[1]})
    except SQLAlchemyError:
        db.rollback()
        usage.warn("plan_admission_unavailable")
        raise unavailable() from None


def totals(db, user_id, feature, day):
    row = db.execute(select(func.coalesce(func.sum(PlanUsage.amount), 0), func.coalesce(func.sum(PlanUsage.reserved), 0))
        .where(PlanUsage.user_id == user_id, PlanUsage.feature == "quota_"+feature, PlanUsage.usage_day == day)).one()
    return int(row[0]), int(row[1])


def check_text(db, user_id, mode, timestamp=None):
    plan, exempt = usage.entitlement(db, user_id)
    if exempt:
        return
    feature = mode+"_text"
    timestamp = timestamp or usage.now()
    if sum(totals(db, user_id, feature, timestamp.date())) >= usage.LIMITS[plan][feature]:
        raise exceeded(feature, timestamp=timestamp)


def check_capacity(db, user_id, feature, amount=1):
    plan, exempt = usage.entitlement(db, user_id)
    if exempt:
        return
    current = usage.capacity(db, user_id)
    occupied = current[feature] + (current["storage_reserved_bytes"] if feature == "storage_bytes" else 0)
    if occupied+amount > usage.LIMITS[plan][feature]:
        raise exceeded(feature)


def voice_remaining(db, user_id, mode, timestamp=None):
    if not enabled():
        return None
    if cutover(db) is None:
        raise unavailable()
    plan, exempt = usage.entitlement(db, user_id)
    if exempt:
        return None
    used, _ = totals(db, user_id, mode+"_voice_us", usage.utc(timestamp or usage.now()).date())
    return max(0, usage.LIMITS[plan][mode+"_voice_ms"] - used/1000)


def check_voice(db, user_id, mode, timestamp=None):
    remaining = voice_remaining(db, user_id, mode, timestamp)
    if remaining is not None and remaining <= 0:
        raise exceeded(mode+"_voice_ms", timestamp=timestamp)
    return remaining


def voice_receipts(db, interval):
    start = cutover(db)
    if not start:
        return
    plan, exempt = usage.entitlement(db, interval.user_id)
    for day, micros in usage._segments(max(usage.utc(interval.started_at), start), interval.observed_until):
        key = f"quota:voice:{interval.session_id}:{interval.generation}:{day}"
        if enabled() and not exempt:
            other = db.scalar(select(func.coalesce(func.sum(PlanUsage.amount), 0)).where(
                PlanUsage.user_id == interval.user_id, PlanUsage.feature == "quota_"+interval.feature,
                PlanUsage.usage_day == day, PlanUsage.operation_key != key))
            # A scheduler/teardown delay never charges beyond the allowance.
            micros = min(micros, max(0, usage.LIMITS[plan][interval.feature.replace("_us", "_ms")]*1000-int(other)))
        usage._put(db, key=key, user_id=interval.user_id, feature="quota_"+interval.feature,
                   day=day, amount=micros, state="completed" if interval.ended_at else "pending")
