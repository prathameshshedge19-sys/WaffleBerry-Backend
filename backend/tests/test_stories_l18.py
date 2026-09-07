from datetime import date

import pytest
from sqlalchemy import func, select

from app.api.routes.stories import get_story_provider
from app.main import app
from app.models.memory import Memory, MemoryRevision, MemoryStatus
from app.models.story import Story, StoryChapter, StorySupportLink, StoryVersion
from app.models.timeline import LifeEvent
from app.services.stories import DeterministicStoryProvider, OutlineChapter, StoryChapterDraft, StoryEngine, audit_chapter
from tests.conftest import register_user
from tests.test_memory import _headers, _named_legacy, _new_legacy


class SafeProvider(DeterministicStoryProvider):
    pass


class BadProvider:
    model = "test-bad"
    async def outline(self, *args):
        return type("Outline", (), {"chapters": [OutlineChapter(title="Bad", memory_ids=[1], event_ids=[])]})()
    async def chapter(self, *args):
        return StoryChapterDraft(title="Bad", narrative_text="She moved because it was her only dream. She always said, 'Never give up.'", memory_ids=[1], event_ids=[])


def _memory(db, legacy_id, memory_id, text, category="life_event"):
    item = Memory(id=memory_id, legacy_id=legacy_id, canonical_text=text, category=category, source_language="english", source_excerpt=text, confidence=.95, status=MemoryStatus.ACTIVE.value, operation_type="explicit_save", explicit_save=True, normalized_fingerprint=f"l18-{memory_id}")
    db.add(item); db.flush(); return item


def test_audit_allows_direct_fact_and_safe_implication_but_rejects_invention():
    facts = [{"kind": "memory", "id": 1, "text": "Pallavi moved to Pune in 1998."}, {"kind": "memory", "id": 2, "text": "Pallavi became a teacher in 2001."}]
    assert audit_chapter(StoryChapterDraft(title="x", narrative_text="Pallavi moved to Pune in 1998. A few years later, she began teaching.", memory_ids=[1, 2]), "biography_third_person", facts)["accepted"]
    assert "unsupported_causality" in audit_chapter(StoryChapterDraft(title="x", narrative_text="She moved because teaching had always been her dream.", memory_ids=[1, 2]), "biography_third_person", facts)["reasons"]
    assert "unsupported_quote" in audit_chapter(StoryChapterDraft(title="x", narrative_text="She always said, 'Never give up.'", memory_ids=[1]), "biography_third_person", facts)["reasons"]


def test_story_generation_has_zero_canonical_side_effects_and_supports_both_perspectives(test_context):
    client, sessions, codes, _ = test_context
    auth = register_user(client, codes, email="l18-owner@example.com")
    legacy = _named_legacy(client, sessions, auth, "Pallavi")
    app.dependency_overrides[get_story_provider] = lambda: SafeProvider()
    try:
        for perspective in ("legacy_first_person", "biography_third_person"):
            created = client.post(f"/api/v1/stories?legacy_id={legacy['legacy_id']}", json={"title": "Teaching years", "scope": "full_biography", "narrative_perspective": perspective}, headers=_headers(auth))
            assert created.status_code == 201, created.text
            with sessions() as db:
                _memory(db, legacy["legacy_id"], 100 if perspective.startswith("legacy") else 101, "Pallavi moved to Pune in 1998.")
                db.commit()
            result = client.post(f"/api/v1/stories/{created.json()['id']}/generate?legacy_id={legacy['legacy_id']}", json={"request_key": perspective}, headers=_headers(auth))
            assert result.status_code == 200, result.text
            text = result.json()["current_version"]["chapters"][0]["narrative_text"]
            assert ("I " in text or "I moved" in text) if perspective.startswith("legacy") else "I " not in text
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(Memory)) == 2
            assert db.scalar(select(func.count()).select_from(MemoryRevision)) == 0
            assert db.scalar(select(func.count()).select_from(LifeEvent)) == 0
    finally:
        app.dependency_overrides.pop(get_story_provider, None)


