"""Feature-specific plan limits and concurrency-safe usage accounting."""

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from enum import Enum
from threading import Lock, RLock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.memory import Legacy, Memory, MemoryReviewStatus
from app.models.quota import UserDailyUsage
from app.models.user import PlanTier, User


class QuotaFeature(str, Enum):
    CHAT = "chat"
    LIVE_CALL = "live_call"
    VOICE_PLAY = "voice_play"
    MEMORY = "memory"


@dataclass(frozen=True)
class PlanLimits:
    chat_daily: int
    live_call_seconds_daily: int
    voice_plays_daily: int
    memories_per_legacy: int


PLAN_LIMITS = {
    PlanTier.FREE: PlanLimits(40, 180, 10, 100),
    PlanTier.PLUS: PlanLimits(120, 540, 30, 300),
    PlanTier.PRO: PlanLimits(400, 1800, 100, 1000),
}


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    feature: QuotaFeature
    plan: PlanTier
    limit: int
    used: int
    remaining: int
    resets_at: datetime | None
    quota_exempt: bool


@dataclass(frozen=True)
class QuotaExceededDetail:
    error: str
    feature: QuotaFeature
    plan: PlanTier
    resets_at: datetime | None
    upgrade_available: bool = False


@dataclass(frozen=True)
class ChatQuotaReservation:
    """One atomically accepted Chat turn that can be refunded on failure."""

    decision: QuotaDecision
    usage_id: int | None
    reserved: bool


@dataclass(frozen=True)
class VoiceQuotaReservation:
    """One atomically accepted user-triggered Chat speech generation."""

    decision: QuotaDecision
    usage_id: int | None
    reserved: bool


@dataclass(frozen=True)
class LiveCallQuotaReservation:
    """The user's entire remaining Live Call allowance, held for one session."""

    decision: QuotaDecision
    usage_id: int | None
    reserved_seconds: int


def get_plan_limits(plan: PlanTier | str) -> PlanLimits:
    try:
        return PLAN_LIMITS[PlanTier(plan)]
    except (ValueError, KeyError):
        return PLAN_LIMITS[PlanTier.FREE]


def safe_timezone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return ZoneInfo("UTC")


