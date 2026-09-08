import asyncio
import json

import pytest
from sqlalchemy import select

from app.api.routes.stories import get_story_provider
from app.main import app
from app.models.story import Story, StoryChapter, StoryVersion
from app.models.memory import Memory, MemoryRevision
from app.models.timeline import LifeEvent
from app.models.personality import LegacyPersonalityProfile
from app.models.media_intelligence import SourceEvidence
from app.services.stories import (DeterministicStoryProvider, StoryChapterDraft,
                                 audit_chapter, generate_audited_chapter, OutlineChapter)
from tests.conftest import register_user
from tests.test_memory import _headers, _named_legacy
from tests.test_stories_l18 import _memory


FACTS = [{"kind": "memory", "id": 1, "text": "Asha moved to Pune in 1998."},
         {"kind": "memory", "id": 2, "text": "Asha helped her children with homework and attended their school functions."}]


@pytest.mark.parametrize("text,perspective,accepted", [
    ("I moved to Pune in 1998. Asha moved to Pune in 1998.", "legacy_first_person", False),
    ("I helped my children with their homework.", "legacy_first_person", False),
    ("I helped my children with homework and attended school functions.", "legacy_first_person", True),
    ("Asha helped her children with homework.", "biography_third_person", True),
    ("I moved to Pune in 1998.", "biography_third_person", False),
    ("Asha moved to Pune in 1998.", "legacy_first_person", False),
    ('I said, "Never give up."', "legacy_first_person", False),
    ("I moved because teaching was my dream.", "legacy_first_person", False),
    ("My children were the only thing that mattered.", "legacy_first_person", False),
])
def test_exact_provider_shape_and_grounding(text, perspective, accepted):
    assert audit_chapter(StoryChapterDraft(title="QA", narrative_text=text), perspective, FACTS)["accepted"] is accepted


class RepairProvider(DeterministicStoryProvider):
    def __init__(self, fail=False):
        self.feedback = []
        self.fail = fail

    async def chapter(self, legacy, perspective, chapter, facts, style="", *, audit_feedback=()):
        self.feedback.append(audit_feedback)
        text = "I helped my children with their homework."
        if audit_feedback and not self.fail:
            text = "I helped my children with homework and attended school functions."
        return StoryChapterDraft(title=chapter.title, narrative_text=text, memory_ids=chapter.memory_ids)


def snapshot(db):
    return {model.__tablename__: [dict(row) for row in db.execute(select(model.__table__)).mappings()]
            for model in (Memory, MemoryRevision, LifeEvent, SourceEvidence, LegacyPersonalityProfile)}


@pytest.mark.parametrize("fail", [False, True])
def test_remediation_is_bounded_idempotent_and_has_zero_canonical_writes(test_context, fail, caplog):
    client, sessions, codes, _ = test_context
    owner = register_user(client, codes, email="closure-owner@example.com")
    lid = _named_legacy(client, sessions, owner, "Asha")["legacy_id"]
    with sessions() as db:
        _memory(db, lid, 400, FACTS[1]["text"])
        db.commit()
        before = snapshot(db)
    provider = RepairProvider(fail)
    app.dependency_overrides[get_story_provider] = lambda: provider
    try:
        story = client.post(f"/api/v1/stories?legacy_id={lid}", headers=_headers(owner), json={"title":"QA", "scope":"full_biography", "narrative_perspective":"legacy_first_person"}).json()
        url = f"/api/v1/stories/{story['id']}/generate?legacy_id={lid}"
        response = client.post(url, headers=_headers(owner), json={"request_key":"repair"})
        assert response.status_code == (422 if fail else 200)
        assert provider.feedback == [(), ("perspective_mismatch",)] + ([("perspective_mismatch",)] if fail else [])
        assert client.post(url, headers=_headers(owner), json={"request_key":"repair"}).status_code == response.status_code
        assert len(provider.feedback) == (3 if fail else 2)
        with sessions() as db:
            assert before == snapshot(db)
            versions = list(db.scalars(select(StoryVersion)).all())
            assert len(versions) == 1
            assert versions[0].status == ("audit_failed" if fail else "ready")
            if fail:
                assert db.get(Story, story["id"]).current_version_id is None
                assert not db.scalars(select(StoryChapter)).all()
        assert "their homework" not in caplog.text
    finally:
        app.dependency_overrides.pop(get_story_provider, None)


def test_corrective_feedback_cannot_clear_grounding_failure():
    class Unsafe(RepairProvider):
        async def chapter(self, *args, audit_feedback=(), **kwargs):
            self.feedback.append(audit_feedback)
            text = 'I said, "Never give up."' if len(self.feedback) == 1 else "I moved because teaching was my dream."
            return StoryChapterDraft(title="QA", narrative_text=text)
    provider = Unsafe()
    with pytest.raises(ValueError, match="audit_failed"):
        asyncio.run(generate_audited_chapter(provider, None, "legacy_first_person", OutlineChapter(title="QA"), FACTS))
    assert len(provider.feedback) == 3
    assert {"unsupported_quote", "unsupported_causality"} <= set(provider.feedback[-1])


