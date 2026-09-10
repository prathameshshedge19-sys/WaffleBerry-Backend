from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.plan_usage import PlanTrackingState, PlanVoiceInterval
from app.models.media_source import MediaArtifact
from app.models.user import User
from app.services import plan_usage as plans
from app.services.media_sources import MediaSourceService
from tests.test_realtime_l15 import live, create, authenticate, connected
from tests.test_media_sources_l16 import media_db, _reserve


@pytest.mark.parametrize("actor,mode", [(1,"rya"), (2,"rya"), (3,"legacy")])
def test_actual_live_lifecycle_still_works_above_shadow_allowance(live, monkeypatch, actor, mode):
    client, factory, fake, settings = live
    monkeypatch.setattr(settings, "plans_tracking_enabled", True)
    with factory.begin() as db:
        db.add(PlanTrackingState(name="shadow", started_at=plans.now()-timedelta(days=1)))
        plans._put(db, key="already-used", user_id=actor, feature=mode+"_voice_us", day=plans.now().date(), amount=61_000_000)
    response = create(live, actor, legacy_id=1, mode=mode)
    assert response.status_code == 201
    with authenticate(client, response.json()) as ws:
        connected(ws, response.json())
        ws.send_json({"type":"ping"})
        assert ws.receive_json() == {"type":"pong"}
        ws.send_json({"type":"end_call"})
        assert ws.receive_json() == {"type":"ended", "reason":"client_end"}
    with factory() as db:
        interval = db.scalar(select(PlanVoiceInterval))
        assert interval.user_id == actor and interval.ended_at is not None and not interval.uncertain_tail
        report = plans.snapshot(db, actor)
        assert report["daily"][mode+"_voice_ms"]["used"] >= 61000
        assert report["daily"][mode+"_text"]["used"] == 0
        assert report["enforcement_enabled"] is False


def test_collaborator_storage_reservations_and_physical_purge(media_db):
    factory, storage, settings = media_db
    source_id = _reserve(factory, storage, user_id=2, size=5)
    with factory() as db:
        assert plans.capacity(db, 1)["storage_reserved_bytes"] == 5
        assert plans.capacity(db, 2)["storage_reserved_bytes"] == 0
        source = MediaSourceService(storage=storage).receive(db, db.get(User, 2), 1, source_id, b"hello")
        assert plans.capacity(db, 1)["storage_bytes"] == 5
        assert plans.capacity(db, 1)["storage_reserved_bytes"] == 0
        # Idempotent content retry does not create a second stored original.
        MediaSourceService(storage=storage).receive(db, db.get(User, 2), 1, source_id, b"hello")
        assert plans.capacity(db, 1)["storage_bytes"] == 5
        MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_id)
        assert plans.capacity(db, 1)["storage_bytes"] == 5
        original = db.scalar(select(MediaArtifact).where(MediaArtifact.source_id == source_id, MediaArtifact.kind == "original"))
        original.state = "purged"
        db.commit()
        assert plans.capacity(db, 1)["storage_bytes"] == 0


def test_expired_upload_no_longer_reserves_capacity(media_db):
    from app.models.media_source import MediaSource
    factory, storage, settings = media_db
    source_id = _reserve(factory, storage, size=5)
    with factory.begin() as db:
        db.get(MediaSource, source_id).upload_expires_at = plans.now()-timedelta(seconds=1)
    with factory() as db:
        assert plans.capacity(db, 1)["storage_reserved_bytes"] == 0


def test_shadow_write_failure_does_not_break_live_call(live, monkeypatch):
    from sqlalchemy import text
    client, factory, fake, settings = live
    monkeypatch.setattr(settings, "plans_tracking_enabled", True)
    def unavailable(db, **kwargs):
        db.execute(text("SELECT * FROM missing_optional_voice_accounting"))
    monkeypatch.setattr(plans, "_put", unavailable)
    response = create(live)
    assert response.status_code == 201
    with authenticate(client, response.json()) as ws:
        connected(ws, response.json())
        ws.send_json({"type":"ping"})
        assert ws.receive_json() == {"type":"pong"}
        ws.send_json({"type":"end_call"})
        assert ws.receive_json() == {"type":"ended", "reason":"client_end"}
    assert fake.closed and fake.cancelled
