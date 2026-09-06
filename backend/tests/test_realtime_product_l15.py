"""The product entry gate cannot create chat/session state or bypass auth."""
import pytest
from sqlalchemy import func, select
from app.models.conversation import Conversation
from app.models.realtime_session import RealtimeSession
from tests.test_realtime_l15 import live, headers


@pytest.mark.parametrize("enabled", [False, True])
def test_capabilities_is_authenticated_read_only_and_tracks_feature_flag(live, monkeypatch, enabled):
    client, factory, _, settings = live
    monkeypatch.setattr(settings, "realtime_enabled", enabled)
    with factory() as db:
        before = (db.scalar(select(func.count()).select_from(Conversation)),
                  db.scalar(select(func.count()).select_from(RealtimeSession)))
    assert client.get("/api/v1/realtime/capabilities").status_code == 401
    response = client.get("/api/v1/realtime/capabilities", headers=headers())
    assert response.status_code == 200 and response.json() == {"enabled": enabled}
    with factory() as db:
        assert before == (db.scalar(select(func.count()).select_from(Conversation)),
                          db.scalar(select(func.count()).select_from(RealtimeSession)))
