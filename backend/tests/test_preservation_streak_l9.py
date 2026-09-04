from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models.legacy import Legacy
from app.models.progress import BuilderActivity
from app.models.user import User
from app.services.progression import local_date, record_builder_activity, streak_summary
from app.services.memory import CanonicalEdit, MemoryAnalysis, MemoryCandidate
from tests.conftest import register_user
from tests.test_legacy_persona_l6 import active_legacy, headers


def make_legacies(db):
    owner = User(full_name="Owner", email="l9-owner@example.test", password_hash="x", is_verified=True)
    collaborator = User(full_name="Arya", email="l9-collab@example.test", password_hash="x", is_verified=True)
    db.add_all([owner, collaborator]); db.flush()
    pallavi = Legacy(owner_user_id=owner.id, subject_name="Pallavi", relationship_to_owner="mother", is_self=False, setup_status="active")
    prathamesh = Legacy(owner_user_id=owner.id, subject_name="Prathamesh", relationship_to_owner="self", is_self=True, setup_status="active")
    db.add_all([pallavi, prathamesh]); db.commit()
    return owner, collaborator, pallavi, prathamesh


def test_first_same_day_next_day_missed_day_and_longest_streak(test_context):
    _client, sessions, _codes, _provider = test_context
    with sessions() as db:
        owner, _collab, legacy, _other = make_legacies(db)
        day = date(2026, 9, 1)
        first = record_builder_activity(db, user_id=owner.id, legacy_id=legacy.id, activity_type="new", memory_id=None, activity_date=day)
        assert first.was_first_today is True
        summary = streak_summary(db, legacy.id, day)
        assert summary["current_streak_days"] == 1 and summary["today_completed"] is True
        second = record_builder_activity(db, user_id=owner.id, legacy_id=legacy.id, activity_type="enrich", memory_id=None, activity_date=day)
        assert second.was_first_today is False and second.contribution_count == 2
        assert streak_summary(db, legacy.id, day)["current_streak_days"] == 1
        record_builder_activity(db, user_id=owner.id, legacy_id=legacy.id, activity_type="correct", memory_id=None, activity_date=day + timedelta(days=1))
        assert streak_summary(db, legacy.id, day + timedelta(days=1))["current_streak_days"] == 2
        missed = streak_summary(db, legacy.id, day + timedelta(days=3))
        assert missed["current_streak_days"] == 0 and missed["longest_streak_days"] == 2
        record_builder_activity(db, user_id=owner.id, legacy_id=legacy.id, activity_type="new", memory_id=None, activity_date=day + timedelta(days=3))
        restarted = streak_summary(db, legacy.id, day + timedelta(days=3))
        assert restarted["current_streak_days"] == 1 and restarted["longest_streak_days"] == 2


def test_owner_and_collaborator_share_one_daily_row_and_provenance(test_context):
    _client, sessions, _codes, _provider = test_context
    with sessions() as db:
        owner, collaborator, legacy, _other = make_legacies(db)
        day = date(2026, 9, 4)
        record_builder_activity(db, user_id=owner.id, legacy_id=legacy.id, activity_type="new", memory_id=None, activity_date=day)
        activity = record_builder_activity(db, user_id=collaborator.id, legacy_id=legacy.id, activity_type="explicit_save", memory_id=None, activity_date=day)
        assert db.scalar(select(func.count(BuilderActivity.id))) == 1
        assert activity.contribution_count == 2
        assert activity.first_contributor_user_id == owner.id and activity.last_contributor_user_id == collaborator.id
        assert streak_summary(db, legacy.id, day)["current_streak_days"] == 1


