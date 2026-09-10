from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.models.conversation import Conversation
from app.models.plan_usage import PlanEntitlement, PlanTrackingState, PlanUsage, PlanVoiceInterval
from app.models.turn import ConversationTurn
from app.models.user import User
from app.services import plan_usage as plans
from app.services.security import create_access_token
from tests.test_conversation_turns_l14 import seed, send


@pytest.fixture
def shadow(test_context, monkeypatch):
    result = seed(test_context)
    monkeypatch.setattr(get_settings(), "plans_tracking_enabled", True)
    with result[1].begin() as db:
        db.add(PlanTrackingState(name="shadow", started_at=plans.now() - timedelta(days=1)))
    return result


def ledger(sessions):
    with sessions() as db:
        return list(db.scalars(select(PlanUsage)).all())


@pytest.mark.parametrize("streaming", [False, True])
def test_completed_replay_and_deletion_do_not_double_charge(shadow, streaming):
    client, sessions, provider, cid, actor, headers, _ = shadow
    response = send(client, cid, headers, "owner", "A fictional garden memory.", streaming, client_turn_id="one")
    assert response.status_code == (200 if streaming else 201)
    repeat = send(client, cid, headers, "owner", "A fictional garden memory.", False, client_turn_id="one")
    assert repeat.status_code == 201
    rows = ledger(sessions)
    assert len(rows) == 1 and rows[0].amount == 1 and rows[0].reserved == 0 and rows[0].state == "completed"
    with sessions() as db:
        plans.reconcile(db)
        plans.reconcile(db)
    assert len(ledger(sessions)) == 1
    assert client.delete(f"/api/v1/conversations/{cid}?legacy_id=1", headers=headers).status_code == 204
    assert ledger(sessions)[0].amount == 1


def test_failed_reply_releases_reservation_and_retry_is_not_reexecuted(shadow):
    client, sessions, provider, cid, actor, headers, _ = shadow
    provider.stream_error_after = 1
    response = send(client, cid, headers, "owner", "A fictional garden memory.", True, client_turn_id="failed")
    assert response.status_code == 200 and "event: error" in response.text
    rows = ledger(sessions)
    assert len(rows) == 1 and rows[0].amount == 0 and rows[0].reserved == 0 and rows[0].released == 1
    assert rows[0].state == "failed"
    assert send(client, cid, headers, "owner", "A fictional garden memory.", False, client_turn_id="failed").status_code == 409
    assert ledger(sessions)[0].released == 1


def test_shadow_does_not_block_over_limit_and_api_is_private(shadow):
    client, sessions, provider, cid, actor, headers, _ = shadow
    with sessions.begin() as db:
        plans._put(db, key="test:forty", user_id=actor, feature="rya_text", day=plans.now().date(), amount=40)
    response = send(client, cid, headers, "owner", "A fictional garden memory.", False, client_turn_id="forty-one")
    assert response.status_code == 201
    usage = client.get("/api/v1/plans/usage", headers=headers)
    assert usage.status_code == 200 and usage.headers["cache-control"] == "private, no-store"
    body = usage.json()
    assert body["daily"]["rya_text"]["used"] == 41
    assert body["daily"]["rya_text"]["would_block_next"] is True
    assert body["daily"]["legacy_text"]["used"] == 0
    assert body["enforcement_enabled"] is False
    assert client.get("/api/v1/plans/usage").status_code == 401
    other = client.get("/api/v1/plans/usage?user_id=1", headers={"Authorization": "Bearer " + create_access_token(4)})
    assert other.json()["daily"]["rya_text"]["used"] == 0
    assert client.post("/api/v1/plans/usage", json={"plan":"pro", "quota_exempt":True}, headers=headers).status_code == 405


def test_tracking_database_failure_does_not_break_chat_and_reconciles(shadow, monkeypatch):
    client, sessions, provider, cid, actor, headers, _ = shadow
    original = plans._put
    from app.services import turn_lifecycle
    original_link = turn_lifecycle.link_user
    def inspect_link(db, message):
        turn = db.get(ConversationTurn, db.info["active_turn_id"])
        assert turn.state == "streaming", (turn.state, "claim state")
        assert turn.claim_token == db.info["turn_claim_token"], "claim ownership changed"
        assert turn.user_message_id is None, "user already linked"
        return original_link(db, message)
    monkeypatch.setattr("app.api.routes.conversations.link_user", inspect_link)
    def broken(db, **kwargs):
        db.execute(text("SELECT * FROM deliberately_missing_plan_table"))
    monkeypatch.setattr(plans, "_put", broken)
    assert send(client, cid, headers, "owner", "A fictional garden memory.", False, client_turn_id="outage").status_code == 201
    assert ledger(sessions) == []
    monkeypatch.setattr(plans, "_put", original)
    with sessions() as db:
        plans.reconcile(db)
    assert ledger(sessions)[0].amount == 1


def test_unlimited_is_id_bound_and_still_measured(shadow):
    client, sessions, provider, cid, actor, headers, _ = shadow
    from scripts.plan_usage_admin import grant_testing_exemption, TESTING_EMAIL
    with sessions.begin() as db:
        db.get(User, actor).email = TESTING_EMAIL
    with sessions() as db:
        with pytest.raises(ValueError): grant_testing_exemption(db, 4)
        db.rollback()
        grant_testing_exemption(db, actor)
    assert send(client, cid, headers, "owner", "A fictional garden memory.", False, client_turn_id="exempt").status_code == 201
    with sessions() as db:
        result = plans.snapshot(db, actor)
        assert result["quota_exempt"] and result["daily"]["rya_text"]["used"] == 1
        assert result["daily"]["rya_text"]["limit"] is None
        assert not plans.snapshot(db, 4)["quota_exempt"]