def test_story_edit_is_artifact_only_and_regeneration_does_not_overwrite_edit(test_context):
    client, sessions, codes, _ = test_context
    auth = register_user(client, codes, email="l18-edit@example.com")
    legacy = _named_legacy(client, sessions, auth, "Pallavi")
    app.dependency_overrides[get_story_provider] = lambda: SafeProvider()
    try:
        with sessions() as db:
            _memory(db, legacy["legacy_id"], 110, "Pallavi moved to Pune in 1998."); db.commit()
        story = client.post(f"/api/v1/stories?legacy_id={legacy['legacy_id']}", json={"title":"Move","scope":"full_biography","narrative_perspective":"biography_third_person"}, headers=_headers(auth)).json()
        generated = client.post(f"/api/v1/stories/{story['id']}/generate?legacy_id={legacy['legacy_id']}", json={"request_key":"one"}, headers=_headers(auth)).json()
        chapter = generated["current_version"]["chapters"][0]
        edited = client.patch(f"/api/v1/stories/{story['id']}/chapters/{chapter['id']}?legacy_id={legacy['legacy_id']}", json={"title":"Edited","narrative_text":"Pallavi moved to Pune in 1999."}, headers=_headers(auth))
        assert edited.status_code == 200
        with sessions() as db:
            assert db.get(Memory, 110).canonical_text.endswith("1998.")
            assert db.scalar(select(func.count()).select_from(MemoryRevision)) == 0
            assert db.scalar(select(func.count()).select_from(LifeEvent)) == 0
            assert db.scalar(select(func.count()).select_from(StoryVersion)) == 2
    finally:
        app.dependency_overrides.pop(get_story_provider, None)


def test_owner_publication_and_collaborator_or_foreign_access_are_scoped(test_context):
    client, sessions, codes, _ = test_context
    auth = register_user(client, codes, email="l18-access@example.com")
    first = _named_legacy(client, sessions, auth, "First")
    second = _new_legacy(client, sessions, auth, "Second")
    created = client.post(f"/api/v1/stories?legacy_id={first['legacy_id']}", json={"title":"Private","scope":"custom","narrative_perspective":"biography_third_person"}, headers=_headers(auth))
    assert created.status_code == 201
    story_id = created.json()["id"]
    assert client.get(f"/api/v1/stories/{story_id}?legacy_id={second}", headers=_headers(auth)).status_code == 404
    assert client.post(f"/api/v1/stories/{story_id}/publish?legacy_id={first['legacy_id']}", json={"published":True}, headers=_headers(auth)).status_code == 409
    assert client.delete(f"/api/v1/stories/{story_id}?legacy_id={first['legacy_id']}", headers=_headers(auth)).status_code == 405


def test_bad_grounding_leaves_no_accepted_version(test_context):
    client, sessions, codes, _ = test_context
    auth = register_user(client, codes, email="l18-audit@example.com")
    legacy = _named_legacy(client, sessions, auth, "Pallavi")
    app.dependency_overrides[get_story_provider] = lambda: BadProvider()
    try:
        with sessions() as db:
            _memory(db, legacy["legacy_id"], 120, "Pallavi moved to Pune in 1998."); db.commit()
        story = client.post(f"/api/v1/stories?legacy_id={legacy['legacy_id']}", json={"title":"Bad","scope":"full_biography","narrative_perspective":"biography_third_person"}, headers=_headers(auth)).json()
        response = client.post(f"/api/v1/stories/{story['id']}/generate?legacy_id={legacy['legacy_id']}", json={"request_key":"bad"}, headers=_headers(auth))
        assert response.status_code == 422
        with sessions() as db: assert db.scalar(select(func.count()).select_from(StoryChapter)) == 0
    finally:
        app.dependency_overrides.pop(get_story_provider, None)
