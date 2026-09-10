"""Shadow usage only: no decision in this module may reject product work.

Product writes own their existing transaction boundaries. Small savepoints let
optional accounting fail independently, while durable turns permit repair.
No provider calls, private content, refunds of aggregate counters, or checkout.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import logging

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import get_settings
from app.models.plan_usage import PlanEntitlement, PlanTrackingState, PlanUsage, PlanVoiceInterval

VERSION = "2026-09-10-v1"
LIMITS = {
    "free": dict(rya_text=40, legacy_text=40, rya_voice_ms=60_000, legacy_voice_ms=60_000, owned_legacies=1, storage_bytes=100_000_000),
    "plus": dict(rya_text=120, legacy_text=120, rya_voice_ms=180_000, legacy_voice_ms=180_000, owned_legacies=3, storage_bytes=1_000_000_000),
    "pro": dict(rya_text=400, legacy_text=400, rya_voice_ms=600_000, legacy_voice_ms=600_000, owned_legacies=10, storage_bytes=5_000_000_000),
}
logger = logging.getLogger("app.plans")


def warn(event):
    try:
        logger.warning(event)
    except Exception:
        pass  # Optional accounting/logging must not become a product failure.


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def now():
    return datetime.now(timezone.utc)


def entitlement(db, user_id):
    row = db.get(PlanEntitlement, user_id)
    plan = row.plan if row and row.plan in LIMITS else "free"
    return plan, bool(row and row.quota_exempt)


def _insert(db, model):
    return (pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert)(model)


def _safe(db, function, *args, **kwargs):
    if not get_settings().plans_tracking_enabled:
        return
    # Flush existing product work outside our catch: its own failure must still
    # follow the existing route's rollback/error contract.
    db.flush()
    try:
        with db.begin_nested():
            previous = None
            if db.get_bind().dialect.name == "postgresql":
                previous = db.execute(text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")).one()
                db.execute(text("SELECT set_config('lock_timeout','250ms',true), set_config('statement_timeout','500ms',true)"))
            function(db, *args, **kwargs)
            db.flush()
            if previous is not None:
                db.execute(text("SELECT set_config('lock_timeout',:lock,true), set_config('statement_timeout',:statement,true)"),
                           {"lock":previous[0], "statement":previous[1]})
    except Exception:
        # A savepoint rollback can expire state synchronized by an earlier Core
        # UPDATE. Reload from the still-valid outer transaction before callers
        # inspect turn ownership or session generations.
        db.expire_all()
        # Never log exception text/SQL, which could expose identifiers/content.
        warn("plan_shadow_write_degraded")


def _put(db, *, key, user_id, feature, day, amount=0, reserved=0, released=0, state="completed"):
    statement = _insert(db, PlanUsage).values(operation_key=key, user_id=user_id,
        feature=feature, usage_day=day, amount=amount, reserved=reserved, released=released,
        state=state, plan_version=VERSION, observed_at=now())
    db.execute(statement.on_conflict_do_update(index_elements=[PlanUsage.operation_key],
        set_={name: getattr(statement.excluded, name) for name in
              ("amount", "reserved", "released", "state", "observed_at")},
        # Reconciliation must not regress a terminal receipt to pending.
        where=(PlanUsage.state == "pending") | (PlanUsage.state == statement.excluded.state)))


def _turn(db, turn):
    if turn.input_mode == "realtime_voice":
        return  # Voice minutes, never an additional text charge.
    tracking = db.get(PlanTrackingState, "shadow")
    if not tracking or utc(turn.accepted_at) < utc(tracking.started_at):
        return
    state = turn.state if turn.state in {"completed", "failed", "interrupted"} else "pending"
    identity = hashlib.sha256(f"{turn.id}:{utc(turn.accepted_at).isoformat()}".encode()).hexdigest()
    _put(db, key="turn:" + identity, user_id=turn.actor_user_id, feature=turn.mode + "_text",
         day=utc(turn.accepted_at).date(), amount=int(state == "completed"),
         reserved=int(state == "pending"), released=int(state in {"failed", "interrupted"}), state=state)
    from app.services.plan_enforcement import cutover
    start = cutover(db)
    if start and utc(turn.accepted_at) >= start:
        _put(db, key="quota:turn:"+identity, user_id=turn.actor_user_id, feature="quota_"+turn.mode+"_text",
             day=utc(turn.accepted_at).date(), amount=int(state == "completed"),
             reserved=int(state == "pending"), released=int(state in {"failed", "interrupted"}), state=state)


def track_turn(db, turn):
    from app.services.plan_enforcement import enabled
    if enabled():
        _turn(db, turn)  # Receipt and saved reply must agree atomically.
    else:
        _safe(db, _turn, turn)


def _segments(start, end):
    """Integer microseconds avoid one-second reconnect rounding penalties."""
    cursor, end = utc(start), utc(end)
    while cursor < end:
        boundary = datetime.combine(cursor.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        stop = min(end, boundary)
        delta = stop - cursor
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        yield cursor.date(), micros
        cursor = stop


def _voice_receipts(db, interval):
    # Store microseconds so short reconnect intervals retain their fractions.
    for day, micros in _segments(interval.started_at, interval.observed_until):
        _put(db, key=f"voice:{interval.session_id}:{interval.generation}:{day}",
             user_id=interval.user_id, feature=interval.feature, day=day,
             amount=micros, state="completed" if interval.ended_at else "pending")
    from app.services.plan_enforcement import voice_receipts
    voice_receipts(db, interval)


def _voice(db, live, timestamp, *, ready=False, ending=False, recovered=False):
    interval = db.get(PlanVoiceInterval, (live.id, live.connection_generation))
    timestamp = min(utc(timestamp), utc(live.expires_at), utc(live.auth_expires_at))
    if interval is None:
        if not ready and (ending or live.state != "connected"):
            return  # No ready event => no invented connected duration.
        interval = PlanVoiceInterval(session_id=live.id, generation=live.connection_generation,
            user_id=live.actor_user_id, feature=live.mode + "_voice_us",
            started_at=timestamp, observed_until=timestamp, uncertain_tail=not ready)
        db.add(interval)
    if interval.ended_at is not None:
        return
    if not recovered:
        interval.observed_until = max(utc(interval.observed_until), timestamp)
    if ending:
        # On process loss retain confirmed time and disclose unknown tail, never
        # bill setup/reconnect grace or the entire remaining allowance.
        interval.ended_at = interval.observed_until
        interval.uncertain_tail = bool(interval.uncertain_tail or recovered)
    _voice_receipts(db, interval)


def track_voice(db, live, timestamp, **kwargs):
    from app.services.plan_enforcement import enabled
    if enabled():
        _voice(db, live, timestamp, **kwargs)
    else:
        _safe(db, _voice, live, timestamp, **kwargs)


def reconcile(db, *, batch_size=500):
    """Repair durable text receipts and orphaned voice intervals, bounded per pass.

    No provider work or turn retries. A terminal source deletion during a shadow
    storage outage cannot be reconstructed; operators must retain the degraded
    signal instead of claiming this ledger is complete under arbitrary outages.
    """
    from app.models.turn import ConversationTurn
    from app.models.realtime_session import RealtimeSession
    tracking = db.get(PlanTrackingState, "shadow", with_for_update=True)
    if not tracking:
        return {"turns": 0, "recovered_voice": 0}
    # Cursor cycles intentionally: turns seen pending must be revisited later.
    turns = db.scalars(select(ConversationTurn).where(
        ConversationTurn.id > tracking.cursor_turn_id,
        ConversationTurn.accepted_at >= tracking.started_at,
    ).order_by(ConversationTurn.id).limit(batch_size)).all()
    for turn in turns:
        _turn(db, turn)
    tracking.cursor_turn_id = turns[-1].id if len(turns) == batch_size else 0
    tracking.reconciled_at = now()
    db.commit()  # Release text receipts before acquiring any live actor lock.
    recovered = 0
    intervals = db.scalars(select(PlanVoiceInterval).where(PlanVoiceInterval.ended_at.is_(None))
                          .order_by(PlanVoiceInterval.started_at).limit(batch_size)).all()
    for interval in intervals:
        # Match the existing live-session lock order before deciding whether an
        # interval was orphaned. A sweeper must not race a ready/heartbeat write.
        from app.services.realtime_sessions import lock_actor
        lock_actor(db, interval.user_id)
        db.refresh(interval)
        if interval.ended_at is not None:
            db.commit()
            continue
        live = db.get(RealtimeSession, interval.session_id)
        if (live is None or live.connection_generation != interval.generation
                or live.state not in {"connecting", "connected"}
                or live.lease_expires_at is None or utc(live.lease_expires_at) <= now()):
            interval.ended_at = interval.observed_until
            interval.uncertain_tail = True
            _voice_receipts(db, interval)
            recovered += 1
        # Never hold multiple actor locks in a sweep: sharing/deletion may lock
        # owners and visitors in a different order in existing product flows.
        db.commit()
    return {"turns": len(turns), "recovered_voice": recovered}


def capacity(db, user_id):
    from app.models.legacy import Legacy
    from app.models.media_source import MediaSource, MediaArtifact
    from app.models.collaboration import LegacyCollaborator
    from app.models.viewer import LegacyViewerAccess
    from app.models.story import StoryVersion
    owned = select(Legacy.id).where(Legacy.owner_user_id == user_id)
    # Purge-pending originals still occupy retained physical storage. Never count
    # previews/derived artifacts, nor count the same source twice for its DP.
    stored = db.scalar(select(func.coalesce(func.sum(MediaArtifact.byte_size), 0))
        .where(MediaArtifact.legacy_id.in_(owned), MediaArtifact.kind == "original",
               MediaArtifact.state.in_(["available", "purge_pending"])))
    reserved = db.scalar(select(func.coalesce(func.sum(MediaSource.declared_size_bytes), 0))
        .where(MediaSource.legacy_id.in_(owned), MediaSource.state == "uploading",
               MediaSource.upload_expires_at > now()))
    def count(model, condition):
        return db.scalar(select(func.count()).select_from(model).where(condition)) or 0
    return {"owned_legacies": count(Legacy, Legacy.owner_user_id == user_id),
            "storage_bytes": int(stored or 0), "storage_reserved_bytes": int(reserved or 0),
            "collaborator_records": count(LegacyCollaborator, LegacyCollaborator.legacy_id.in_(owned)),
            "viewer_records": count(LegacyViewerAccess, LegacyViewerAccess.legacy_id.in_(owned)),
            "story_versions_retained": count(StoryVersion, StoryVersion.legacy_id.in_(owned))}


def snapshot(db, user_id, *, timestamp=None):
    timestamp = utc(timestamp or now())
    plan, exempt = entitlement(db, user_id)
    limits = LIMITS[plan]
    rows = db.execute(select(PlanUsage.feature, func.sum(PlanUsage.amount), func.sum(PlanUsage.reserved),
                            func.sum(PlanUsage.released)).where(PlanUsage.user_id == user_id,
                            PlanUsage.usage_day == timestamp.date()).group_by(PlanUsage.feature)).all()
    from app.services.plan_enforcement import enabled, cutover
    enforcing = enabled()
    totals = {feature: (int(amount), int(reserved), int(released)) for feature, amount, reserved, released in rows}
    daily = {}
    for feature in ("rya_text", "legacy_text", "rya_voice_ms", "legacy_voice_ms"):
        stored_feature = ("quota_" if enforcing else "")+feature.replace("_ms", "_us")
        used, reserved, released = totals.get(stored_feature, (0, 0, 0))
        divisor = 1000 if feature.endswith("_ms") else 1
        used = used / divisor if divisor != 1 else used
        daily[feature] = {"used": used, "reserved": reserved, "released": released,
            "limit": None if exempt else limits[feature],
            "remaining": None if exempt else max(0, limits[feature] - used - reserved),
            "would_block_next": not exempt and used + reserved >= limits[feature]}
    current = capacity(db, user_id)
    caps = {feature: {"used": current[feature], "limit": None if exempt else limits[feature],
        "would_block_next": not exempt and current[feature] +
        (current["storage_reserved_bytes"] if feature == "storage_bytes" else 0) >= limits[feature]}
        for feature in ("owned_legacies", "storage_bytes")}
    tracking = db.get(PlanTrackingState, "shadow")
    unknown = db.scalar(select(func.count()).select_from(PlanVoiceInterval).where(
        PlanVoiceInterval.user_id == user_id, PlanVoiceInterval.uncertain_tail.is_(True),
        PlanVoiceInterval.observed_until >= datetime.combine(timestamp.date(), datetime.min.time(), tzinfo=timezone.utc)))
    from app.models.realtime_session import RealtimeSession
    missing_voice = 0
    if tracking:
        missing_voice = db.scalar(select(func.count()).select_from(RealtimeSession).where(
            RealtimeSession.actor_user_id == user_id,
            RealtimeSession.connected_at >= tracking.started_at,
            ~select(PlanVoiceInterval.session_id).where(PlanVoiceInterval.session_id == RealtimeSession.id).exists())) or 0
    return {"mode": "enforced" if enforcing else "shadow" if get_settings().plans_tracking_enabled else "off", "enforcement_enabled": enforcing,
        "enforcement_since": cutover(db).isoformat() if cutover(db) else None,
        "plan": plan, "quota_exempt": exempt, "plan_version": VERSION,
        "resets_at": datetime.combine(timestamp.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).isoformat(),
        "tracking_since": utc(tracking.started_at).isoformat() if tracking else None,
        "reconciled_at": utc(tracking.reconciled_at).isoformat() if tracking and tracking.reconciled_at else None,
        "daily": daily, "capacity": caps, "storage_reserved_bytes": current["storage_reserved_bytes"],
        "auxiliary_daily": {k: {"completed": v[0], "failed": v[2]} for k, v in totals.items()
                            if k.endswith("_requests") or k == "voice_cache_hits"},
        "auxiliary_retained_counts": {k: v for k, v in current.items() if k.endswith("records") or k.endswith("retained")},
        "uncertain_voice_intervals": unknown or 0, "untracked_connected_sessions": missing_voice, "memory_limit": None,
        "memory_limit_decided": False, "paid_activation_available": False}
