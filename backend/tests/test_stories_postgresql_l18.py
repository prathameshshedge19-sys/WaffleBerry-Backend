"""Opt-in PostgreSQL acceptance for L18 Story integrity and concurrency."""

import asyncio
import os
import threading
from datetime import datetime, timezone
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import build_engine
from app.models.legacy import Legacy
from app.models.memory import Memory
from app.models.timeline import LifeEvent
from app.models.media_intelligence import SourceEvidence
from app.models.media_source import MediaSource, MediaArtifact, MediaProcessingJob
from app.models.story import Story, StoryChapter, StorySupportLink, StoryVersion
from app.services.stories import DeterministicStoryProvider, StoryEngine


@pytest.fixture
def pg():
    url = os.environ.get("L18_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires explicitly configured disposable L18 PostgreSQL database")
    parsed = make_url(url)
    assert parsed.host in {"localhost", "127.0.0.1"} and parsed.database == "l18_test_phase_b"
    engine = build_engine(url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with engine.begin() as db:
        db.execute(text("TRUNCATE story_support_links, story_chapters, story_versions, stories, life_event_entities, life_event_evidence, life_event_memories, life_events, source_evidence, media_artifacts, media_processing_jobs, media_sources, memory_entity_links, memory_entities, memories, memory_revisions, legacies, users RESTART IDENTITY CASCADE"))
        db.execute(text("INSERT INTO users (id, full_name, email, password_hash, is_verified) VALUES (1, 'L18 Owner', 'l18-owner@example.com', 'x', true)"))
        db.execute(text("INSERT INTO legacies (id, owner_user_id, subject_name, setup_status) VALUES (1, 1, 'Pallavi', 'active'), (2, 1, 'Other', 'active')"))
    try:
        yield factory
    finally:
        engine.dispose()


def _memory(db, legacy_id=1, memory_id=1, text_value="Pallavi moved to Pune in 1998."):
    item = Memory(id=memory_id, legacy_id=legacy_id, canonical_text=text_value, category="life_event", source_language="english", source_excerpt=text_value, confidence=.95, status="active", operation_type="explicit_save", explicit_save=True, normalized_fingerprint="pg-l18-" + str(memory_id))
    db.add(item); db.flush(); return item


def _story(db, legacy_id=1, perspective="biography_third_person"):
    item = Story(id="story-" + str(legacy_id), legacy_id=legacy_id, title="Pallavi's story", scope="full_biography", narrative_perspective=perspective, visibility="draft", lifecycle_state="active", staleness_state="current", created_by_user_id=1)
    db.add(item); db.commit(); return item


def test_pg_migration_shape_and_checks(pg):
    with pg() as db:
        tables = set(db.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'" )).scalars())
        assert {"stories", "story_versions", "story_chapters", "story_support_links"} <= tables
        checks = set(db.execute(text("SELECT conname FROM pg_constraint WHERE conname LIKE 'ck_stor%'" )).scalars())
        assert {"ck_stories_scope", "ck_story_versions_status", "ck_story_chapters_text_bound", "ck_story_support_one_target"} <= checks
        assert db.execute(text("SELECT COUNT(*) FROM pg_constraint WHERE conname IN ('fk_story_support_memory_scope','fk_story_support_event_scope','fk_story_support_evidence_scope','fk_story_versions_story_scope')")).scalar_one() == 4


def test_pg_cross_legacy_story_support_is_rejected(pg):
    with pg() as db:
        _memory(db, 1, 10); _memory(db, 2, 20, "Other moved to Rome in 2000.")
        first = _story(db, 1)
        version = StoryVersion(id="version-1", legacy_id=1, story_id=first.id, version_number=1, status="ready", created_by_user_id=1); db.add(version); db.flush()
        chapter = StoryChapter(id="chapter-1", legacy_id=1, story_version_id=version.id, title="Move", ordinal=0, narrative_text="Pallavi moved to Pune in 1998.", generation_status="ready"); db.add(chapter); db.flush()
        with pytest.raises(IntegrityError):
            db.add(StorySupportLink(id="support-foreign", legacy_id=1, story_version_id=version.id, chapter_id=chapter.id, support_kind="memory", memory_id=20)); db.flush()
        db.rollback()


def test_pg_duplicate_generation_request_is_idempotent(pg):
    with pg() as db:
        _memory(db); story = _story(db); story_id = story.id
    def run(_):
        with pg() as db:
            row = db.get(Story, story_id)
            return asyncio.run(StoryEngine(db, DeterministicStoryProvider()).generate(row, db.get(Legacy, 1), 1, "same-request")).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = [f.result(timeout=30) for f in [pool.submit(run, 1), pool.submit(run, 2)]]
    with pg() as db:
        assert values == [story_id, story_id]
        assert db.scalar(select(func.count()).select_from(StoryVersion).where(StoryVersion.story_id == story_id)) == 1
        assert db.scalar(select(func.count()).select_from(StoryChapter)) == 1


@pytest.mark.parametrize("kind", ["timeline_event", "source_evidence"])
def test_pg_foreign_event_and_evidence_support_rejected(pg, kind):
    with pg() as db:
        event_id, source_id, job_id, artifact_id, evidence_id = [str(uuid4()) for _ in range(5)]
        db.add(LifeEvent(id=event_id, legacy_id=2, admission_key=str(uuid4()), title="Foreign QA", event_type="other", origin="human_created", review_state="approved", lifecycle_state="active"))
        db.add(MediaSource(id=source_id, legacy_id=2, uploader_user_id=1, kind="document", original_filename="QA.txt", declared_mime_type="text/plain", declared_size_bytes=1, upload_request_key=str(uuid4()), upload_request_digest="x"*64, upload_expires_at=datetime.now(timezone.utc)))
        db.flush()
        db.add(MediaProcessingJob(id=job_id, legacy_id=2, source_id=source_id, generation=1, kind="extract", pipeline_version="test"))
        db.add(MediaArtifact(id=artifact_id, legacy_id=2, source_id=source_id, generation=1, kind="text", logical_key="text", storage_backend="local", object_key="qa"))
        db.flush()
        db.add(SourceEvidence(id=evidence_id, legacy_id=2, source_id=source_id, generation=1, job_id=job_id, artifact_id=artifact_id, stable_key="qa", kind="text_span", text="Synthetic QA evidence"))
        db.flush()
        story = _story(db)
        db.add(StoryVersion(id="scope-version",legacy_id=1,story_id=story.id,version_number=1,status="ready",created_by_user_id=1)); db.flush()
        db.add(StoryChapter(id="scope-chapter",legacy_id=1,story_version_id="scope-version",ordinal=0,title="QA",narrative_text="QA",generation_status="ready")); db.flush()
        target = {"life_event_id":event_id} if kind == "timeline_event" else {"evidence_id":evidence_id}
        with pytest.raises(IntegrityError):
            db.add(StorySupportLink(id=str(uuid4()),legacy_id=1,story_version_id="scope-version",chapter_id="scope-chapter",support_kind=kind,**target)); db.flush()
        db.rollback()


class BlockingProvider(DeterministicStoryProvider):
    started = threading.Event()
    release = threading.Event()
    async def outline(self, *args, **kwargs):
        self.started.set(); self.release.wait(timeout=20); return await super().outline(*args, **kwargs)


def test_pg_generation_and_owner_edit_leave_owner_edit_current(pg):
    with pg() as db:
        _memory(db); story = _story(db); story_id = story.id
    provider = BlockingProvider()
    def generate():
        with pg() as db:
            return asyncio.run(StoryEngine(db, provider).generate(db.get(Story, story_id), db.get(Legacy, 1), 1, "blocking"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        generated_future = pool.submit(generate)
        assert provider.started.wait(timeout=10)
        def edit():
            with pg() as db:
                # The Story row lock makes this wait until generation commits.
                while db.scalar(select(Story.current_version_id).where(Story.id == story_id)) is not None:
                    break
                return StoryEngine(db).edit_chapter(db.get(Story, story_id), db.scalar(select(StoryChapter.id)), 1, "Owner edit", "Pallavi moved to Pune in 1999.")
        edit_future = pool.submit(edit)
        provider.release.set()
        generated_future.result(timeout=30); edit_future.result(timeout=30)
    with pg() as db:
        story = db.get(Story, story_id); chapter = db.scalar(select(StoryChapter).where(StoryChapter.story_version_id == story.current_version_id))
        assert chapter.human_edited and "1999" in chapter.narrative_text
        assert db.scalar(select(func.count()).select_from(StoryVersion)) == 2


def test_pg_memory_correction_marks_story_stale_without_rewriting_text(pg):
    with pg() as db:
        _memory(db); story = _story(db)
        asyncio.run(StoryEngine(db, DeterministicStoryProvider()).generate(story, db.get(Legacy, 1), 1, "stale"))
        story_id = story.id; version_id = story.current_version_id
        memory = db.get(Memory, 1); memory.canonical_text = "Pallavi moved to Pune in 1999."; db.commit()
    with pg() as db:
        story = db.get(Story, story_id); chapter = db.scalar(select(StoryChapter).where(StoryChapter.story_version_id == version_id))
        assert story.staleness_state == "stale"
        assert "1998" in chapter.narrative_text


def test_pg_old_request_replay_does_not_undo_owner_edit(pg):
    with pg() as db:
        _memory(db); story = _story(db)
        asyncio.run(StoryEngine(db).generate(story, db.get(Legacy, 1), 1, "original"))
        chapter = db.scalar(select(StoryChapter))
        StoryEngine(db).edit_chapter(story, chapter.id, 1, "Owner edit", "Owner preserved Story edit.")
        edited_id = story.current_version_id
    with pg() as db:
        story = db.get(Story, "story-1")
        asyncio.run(StoryEngine(db).generate(story, db.get(Legacy, 1), 1, "original"))
        db.commit()
        assert story.current_version_id == edited_id
        assert db.scalar(select(func.count()).select_from(StoryVersion)) == 2


def test_pg_publish_archive_race_has_one_coherent_final_state(pg):
    with pg() as db:
        story = _story(db); story.visibility = "published"; db.commit(); story_id = story.id
    barrier = threading.Barrier(2)
    def publish():
        with pg() as db:
            barrier.wait(timeout=10); row = db.scalar(select(Story).where(Story.id == story_id, Story.lifecycle_state == "active").with_for_update())
            if row is not None: row.visibility = "published"
            db.commit()
    def archive():
        with pg() as db:
            barrier.wait(timeout=10); row = db.scalar(select(Story).where(Story.id == story_id).with_for_update()); row.visibility = "archived"; row.lifecycle_state = "deleted"; db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish), pool.submit(archive)]
        [future.result(timeout=30) for future in futures]
    with pg() as db:
        row = db.get(Story, story_id)
        assert (row.visibility, row.lifecycle_state) in {("published", "active"), ("archived", "deleted")}