def test_plan_definitions_and_utc_reset(shadow):
    _, sessions, _, _, actor, _, _ = shadow
    with sessions.begin() as db:
        db.add(PlanEntitlement(user_id=actor, plan="plus", quota_exempt=False))
    with sessions() as db:
        instant = datetime(2026, 9, 10, 23, 59, 59, tzinfo=timezone.utc)
        plans._put(db, key="yesterday", user_id=actor, feature="rya_text", day=instant.date(), amount=120)
        db.commit()
        before = plans.snapshot(db, actor, timestamp=instant)
        after = plans.snapshot(db, actor, timestamp=instant + timedelta(seconds=1))
        assert before["daily"]["rya_text"]["would_block_next"]
        assert after["daily"]["rya_text"]["used"] == 0
        assert before["resets_at"] == "2026-09-11T00:00:00+00:00"
        assert after["daily"]["rya_voice_ms"]["limit"] == 180000
        assert plans.LIMITS["pro"]["legacy_voice_ms"] == 600000


def fake_live(start, generation=1, mode="rya"):
    return SimpleNamespace(id="00000000-0000-0000-0000-000000000001", connection_generation=generation,
        actor_user_id=1, mode=mode, expires_at=start+timedelta(hours=1), auth_expires_at=start+timedelta(hours=1))


def test_voice_splits_midnight_and_excludes_reconnect_gaps(shadow):
    _, sessions, *_ = shadow
    start = datetime(2026, 9, 10, 23, 59, 59, 500000, tzinfo=timezone.utc)
    live = fake_live(start)
    with sessions.begin() as db:
        plans.track_voice(db, live, start, ready=True)
        plans.track_voice(db, live, start+timedelta(seconds=1.25), ending=True)
        plans.track_voice(db, live, start+timedelta(seconds=10), ending=True)  # repeated close
        live.connection_generation = 3
        plans.track_voice(db, live, start+timedelta(seconds=20), ready=True)
        plans.track_voice(db, live, start+timedelta(seconds=20.125), ending=True)
    rows = ledger(sessions)
    assert sum(x.amount for x in rows) == 1_375_000
    assert sum(x.amount for x in rows if x.usage_day == start.date()) == 500000
    assert all(x.feature == "rya_voice_us" for x in rows)


def test_failed_setup_and_recovered_tail_are_not_invented(shadow):
    _, sessions, *_ = shadow
    start = plans.now()
    live = fake_live(start)
    with sessions.begin() as db:
        plans.track_voice(db, live, start, ending=True)
        assert db.scalar(select(func.count()).select_from(PlanVoiceInterval)) == 0
        plans.track_voice(db, live, start, ready=True)
        plans.track_voice(db, live, start+timedelta(seconds=2))
        plans.track_voice(db, live, start+timedelta(seconds=20), ending=True, recovered=True)
    assert sum(x.amount for x in ledger(sessions)) == 2_000_000
    with sessions() as db:
        assert db.scalar(select(PlanVoiceInterval)).uncertain_tail


def test_auxiliary_telemetry_keeps_text_and_call_counters_separate(shadow):
    client, sessions, provider, cid, actor, headers, _ = shadow
    from app.services.plan_auxiliary import drain, pending
    while not pending.empty(): pending.get_nowait(); pending.task_done()
    assert client.post("/api/v1/voice/preview", json={"voice":"marin"}, headers=headers).status_code == 200
    assert client.post("/api/v1/voice/preview", json={"voice":"marin"}, headers=headers).status_code == 200
    with sessions() as db:
        assert drain(db) == 2
        result = plans.snapshot(db, actor)
        assert result["auxiliary_daily"]["voice_preview_requests"]["completed"] == 2
        assert result["auxiliary_daily"]["voice_cache_hits"]["completed"] == 1
        assert result["daily"]["rya_text"]["used"] == result["daily"]["rya_voice_ms"]["used"] == 0


@pytest.mark.parametrize("actor,role,prefix,feature", [
    (2,"collaborator","conversations","rya_text"),
    (3,"viewer","legacy-conversations","legacy_text"),
])
def test_shared_legacy_uses_actor_allowance_not_exempt_owner(shadow, actor, role, prefix, feature):
    client, sessions, *_ = shadow
    with sessions.begin() as db:
        db.add(PlanEntitlement(user_id=1, plan="free", quota_exempt=True))
    headers = {"Authorization":"Bearer "+create_access_token(actor)}
    created = client.post("/api/v1/"+prefix, json={"legacy_id":1}, headers=headers)
    assert created.status_code == 201
    cid = created.json()["id"]
    assert send(client,cid,headers,role,"A fictional garden memory.",False,client_turn_id="actor-turn").status_code == 201
    with sessions() as db:
        assert plans.snapshot(db, actor)["daily"][feature]["used"] == 1
        assert plans.snapshot(db, actor)["quota_exempt"] is False
        assert plans.snapshot(db, 1)["daily"][feature]["used"] == 0
