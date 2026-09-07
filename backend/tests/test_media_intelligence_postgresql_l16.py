"""Live PostgreSQL races for L16 Phase C review and provenance."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import build_engine
from app.models.media_intelligence import MemorySourceLink, SourceCandidateEvidence, SourceEvidence, SourceMemoryCandidate
from app.models.media_source import MediaArtifact, MediaProcessingJob, MediaSource
from app.models.memory import Memory
from app.models.user import User
from app.services.legacy_personality import load_evidence
from app.services.media_intelligence import MediaIntelligenceService, RuleBasedSourceAnalysisProvider
from app.services.media_review import MediaReviewService
from app.services.media_sources import MediaSourceService
from app.services.media_storage import LocalSourceStorage
from app.services.media_worker import MediaIntelligenceWorker, MediaWorker
from app.services.memory import LivingMemoryService
from app.services.memory import CanonicalEdit, _fingerprint


class FakeMemoryProvider:
    model = "fake-memory"
    embedding_model = "fake-embedding"
    embedding_version = "pg-test-v1"
    embedding_dimensions = 4

    async def embed(self, texts):
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    url = os.environ.get("L16_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires explicitly configured disposable L16 PostgreSQL database")
    parsed = make_url(url)
    assert parsed.host in {"localhost", "127.0.0.1"} and parsed.database and parsed.database.startswith("l16_test")
    previous = {key: os.environ.get(key) for key in ("DATABASE_URL", "LEGARYA_DEBUG", "JWT_SECRET_KEY", "MEDIA_ENABLED", "MEDIA_LOCAL_STORAGE_PATH")}
    os.environ.update({"DATABASE_URL": url, "LEGARYA_DEBUG": "true", "JWT_SECRET_KEY": "test-only-secret-with-sufficient-length-123456", "MEDIA_ENABLED": "true", "MEDIA_LOCAL_STORAGE_PATH": str(tmp_path_factory.mktemp("media"))})
    get_settings.cache_clear()
    # Alembic configures logging globally; isolate it from the pytest process.
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=Path(__file__).parents[1], check=True)
    engine = build_engine(url)
    with engine.begin() as db:
        assert db.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == "0018_media_intelligence"
        tables = set(db.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'source_%' OR table_name='memory_source_links'")).scalars())
        assert {"source_evidence", "source_memory_candidates", "source_candidate_evidence", "memory_source_links"} <= tables
        db.execute(text("TRUNCATE memory_source_links, source_candidate_evidence, source_memory_candidates, source_evidence, media_processing_jobs, media_artifacts, media_sources, memories, memory_revisions, memory_entity_links, memory_entities, builder_activities, legacy_personality_profiles, legacies, users RESTART IDENTITY CASCADE"))
        db.execute(text("INSERT INTO users (id, full_name, email, password_hash, is_verified) VALUES (1, 'Owner', 'l16-c-owner@example.com', 'x', true), (2, 'Collaborator', 'l16-collab@example.com', 'x', true)"))
        db.execute(text("INSERT INTO legacies (id, owner_user_id, subject_name, setup_status) VALUES (1, 1, 'Pallavi', 'active')"))
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage = LocalSourceStorage(str(tmp_path_factory.mktemp("objects")))
    try:
        yield factory, storage, engine
    finally:
        engine.dispose()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


@pytest.fixture(autouse=True)
def clean(pg):
    with pg[2].begin() as db:
        db.execute(text("TRUNCATE memory_source_links, source_candidate_evidence, source_memory_candidates, source_evidence, media_processing_jobs, media_artifacts, media_sources, memories, memory_revisions, memory_entity_links, memory_entities, builder_activities, legacy_personality_profiles RESTART IDENTITY CASCADE"))
        db.execute(text("DELETE FROM legacies WHERE id > 1"))


def _source(pg):
    factory, storage, _ = pg
    data = b"Pallavi loved jasmine flowers."
    with factory() as db:
        source = MediaSourceService(storage=storage).create(db, db.get(User, 1), 1, kind="document", filename="letter.txt", mime_type="text/plain", size_bytes=len(data), upload_request_key=str(uuid4()))
        MediaSourceService(storage=storage).receive(db, db.get(User, 1), 1, source.id, data)
        return source.id


def _process(pg, source_id):
    factory, storage, _ = pg
    assert MediaIntelligenceWorker(sessions=factory, storage=storage, provider=RuleBasedSourceAnalysisProvider()).run_once() == "candidates_ready"
    with factory() as db:
        return db.scalar(select(SourceMemoryCandidate).where(SourceMemoryCandidate.source_id == source_id)).id


def _race(factory, operation):
    barrier = threading.Barrier(2)

    def run(index):
        with factory() as db:
            barrier.wait(timeout=10)
            try:
                return operation(db, index)
            except HTTPException as exc:
                db.rollback()
                assert exc.status_code in {409, 410}, exc
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, index) for index in range(2)]
        return [future.result(timeout=30) for future in futures]


def _review_service():
    return MediaReviewService(LivingMemoryService(FakeMemoryProvider()))


def test_pg_preserve_vs_preserve_has_one_final_outcome(pg):
    source_id = _source(pg); candidate_id = _process(pg, source_id); factory, storage, engine = pg
    def operation(db, _):
        return asyncio.run(_review_service().review(db, db.get(User, 1), 1, candidate_id, action="preserve", expected_version=1, review_request_key=str(uuid4())))
    results = _race(factory, operation)
    assert sum(isinstance(item, dict) for item in results) == 1
    assert results.count("conflict") == 1
    with engine.begin() as db:
        assert db.execute(text("SELECT COUNT(*) FROM memories")).scalar_one() == 1
        assert db.execute(text("SELECT COUNT(*) FROM memory_source_links")).scalar_one() == 1


def test_pg_preserve_vs_skip_serializes_candidate(pg):
    source_id = _source(pg); candidate_id = _process(pg, source_id); factory, _, engine = pg
    def operation(db, index):
        return asyncio.run(_review_service().review(db, db.get(User, 1), 1, candidate_id, action="preserve" if index == 0 else "skip", expected_version=1, review_request_key=str(uuid4())))
    results = _race(factory, operation)
    assert sum(isinstance(item, dict) for item in results) == 1
    assert results.count("conflict") == 1
    with engine.begin() as db:
        state = db.execute(text("SELECT review_state FROM source_memory_candidates WHERE id = :id"), {"id": candidate_id}).scalar_one()
        assert state in {"preserved", "skipped"}
        assert db.execute(text("SELECT COUNT(*) FROM memories")).scalar_one() == (1 if state == "preserved" else 0)


def test_pg_edit_preserve_vs_preserve_uses_version_fence(pg):
    source_id = _source(pg); candidate_id = _process(pg, source_id); factory, _, engine = pg
    with factory() as db:
        asyncio.run(_review_service().prepare_edit(db, db.get(User, 1), 1, candidate_id, canonical_text="Pallavi loved flowers.", category="preference", expected_version=1))
    def operation(db, index):
        return asyncio.run(_review_service().review(db, db.get(User, 1), 1, candidate_id, action="edit_preserve" if index == 0 else "preserve", expected_version=2 if index == 0 else 1, review_request_key=str(uuid4())))
    results = _race(factory, operation)
    assert sum(isinstance(item, dict) for item in results) == 1
    assert results.count("conflict") == 1
    with engine.begin() as db:
        assert db.execute(text("SELECT review_state FROM source_memory_candidates WHERE id = :id"), {"id": candidate_id}).scalar_one() == "preserved"
        assert db.execute(text("SELECT COUNT(*) FROM memories")).scalar_one() == 1


def test_pg_preserve_vs_source_delete_keeps_only_valid_outcome(pg):
    source_id = _source(pg); candidate_id = _process(pg, source_id); factory, storage, engine = pg
    def operation(db, index):
        if index == 0:
            return asyncio.run(_review_service().review(db, db.get(User, 1), 1, candidate_id, action="preserve", expected_version=1, review_request_key=str(uuid4())))
        return MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_id).state
    results = _race(factory, operation)
    assert results[1] == "deleting"
    assert isinstance(results[0], dict) or results[0] == "conflict"
    with engine.begin() as db:
        state = db.execute(text("SELECT state FROM media_sources WHERE id = :id"), {"id": source_id}).scalar_one()
        assert state == "deleting"
        assert db.execute(text("SELECT COUNT(*) FROM memories")).scalar_one() in {0, 1}
        assert db.execute(text("SELECT COUNT(*) FROM source_memory_candidates WHERE review_state='preserved'" )).scalar_one() in {0, 1}
        memories = db.execute(text("SELECT COUNT(*) FROM memories")).scalar_one()
        assert db.execute(text("SELECT COUNT(*) FROM source_memory_candidates WHERE review_state='preserved'")).scalar_one() == memories
        assert db.execute(text("SELECT COUNT(*) FROM memory_source_links WHERE support_state='unavailable' AND removed_at IS NOT NULL")).scalar_one() == memories
        assert db.execute(text("SELECT COUNT(*) FROM memory_source_links WHERE support_state='approved'")).scalar_one() == 0
    with factory() as db:
        assert not load_evidence(db, 1)


def test_pg_stale_processing_completion_cannot_create_candidates(pg):
    source_id = _source(pg); factory, storage, engine = pg
    worker = MediaWorker(sessions=factory, storage=storage)
    claim = worker.claim(); assert claim is not None
    with factory() as db:
        MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_id)
    result = asyncio.run(MediaIntelligenceService(RuleBasedSourceAnalysisProvider(), storage=storage).process_claim(factory, *claim))
    assert result == "stale"
    with engine.begin() as db:
        assert db.execute(text("SELECT COUNT(*) FROM source_evidence")).scalar_one() == 0
        assert db.execute(text("SELECT COUNT(*) FROM source_memory_candidates")).scalar_one() == 0
        assert db.execute(text("SELECT COUNT(*) FROM memories")).scalar_one() == 0


def test_pg_duplicate_processing_publishes_once_and_retry_keeps_review(pg):
    source_id = _source(pg)
    factory, storage, engine = pg
    claim = MediaWorker(sessions=factory, storage=storage).claim()
    barrier = threading.Barrier(2)

    class ConcurrentProvider(RuleBasedSourceAnalysisProvider):
        async def analyze(self, *args):
            barrier.wait(timeout=10)
            return await super().analyze(*args)

    def process(_):
        return asyncio.run(MediaIntelligenceService(ConcurrentProvider(), storage=storage).process_claim(factory, *claim))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(process, range(2)))
    assert sorted(results) == ["candidates_ready", "stale"]
    with factory() as db:
        candidate = db.scalar(select(SourceMemoryCandidate))
        candidate_id = candidate.id
        asyncio.run(_review_service().review(db, db.get(User, 1), 1, candidate_id, action="skip", expected_version=1, review_request_key=str(uuid4())))
        db.get(MediaSource, source_id).state = "partially_ready"
        db.commit()
        MediaSourceService(storage=storage).retry(db, db.get(User, 1), 1, source_id)
    assert MediaIntelligenceWorker(sessions=factory, storage=storage, provider=RuleBasedSourceAnalysisProvider()).run_once() == "candidates_ready"
    with engine.connect() as db:
        for table in ("source_evidence", "source_memory_candidates", "source_candidate_evidence"):
            assert db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 1
        assert db.execute(text("SELECT review_state FROM source_memory_candidates")).scalar_one() == "skipped"
        assert db.execute(text("SELECT COUNT(*) FROM memories")).scalar_one() == 0


def test_pg_delete_while_provider_runs_fences_completion(pg):
    source_id = _source(pg)
    factory, storage, engine = pg
    claim = MediaWorker(sessions=factory, storage=storage).claim()
    entered, release = threading.Event(), threading.Event()

    class PausedProvider(RuleBasedSourceAnalysisProvider):
        async def analyze(self, *args):
            entered.set()
            assert release.wait(timeout=10)
            return await super().analyze(*args)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: asyncio.run(MediaIntelligenceService(PausedProvider(), storage=storage).process_claim(factory, *claim)))
        try:
            assert entered.wait(timeout=10)
            with factory() as db:
                MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_id)
        finally:
            release.set()
        assert future.result(timeout=10) == "stale"
    with engine.connect() as db:
        for table in ("source_evidence", "source_memory_candidates", "memories", "memory_source_links"):
            assert db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0
        assert db.execute(text("SELECT state FROM media_sources")).scalar_one() == "deleting"


def test_pg_promotion_failure_rolls_back_all_effects(pg, monkeypatch):
    source_id = _source(pg)
    candidate_id = _process(pg, source_id)
    factory, _, engine = pg
    import app.services.media_review as review_module

    def fail(*args, **kwargs):
        raise RuntimeError("injected activity failure")

    monkeypatch.setattr(review_module, "record_builder_activity", fail)
    with factory() as db:
        with pytest.raises(RuntimeError, match="injected activity"):
            asyncio.run(_review_service().review(db, db.get(User, 1), 1, candidate_id, action="preserve", expected_version=1, review_request_key=str(uuid4())))
    with engine.connect() as db:
        for table in ("memories", "memory_source_links", "memory_entities", "memory_entity_links", "builder_activities", "legacy_personality_profiles"):
            assert db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0
        assert db.execute(text("SELECT review_state FROM source_memory_candidates")).scalar_one() == "pending"


def test_pg_terminal_review_receipt_is_idempotent(pg):
    source_id = _source(pg)
    candidate_id = _process(pg, source_id)
    factory, _, engine = pg
    key = str(uuid4())
    def operation(db, _):
        return asyncio.run(_review_service().review(db, db.get(User, 1), 1, candidate_id, action="preserve", expected_version=1, review_request_key=key))
    results = _race(factory, operation)
    assert all(isinstance(item, dict) for item in results)
    assert results[0]["canonical_memory_id"] == results[1]["canonical_memory_id"]
    with engine.connect() as db:
        generation = db.execute(text("SELECT source_generation FROM legacy_personality_profiles")).scalar_one()
        assert db.execute(text("SELECT COUNT(*) FROM memories")).scalar_one() == 1
        assert db.execute(text("SELECT COUNT(*) FROM builder_activities")).scalar_one() == 1
    with factory() as db:
        with pytest.raises(HTTPException) as error:
            asyncio.run(_review_service().review(db, db.get(User, 1), 1, candidate_id, action="skip", expected_version=1, review_request_key=key))
        assert error.value.status_code == 409
    with engine.connect() as db:
        assert db.execute(text("SELECT source_generation FROM legacy_personality_profiles")).scalar_one() == generation


@pytest.mark.parametrize("target", ["artifact", "job", "candidate_job", "candidate_evidence", "memory_source_link"])
def test_pg_source_evidence_artifact_cannot_cross_legacy(pg, target):
    source_id = _source(pg)
    candidate_id = _process(pg, source_id)
    factory, storage, engine = pg
    with engine.begin() as db:
        db.execute(text("INSERT INTO legacies (id, owner_user_id, subject_name, setup_status) VALUES (2, 1, 'Other', 'active')"))
    with factory() as db:
        other = MediaSourceService(storage=storage).create(db, db.get(User, 1), 2, kind="document", filename="other.txt", mime_type="text/plain", size_bytes=1, upload_request_key=str(uuid4()))
        artifact = db.scalar(select(MediaArtifact).where(MediaArtifact.source_id == other.id))
        job = db.scalar(select(MediaProcessingJob).where(MediaProcessingJob.source_id == other.id))
        evidence = db.scalar(select(SourceEvidence).where(SourceEvidence.source_id == source_id))
        if target == "artifact":
            evidence.artifact_id = artifact.id
        elif target == "job":
            evidence.job_id = job.id
        elif target == "candidate_job":
            db.get(SourceMemoryCandidate, candidate_id).job_id = job.id
        else:
            other_evidence = SourceEvidence(id=str(uuid4()), legacy_id=2, source_id=other.id, generation=1, job_id=job.id, artifact_id=artifact.id, stable_key="other", kind="text_span", text="Other source")
            db.add(other_evidence)
            db.commit()
            if target == "candidate_evidence":
                db.add(SourceCandidateEvidence(legacy_id=1, source_id=source_id, candidate_id=candidate_id, evidence_id=other_evidence.id))
            else:
                result = asyncio.run(_review_service().review(db, db.get(User, 1), 1, candidate_id, action="preserve", expected_version=1, review_request_key=str(uuid4())))
                link = db.scalar(select(MemorySourceLink).where(MemorySourceLink.memory_id == result["canonical_memory_id"]))
                link.evidence_id = other_evidence.id
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_pg_stale_generation_failure_cannot_change_current_source(pg):
    source_id = _source(pg)
    factory, storage, engine = pg
    claim = MediaWorker(sessions=factory, storage=storage).claim()
    with engine.begin() as db:
        db.execute(text("UPDATE media_sources SET generation=2, state='queued' WHERE id=:id"), {"id": source_id})
    result = asyncio.run(MediaIntelligenceService(RuleBasedSourceAnalysisProvider(), storage=storage)._fail(factory, *claim, "provider_failed"))
    assert result == "stale"
    with engine.connect() as db:
        assert db.execute(text("SELECT state FROM media_sources")).scalar_one() == "queued"


def test_pg_review_after_delete_is_a_conflict_without_provider_work(pg):
    source_id = _source(pg)
    candidate_id = _process(pg, source_id)
    factory, storage, _ = pg
    with factory() as db:
        MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_id)
        with pytest.raises(HTTPException) as error:
            asyncio.run(_review_service().review(db, db.get(User, 1), 1, candidate_id, action="preserve", expected_version=1, review_request_key=str(uuid4())))
        assert error.value.status_code in {409, 410}


def test_pg_edit_racing_preserve_cannot_duplicate_canonical_fact(pg):
    source_id = _source(pg)
    candidate_id = _process(pg, source_id)
    factory, storage, engine = pg
    with factory() as db:
        result = asyncio.run(_review_service().review(db, db.get(User, 1), 1, candidate_id, action="preserve", expected_version=1, review_request_key=str(uuid4())))
        memory_id = result["canonical_memory_id"]
    old_text = "Pallavi loved music."
    with engine.begin() as db:
        db.execute(text("UPDATE memories SET canonical_text=:text, normalized_fingerprint=:fp WHERE id=:id"), {"text": old_text, "fp": _fingerprint(old_text), "id": memory_id})
    second = _source(pg)
    second_candidate = _process(pg, second)
    entered, release = threading.Event(), threading.Event()
    target = "Pallavi loved jasmine flowers."

    class PausedEditProvider(FakeMemoryProvider):
        async def canonicalize_edit(self, legacy, source_text):
            return CanonicalEdit(canonical_text=target, source_language="english")

        async def embed(self, texts):
            entered.set()
            assert release.wait(timeout=10)
            return await super().embed(texts)

    def edit():
        from app.models.legacy import Legacy
        with factory() as db:
            try:
                asyncio.run(LivingMemoryService(PausedEditProvider()).edit(db, db.get(Legacy, 1), db.get(Memory, memory_id), target, None, 1))
                return "edited"
            except ValueError as exc:
                assert str(exc) == "duplicate_memory"
                db.rollback()
                return "duplicate"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(edit)
        try:
            assert entered.wait(timeout=10)
            with factory() as db:
                asyncio.run(_review_service().review(db, db.get(User, 1), 1, second_candidate, action="preserve", expected_version=1, review_request_key=str(uuid4())))
        finally:
            release.set()
        assert future.result(timeout=10) == "duplicate"
    with engine.connect() as db:
        assert db.execute(text("SELECT COUNT(*) FROM memories WHERE normalized_fingerprint=:fp AND status='active'"), {"fp": _fingerprint(target)}).scalar_one() == 1