def test_old_generation_replay_preserves_current_owner_edit(test_context):
    client, sessions, codes, _ = test_context
    owner = register_user(client, codes, email="closure-replay@example.com")
    lid = _named_legacy(client, sessions, owner, "Asha")["legacy_id"]
    h = _headers(owner)
    with sessions() as db:
        _memory(db, lid, 420, FACTS[0]["text"]); db.commit()
    app.dependency_overrides[get_story_provider] = lambda: DeterministicStoryProvider()
    try:
        story = client.post(f"/api/v1/stories?legacy_id={lid}", headers=h, json={"title":"QA", "scope":"full_biography", "narrative_perspective":"biography_third_person"}).json()
        root = f"/api/v1/stories/{story['id']}"
        generated = client.post(f"{root}/generate?legacy_id={lid}", headers=h,json={"request_key":"old"}).json()
        chapter = generated["current_version"]["chapters"][0]['id']
        edited = client.patch(f"{root}/chapters/{chapter}?legacy_id={lid}", headers=h,json={"title":"Owner edit", "narrative_text":"Asha moved to Pune in 1999."}).json()
        replay = client.post(f"{root}/generate?legacy_id={lid}", headers=h,json={"request_key":"old"})
        assert replay.status_code == 200
        assert replay.json()["current_version_id"] == edited["current_version_id"]
        assert replay.json()["current_version"]["human_edited"]
        assert client.get(f"{root}?legacy_id={lid}",headers=h).json()["current_version_id"] == edited["current_version_id"]
    finally:
        app.dependency_overrides.pop(get_story_provider, None)


def test_visitor_publication_redaction_and_all_mutations_denied(test_context):
    client, sessions, codes, _ = test_context
    owner = register_user(client, codes, email="closure-publisher@example.com")
    visitor = register_user(client, codes, email="closure-reader@example.com")
    collaborator = register_user(client, codes, email="closure-col@example.com")
    lid = _named_legacy(client, sessions, owner, "Asha")["legacy_id"]
    h = _headers(owner)
    code = client.post(f"/api/v1/legacy-access/legacies/{lid}/code", headers=h).json()["code"]
    assert client.post("/api/v1/legacy-access/join", headers=_headers(visitor), json={"code":code}).status_code == 200
    colcode = client.post(f"/api/v1/collaborations/legacies/{lid}/code", headers=h).json()["code"]
    assert client.post("/api/v1/collaborations/join", headers=_headers(collaborator), json={"code":colcode}).status_code == 200
    body = {"title":"QA", "scope":"full_biography", "narrative_perspective":"biography_third_person"}
    app.dependency_overrides[get_story_provider] = lambda: DeterministicStoryProvider()
    try:
        with sessions() as db:
            _memory(db, lid, 410, FACTS[0]["text"]); db.commit()
        story = client.post(f"/api/v1/stories?legacy_id={lid}", headers=h, json=body).json()
        root = f"/api/v1/stories/{story['id']}"
        generated = client.post(f"{root}/generate?legacy_id={lid}", headers=h, json={"request_key":"one"}).json()
        chapter = generated["current_version"]["chapters"][0]["id"]
        published_url = f"/api/v1/stories/published?legacy_id={lid}"
        assert client.get(published_url, headers=_headers(visitor)).json() == []
        assert client.post(f"{root}/publish?legacy_id={lid}", headers=h, json={"published":True}).status_code == 200
        listed = client.get(published_url, headers=_headers(visitor)).json()
        assert len(listed) == 1
        assert listed[0]["current_version"]["audit_summary"] is None
        assert listed[0]["current_version"]["chapters"][0]["support"] == []
        assert listed[0]["current_version"]["chapters"][0]["audit_summary"] is None
        with sessions() as db: before = snapshot(db)
        for identity in (visitor, collaborator):
            ih = _headers(identity)
            assert client.post(f"/api/v1/stories?legacy_id={lid}", headers=ih, json=body).status_code in {403,404}
            for suffix, payload in [("generate",{"request_key":"denied"}),("publish",{"published":True}),("archive",None)]:
                assert client.post(f"{root}/{suffix}?legacy_id={lid}", headers=ih, json=payload).status_code in {403,404}
            assert client.patch(f"{root}/chapters/{chapter}?legacy_id={lid}", headers=ih, json={"title":"bad","narrative_text":"bad"}).status_code in {403,404}
        for suffix in ("", "/provenance"):
            assert client.get(f"{root}{suffix}?legacy_id={lid}", headers=_headers(visitor)).status_code == 404
        assert client.get(f"{root}?legacy_id={lid}", headers=_headers(collaborator)).status_code == 200
        assert client.post(f"{root}/publish?legacy_id={lid}", headers=h,json={"published":False}).status_code == 200
        assert client.get(published_url, headers=_headers(visitor)).json() == []
        assert client.post(f"{root}/archive?legacy_id={lid}",headers=h).status_code == 200
        assert client.get(published_url, headers=_headers(visitor)).json() == []
        with sessions() as db: assert snapshot(db) == before
    finally:
        app.dependency_overrides.pop(get_story_provider, None)