class QuotaService:
    """Quota checks and atomic counter consumption; endpoint wiring comes later."""

    _DAILY_FIELDS = {
        QuotaFeature.CHAT: ("chat_turns", "chat_daily"),
        QuotaFeature.LIVE_CALL: ("live_call_seconds", "live_call_seconds_daily"),
        QuotaFeature.VOICE_PLAY: ("voice_plays", "voice_plays_daily"),
    }
    _memory_locks_guard = Lock()
    _memory_locks: dict[int, RLock] = {}

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def local_date(user: User, now: datetime | None = None) -> date:
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return instant.astimezone(safe_timezone(user.timezone)).date()

    @staticmethod
    def next_reset_at(user: User, now: datetime | None = None) -> datetime:
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        zone = safe_timezone(user.timezone)
        next_day = instant.astimezone(zone).date().fromordinal(
            instant.astimezone(zone).date().toordinal() + 1
        )
        return datetime.combine(next_day, time.min, tzinfo=zone)

    def get_daily_usage(self, user: User, now: datetime | None = None) -> UserDailyUsage:
        usage_date = self.local_date(user, now)
        row = self.db.query(UserDailyUsage).filter_by(
            user_id=user.user_id, usage_date=usage_date
        ).first()
        if row is not None:
            return row
        try:
            with self.db.begin_nested():
                row = UserDailyUsage(user_id=user.user_id, usage_date=usage_date)
                self.db.add(row)
                self.db.flush()
            return row
        except IntegrityError:
            return self.db.query(UserDailyUsage).filter_by(
                user_id=user.user_id, usage_date=usage_date
            ).one()

    def _daily_decision(
        self, user: User, feature: QuotaFeature, now: datetime | None = None
    ) -> QuotaDecision:
        row = self.get_daily_usage(user, now)
        counter, limit_name = self._DAILY_FIELDS[feature]
        limit = getattr(get_plan_limits(user.plan), limit_name)
        used = getattr(row, counter)
        return QuotaDecision(
            allowed=bool(user.quota_exempt) or used < limit,
            feature=feature,
            plan=PlanTier(user.plan),
            limit=limit,
            used=used,
            remaining=max(0, limit - used),
            resets_at=self.next_reset_at(user, now),
            quota_exempt=bool(user.quota_exempt),
        )

    def check_chat(self, user: User, now: datetime | None = None) -> QuotaDecision:
        return self._daily_decision(user, QuotaFeature.CHAT, now)

    def check_voice_play(self, user: User, now: datetime | None = None) -> QuotaDecision:
        return self._daily_decision(user, QuotaFeature.VOICE_PLAY, now)

    def get_live_call_remaining_seconds(
        self, user: User, now: datetime | None = None
    ) -> QuotaDecision:
        return self._daily_decision(user, QuotaFeature.LIVE_CALL, now)

    def _consume(
        self, user: User, feature: QuotaFeature, amount: int, now: datetime | None = None
    ) -> QuotaDecision:
        if amount < 0:
            raise ValueError("Usage amount cannot be negative.")
        row = self.get_daily_usage(user, now)
        counter, limit_name = self._DAILY_FIELDS[feature]
        limit = getattr(get_plan_limits(user.plan), limit_name)
        if user.quota_exempt:
            return QuotaDecision(
                allowed=True, feature=feature, plan=PlanTier(user.plan),
                limit=limit, used=getattr(row, counter),
                remaining=max(0, limit - getattr(row, counter)),
                resets_at=self.next_reset_at(user, now), quota_exempt=True,
            )
        if amount == 0:
            return self._daily_decision(user, feature, now)
        result = self.db.execute(
            update(UserDailyUsage)
            .where(
                UserDailyUsage.usage_id == row.usage_id,
                getattr(UserDailyUsage, counter) + amount <= limit,
            )
            .values({counter: getattr(UserDailyUsage, counter) + amount, "updated_at": func.now()})
        )
        self.db.commit()
        refreshed = self.db.get(UserDailyUsage, row.usage_id)
        used = getattr(refreshed, counter)
        return QuotaDecision(
            allowed=bool(result.rowcount), feature=feature, plan=PlanTier(user.plan),
            limit=limit, used=used, remaining=max(0, limit - used),
            resets_at=self.next_reset_at(user, now), quota_exempt=bool(user.quota_exempt),
        )

    def reserve_chat(
        self, user: User, now: datetime | None = None
    ) -> ChatQuotaReservation:
        """Atomically reserve one Chat slot before expensive processing."""
        decision = self._consume(user, QuotaFeature.CHAT, 1, now)
        usage_id = None
        if not user.quota_exempt:
            usage_id = self.get_daily_usage(user, now).usage_id
        return ChatQuotaReservation(
            decision=decision,
            usage_id=usage_id,
            reserved=decision.allowed and not user.quota_exempt,
        )

    def refund_chat(self, reservation: ChatQuotaReservation) -> bool:
        """Atomically release a previously reserved Chat slot without underflow."""
        if not reservation.reserved or reservation.usage_id is None:
            return False
        result = self.db.execute(
            update(UserDailyUsage)
            .where(
                UserDailyUsage.usage_id == reservation.usage_id,
                UserDailyUsage.chat_turns > 0,
            )
            .values(
                chat_turns=UserDailyUsage.chat_turns - 1,
                updated_at=func.now(),
            )
        )
        self.db.commit()
        return bool(result.rowcount)

    def reserve_voice_play(
        self, user: User, now: datetime | None = None
    ) -> VoiceQuotaReservation:
        decision = self._consume(user, QuotaFeature.VOICE_PLAY, 1, now)
        usage_id = None
        if not user.quota_exempt:
            usage_id = self.get_daily_usage(user, now).usage_id
        return VoiceQuotaReservation(
            decision=decision,
            usage_id=usage_id,
            reserved=decision.allowed and not user.quota_exempt,
        )

    def refund_voice_play(self, reservation: VoiceQuotaReservation) -> bool:
        if not reservation.reserved or reservation.usage_id is None:
            return False
        result = self.db.execute(
            update(UserDailyUsage)
            .where(
                UserDailyUsage.usage_id == reservation.usage_id,
                UserDailyUsage.voice_plays > 0,
            )
            .values(
                voice_plays=UserDailyUsage.voice_plays - 1,
                updated_at=func.now(),
            )
        )
        self.db.commit()
        return bool(result.rowcount)

    def reserve_live_call(self, user: User, now: datetime | None = None) -> LiveCallQuotaReservation:
        """Atomically reserve all remaining seconds so parallel calls cannot oversubscribe."""
        decision = self.get_live_call_remaining_seconds(user, now)
        if user.quota_exempt:
            return LiveCallQuotaReservation(decision, None, 0)
        if not decision.allowed or decision.remaining <= 0:
            return LiveCallQuotaReservation(decision, None, 0)
        row = self.get_daily_usage(user, now)
        result = self.db.execute(
            update(UserDailyUsage)
            .where(
                UserDailyUsage.usage_id == row.usage_id,
                UserDailyUsage.live_call_seconds == decision.used,
            )
            .values(live_call_seconds=decision.limit, updated_at=func.now())
        )
        self.db.commit()
        if not result.rowcount:
            blocked = self.get_live_call_remaining_seconds(user, now)
            return LiveCallQuotaReservation(blocked, None, 0)
        return LiveCallQuotaReservation(decision, row.usage_id, decision.remaining)

    def finalize_live_call(self, usage_id: int | None, reserved_seconds: int, used_seconds: int) -> bool:
        """Refund the unused reservation. The session store makes this call idempotent."""
        if usage_id is None or reserved_seconds <= 0:
            return False
        used = min(reserved_seconds, max(0, int(used_seconds)))
        refund = reserved_seconds - used
        if refund <= 0:
            return False
        result = self.db.execute(
            update(UserDailyUsage)
            .where(
                UserDailyUsage.usage_id == usage_id,
                UserDailyUsage.live_call_seconds >= refund,
            )
            .values(
                live_call_seconds=UserDailyUsage.live_call_seconds - refund,
                updated_at=func.now(),
            )
        )
        self.db.commit()
        return bool(result.rowcount)

    def consume_chat(self, user: User, now: datetime | None = None) -> QuotaDecision:
        return self._consume(user, QuotaFeature.CHAT, 1, now)

    def consume_voice_play(self, user: User, now: datetime | None = None) -> QuotaDecision:
        return self._consume(user, QuotaFeature.VOICE_PLAY, 1, now)

    def consume_live_call_seconds(
        self, user: User, seconds: int, now: datetime | None = None
    ) -> QuotaDecision:
        return self._consume(user, QuotaFeature.LIVE_CALL, seconds, now)

    def check_memory_capacity(self, user: User, legacy_id: int) -> QuotaDecision:
        owned = self.db.query(Legacy.legacy_id).filter(
            Legacy.legacy_id == legacy_id, Legacy.owner_user_id == user.user_id
        ).scalar()
        if owned is None:
            raise ValueError("Legacy does not belong to the user.")
        used = self.db.query(Memory.memory_id).filter(
            Memory.legacy_id == legacy_id,
            Memory.review_status == MemoryReviewStatus.APPROVED,
        ).count()
        limit = get_plan_limits(user.plan).memories_per_legacy
        return QuotaDecision(
            allowed=bool(user.quota_exempt) or used < limit,
            feature=QuotaFeature.MEMORY, plan=PlanTier(user.plan), limit=limit,
            used=used, remaining=max(0, limit - used), resets_at=None,
            quota_exempt=bool(user.quota_exempt),
        )

    @classmethod
    def _memory_lock(cls, legacy_id: int) -> RLock:
        """Serialize local SQLite writers for one Legacy without a DB counter."""
        with cls._memory_locks_guard:
            return cls._memory_locks.setdefault(legacy_id, RLock())

    @contextmanager
    def memory_capacity_guard(self, user: User, legacy_id: int):
        """Lock, recount, then hold the capacity slot until the caller commits.

        PostgreSQL serializes writers by locking the owning Legacy row. SQLite's
        local server is protected by the per-Legacy process lock. Capacity always
        remains derived from canonical approved rows.
        """
        lock = self._memory_lock(legacy_id)
        with lock:
            if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
                # A waiting SQLite session may have opened a stale read snapshot
                # before it acquired the process lock. Start its guarded recount
                # from the latest committed state.
                self.db.rollback()
            owned = (
                self.db.query(Legacy)
                .filter(
                    Legacy.legacy_id == legacy_id,
                    Legacy.owner_user_id == user.user_id,
                )
                .with_for_update()
                .first()
            )
            if owned is None:
                raise ValueError("Legacy does not belong to the user.")
            yield self.check_memory_capacity(user, legacy_id)

    @staticmethod
    def exceeded_detail(decision: QuotaDecision) -> QuotaExceededDetail:
        return QuotaExceededDetail(
            error="quota_exceeded", feature=decision.feature, plan=decision.plan,
            resets_at=decision.resets_at,
        )