def test_streaks_are_isolated_per_legacy_for_same_account(test_context):
    _client, sessions, _codes, _provider = test_context
    with sessions() as db:
        owner, _collab, pallavi, prathamesh = make_legacies(db)
        day = date(2026, 9, 4)
        record_builder_activity(db, user_id=owner.id, legacy_id=pallavi.id, activity_type="story", memory_id=None, activity_date=day)
        assert streak_summary(db, pallavi.id, day)["today_completed"] is True
        untouched = streak_summary(db, prathamesh.id, day)
        assert untouched["today_completed"] is False and untouched["current_streak_days"] == 0
        record_builder_activity(db, user_id=owner.id, legacy_id=prathamesh.id, activity_type="new", memory_id=None, activity_date=day - timedelta(days=1))
        assert streak_summary(db, pallavi.id, day)["current_streak_days"] == 1
        assert streak_summary(db, prathamesh.id, day)["current_streak_days"] == 1


def test_timezone_uses_stable_local_calendar_boundaries():
    instant = datetime(2026, 9, 4, 23, 30, tzinfo=timezone.utc)
    assert local_date("Europe/Berlin", instant) == date(2026, 9, 5)
    assert local_date("America/New_York", instant) == date(2026, 9, 4)
    assert local_date("not/a-zone", instant) == date(2026, 9, 4)


def test_next_milestone_and_historical_activity_survive_current_memory_changes(test_context):
    _client, sessions, _codes, _provider = test_context
    with sessions() as db:
        owner, _collab, legacy, _other = make_legacies(db)
        start = date(2026, 8, 29)
        for offset in range(7):
            record_builder_activity(db, user_id=owner.id, legacy_id=legacy.id, activity_type="new", memory_id=None, activity_date=start + timedelta(days=offset))
        summary = streak_summary(db, legacy.id, start + timedelta(days=6))
        assert summary["current_streak_days"] == 7 and summary["longest_streak_days"] == 7 and summary["next_milestone"] == 14
        assert summary["last_meaningful_activity_date"] == (start + timedelta(days=6)).isoformat()


def test_duplicate_and_noop_edit_do_not_count_but_real_edit_and_delete_do(test_context):
    client, sessions, codes, provider = test_context
    owner = register_user(client, codes, email="l9-mutations@example.com")
    conversation = active_legacy(client, sessions, owner)
    source = "Pallavi loved jasmine flowers."
    provider.memory_provider.analyses[source] = MemoryAnalysis(source_language="english", normalized_query=source, memories=[MemoryCandidate(canonical_text=source, category="preference", confidence=.98)])
    url = f"/api/v1/conversations/{conversation['id']}/messages/stream?legacy_id={conversation['legacy_id']}&timezone=Europe/Berlin"
    assert client.post(url, json={"content": source}, headers=headers(owner)).status_code == 200
    assert client.post(url, json={"content": source}, headers=headers(owner)).status_code == 200
    memory = client.get(f"/api/v1/memories?legacy_id={conversation['legacy_id']}", headers=headers(owner)).json()[0]
    provider.memory_provider.canonical_edits[source] = CanonicalEdit(canonical_text=source, source_language="english", entities=[])
    edit_url = f"/api/v1/memories/{memory['id']}?legacy_id={conversation['legacy_id']}&timezone=Europe/Berlin"
    assert client.patch(edit_url, json={"canonical_text": source}, headers=headers(owner)).status_code == 200
    with sessions() as db:
        activity = db.scalar(select(BuilderActivity).where(BuilderActivity.legacy_id == conversation["legacy_id"]))
        assert activity.contribution_count == 1
    changed = "Pallavi especially loved jasmine flowers."
    provider.memory_provider.canonical_edits[changed] = CanonicalEdit(canonical_text=changed, source_language="english", entities=[])
    assert client.patch(edit_url, json={"canonical_text": changed}, headers=headers(owner)).status_code == 200
    assert client.delete(edit_url, headers=headers(owner)).status_code == 204
    with sessions() as db:
        activity = db.scalar(select(BuilderActivity).where(BuilderActivity.legacy_id == conversation["legacy_id"]))
        assert activity.contribution_count == 3 and activity.activity_date is not None
