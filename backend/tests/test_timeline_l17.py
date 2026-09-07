from datetime import date

from sqlalchemy import select

from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryStatus
from app.models.timeline import LifeEvent, LifeEventMemory, TimelineReviewState
from app.services.timeline import TimelineService, parse_date_semantics
from tests.conftest import register_user
from tests.test_memory import _headers, _named_legacy, _new_legacy


def _memory(db, legacy_id, text, category="life_event", memory_id=None):
    value = Memory(
        id=memory_id, legacy_id=legacy_id, canonical_text=text, category=category,
        source_language="english", source_excerpt=text, confidence=.95,
        status=MemoryStatus.ACTIVE.value, operation_type="explicit_save", explicit_save=True,
        normalized_fingerprint=f"fp-{memory_id or text[:12]}",
    )
    db.add(value); db.flush(); return value


def test_date_precision_semantics_are_explicit():
    assert parse_date_semantics("moved to Pune in 1998")["precision"] == "year"
    assert parse_date_semantics("graduated in June 1995")["precision"] == "month"
    assert parse_date_semantics("moved on 12 June 1998")["precision"] == "day"
    assert parse_date_semantics("during college")["precision"] == "life_period"
    assert parse_date_semantics("attended school")["precision"] == "unknown"


def test_canonical_memory_without_source_materializes_scoped_event(test_context):
    client, sessions, codes, _ = test_context
    auth = register_user(client, codes, email="timeline-owner@example.com")
    conversation = _named_legacy(client, sessions, auth, "Pallavi")
    with sessions() as db:
        memory = _memory(db, conversation["legacy_id"], "Pallavi moved to Pune in 1998.", memory_id=101)
        event = TimelineService(db).structure_memory(memory, actor_id=auth["user"]["id"])
        db.commit(); db.refresh(event)
        assert event.date_precision == "year"
        assert event.date_start == date(1998, 1, 1)
        assert event.date_label == "1998"
        assert db.scalar(select(LifeEventMemory).where(LifeEventMemory.memory_id == memory.id)) is not None
    response = client.get(f"/api/v1/timeline?legacy_id={conversation['legacy_id']}", headers=_headers(auth))
    assert response.status_code == 200
    assert response.json()[0]["date_label"] == "1998"
    assert response.json()[0]["source_count"] == 0


def test_same_event_support_consolidates_but_conflicting_years_are_visible(test_context):
    client, sessions, codes, _ = test_context
    auth = register_user(client, codes, email="timeline-conflict@example.com")
    conversation = _named_legacy(client, sessions, auth, "Pallavi")
    with sessions() as db:
        first = _memory(db, conversation["legacy_id"], "Pallavi moved to Pune in 1998.", memory_id=102)
        second = _memory(db, conversation["legacy_id"], "Our family shifted to Pune in 1998.", memory_id=103)
        service = TimelineService(db); service.structure_memory(first); service.structure_memory(second)
        db.commit()
        events = service.list_events(conversation["legacy_id"])
        assert len(events) == 1
        conflict = _memory(db, conversation["legacy_id"], "Pallavi moved to Pune in 1999.", memory_id=104)
        service.structure_memory(conflict); db.commit()
        events = service.list_events(conversation["legacy_id"])
        assert len(events) == 2 or any(event.review_state == TimelineReviewState.CONFLICT.value for event in events)


def test_foreign_legacy_event_route_is_not_disclosed(test_context):
    client, sessions, codes, _ = test_context
    auth = register_user(client, codes, email="timeline-isolation@example.com")
    first = _named_legacy(client, sessions, auth, "First")
    second_id = _new_legacy(client, sessions, auth, "Second")
    with sessions() as db:
        event = TimelineService(db).structure_memory(_memory(db, first["legacy_id"], "First moved to Pune in 1998.", memory_id=105))
        db.commit()
        event_id = event.id
    assert client.get(f"/api/v1/timeline/{event_id}?legacy_id={second_id}", headers=_headers(auth)).status_code == 404


def test_event_deletion_does_not_delete_memory(test_context):
    client, sessions, codes, _ = test_context
    auth = register_user(client, codes, email="timeline-delete@example.com")
    conversation = _named_legacy(client, sessions, auth, "Pallavi")
    with sessions() as db:
        event = TimelineService(db).structure_memory(_memory(db, conversation["legacy_id"], "Pallavi moved to Pune in 1998.", memory_id=106))
        db.commit(); event_id = event.id
    assert client.delete(f"/api/v1/timeline/{event_id}?legacy_id={conversation['legacy_id']}", headers=_headers(auth)).status_code == 204
    with sessions() as db:
        assert db.scalar(select(Memory).where(Memory.id == 106)).status == MemoryStatus.ACTIVE.value
