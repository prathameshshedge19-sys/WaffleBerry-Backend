import asyncio
import logging
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.v1.live_call import record_live_call_operational_event
from app.schemas.live_call import LiveCallOperationalEvent
from app.services.live_call import live_call_sessions


def event(**overrides):
    values = {"event": "call_ended", "outcome": "user_ended", "duration_ms": 1234}
    values.update(overrides)
    return LiveCallOperationalEvent(**values)


def test_outcome_and_provider_taxonomy_is_closed_and_deterministic():
    for outcome in (
        "started", "startup_failed", "completed_normally", "transport_failed",
        "provider_failed", "session_expired", "user_ended",
    ):
        assert event(outcome=outcome).outcome == outcome
    for category in (
        "provider_rate_limited", "provider_quota_exhausted",
        "provider_transient_failure", "provider_unknown_failure", "unknown",
    ):
        assert event(failure_category=category).failure_category == category
    with pytest.raises(ValidationError):
        event(outcome="made_up")


def test_operational_contract_rejects_private_or_unbounded_fields():
    forbidden = (
        "transcript", "response", "memory_text", "tool_payload", "api_key",
        "client_secret", "sdp", "audio", "prompt", "relationship", "name",
    )
    for field in forbidden:
        with pytest.raises(ValidationError):
            LiveCallOperationalEvent(
                event="call_ended", outcome="user_ended", **{field: "private-value"}
            )


def test_memory_unsupported_is_counted_separately_from_error_and_timeout():
    value = event(
        memory_route_count=4, memory_supported_count=1,
        memory_unsupported_count=1, memory_error_count=1, memory_timeout_count=1,
    )
    assert value.memory_unsupported_count == 1
    assert value.memory_error_count == 1
    assert value.memory_timeout_count == 1


def test_operational_log_uses_safe_correlation_and_aggregate_fields(caplog):
    live_call_sessions.clear()
    session = live_call_sessions.create(
        user_id=7, legacy_id=9, legacy_name="Private Name", relationship="Private Relation",
        effective_voice="marin", engine="realtime", speech_renderer="realtime_native",
        realtime_capable=True,
    )
    caplog.set_level(logging.INFO, logger="app.api.v1.live_call")
    asyncio.run(record_live_call_operational_event(
        session.session_id,
        event(turn_started_count=2, turn_completed_count=1, turn_failed_count=1),
        SimpleNamespace(user_id=7),
    ))
    message = next(record.getMessage() for record in caplog.records
                   if "LIVE_CALL_OPERATIONAL" in record.getMessage())
    assert "event=call_ended" in message
    assert "turn_started_count=2" in message
    assert session.session_id not in message
    for private in ("Private Name", "Private Relation", "transport_token"):
        assert private not in message
    live_call_sessions.clear()
