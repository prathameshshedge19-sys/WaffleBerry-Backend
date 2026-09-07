"""Opt-in live PostgreSQL acceptance for the L16 Phase B control plane."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import subprocess
import sys
import threading
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import build_engine
from app.models.media_source import MediaArtifact, MediaProcessingJob, MediaSource, SourceState
from app.models.user import User
from app.services.media_sources import MediaSourceService
from app.services.media_storage import LocalSourceStorage
from app.services.media_worker import MediaWorker


@pytest.fixture(scope="module")
def pg(tmp_path_factory):
    url = os.environ.get("L16_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires explicitly configured disposable L16 PostgreSQL database")
    parsed = make_url(url)
    assert parsed.host in {"localhost", "127.0.0.1"}
    assert parsed.database and parsed.database.startswith("l16_test")

    previous = {key: os.environ.get(key) for key in ("DATABASE_URL", "LEGARYA_DEBUG", "JWT_SECRET_KEY", "MEDIA_ENABLED", "MEDIA_LOCAL_STORAGE_PATH")}
    os.environ.update({"DATABASE_URL": url, "LEGARYA_DEBUG": "true", "JWT_SECRET_KEY": "test-only-secret-with-sufficient-length-123456", "MEDIA_ENABLED": "true", "MEDIA_LOCAL_STORAGE_PATH": str(tmp_path_factory.mktemp("media-objects"))})
    get_settings.cache_clear()

    # Alembic configures logging globally; isolate it from the pytest process.
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=Path(__file__).parents[1], check=True)
    engine = build_engine(url)
    with engine.begin() as db:
        assert db.execute(text("SELECT current_database()")).scalar_one() == parsed.database
        assert db.execute(text("SELECT version()")).scalar_one().startswith("PostgreSQL ")
        assert db.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == "0018_media_intelligence"
        tables = set(db.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'media_%'")).scalars())
        assert tables == {"media_sources", "media_artifacts", "media_processing_jobs"}
        constraints = set(db.execute(text("SELECT constraint_name FROM information_schema.table_constraints WHERE table_schema='public' AND table_name LIKE 'media_%'")).scalars())
        assert {"fk_media_artifacts_source_scope", "fk_media_jobs_source_scope", "uq_media_jobs_source_generation_kind_pipeline"} <= constraints
        indexes = set(db.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename LIKE 'media_%'")).scalars())
        assert {"ix_media_jobs_claim", "ix_media_jobs_source_generation", "ix_media_artifacts_source_state"} <= indexes
        db.execute(text("TRUNCATE media_processing_jobs, media_artifacts, media_sources, legacies, users RESTART IDENTITY CASCADE"))
        db.execute(text("INSERT INTO users (id, full_name, email, password_hash, is_verified) VALUES (1, 'Owner', 'l16-owner@example.com', 'x', true)"))
        db.execute(text("INSERT INTO legacies (id, owner_user_id, subject_name, setup_status) VALUES (1, 1, 'Asha', 'active')"))

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


def _reserve(factory, storage, *, key=None, size=5):
    with factory() as db:
        user = db.get(User, 1)
        source = MediaSourceService(storage=storage).create(
            db, user, 1, kind="document", mime_type="text/plain", filename="note.txt",
            size_bytes=size, upload_request_key=key or str(uuid4()),
        )
        return source.id


@pytest.fixture(autouse=True)
def clean_media(pg):
    with pg[2].begin() as db:
        db.execute(text("TRUNCATE media_processing_jobs, media_artifacts, media_sources RESTART IDENTITY CASCADE"))


def _receive(factory, storage, source_id, data=b"hello"):
    with factory() as db:
        return MediaSourceService(storage=storage).receive(db, db.get(User, 1), 1, source_id, data)


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
                return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, index) for index in range(2)]
        results = [future.result(timeout=30) for future in futures]
    return results


def test_postgresql_migration_constraints_and_composite_fk(pg):
    factory, storage, engine = pg
    source_id = _reserve(factory, storage)
    with factory() as db:
        source = db.get(MediaSource, source_id)
        db.add(MediaArtifact(
            id=str(uuid4()), legacy_id=999, source_id=source.id, generation=1,
            kind="text", logical_key="wrong-scope", storage_backend="local",
            object_key=f"wrong/{uuid4()}",
        ))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.scalar(select(func.count()).select_from(MediaSource)) == 1
    with engine.begin() as db:
        assert db.execute(text("SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public' AND tablename='media_sources'")).scalar_one() >= 5


def test_postgresql_concurrent_claim_vs_delete_cannot_resurrect_source(pg):
    factory, storage, _ = pg
    worker = MediaWorker(sessions=factory, storage=storage)
    for _ in range(5):
        source_id = _reserve(factory, storage)
        _receive(factory, storage, source_id)

        def operation(db, index):
            if index == 0:
                return MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_id).state
            return worker.claim()

        # _race synchronizes entry; either transaction may win the source lock.
        results = _race(factory, operation)
        with factory() as db:
            source = db.get(MediaSource, source_id)
            assert source.state == SourceState.DELETING.value
            assert source.generation == 2
            assert source.state not in {SourceState.QUEUED.value, SourceState.PROCESSING.value, SourceState.READY.value}
        if any(isinstance(result, tuple) for result in results):
            job_id, token = next(result for result in results if isinstance(result, tuple))
            with factory() as db:
                kind = db.get(MediaProcessingJob, job_id).kind
            if kind == "extract":
                assert worker._deferred_extract(job_id, token) in {"stale", "cancelled"}
            else:
                assert worker._purge(job_id, token) in {"purged", "stale"}


def test_postgresql_stale_completion_after_delete_is_fenced(pg):
    factory, storage, _ = pg
    source_id = _reserve(factory, storage)
    _receive(factory, storage, source_id)
    worker = MediaWorker(sessions=factory, storage=storage)
    claim = worker.claim()
    assert claim is not None
    with factory() as db:
        MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_id)
    assert worker._deferred_extract(*claim) in {"stale", "cancelled"}
    with factory() as db:
        source = db.get(MediaSource, source_id)
        assert source.state == SourceState.DELETING.value and source.generation == 2


def test_postgresql_retry_racing_with_delete_stays_deleted(pg):
    factory, storage, _ = pg
    source_id = _reserve(factory, storage)
    _receive(factory, storage, source_id)
    worker = MediaWorker(sessions=factory, storage=storage)
    assert worker.run_once() == "deferred"

    def operation(db, index):
        if index == 0:
            return MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_id).state
        return MediaSourceService(storage=storage).retry(db, db.get(User, 1), 1, source_id).state

    _race(factory, operation)
    with factory() as db:
        source = db.get(MediaSource, source_id)
        assert source.state == SourceState.DELETING.value and source.generation == 2


def test_postgresql_duplicate_admission_is_idempotent(pg):
    factory, storage, engine = pg
    key = str(uuid4())

    def operation(db, _):
        return MediaSourceService(storage=storage).create(
            db, db.get(User, 1), 1, kind="document", mime_type="text/plain", filename="same.txt",
            size_bytes=5, upload_request_key=key,
        ).id

    results = _race(factory, operation)
    assert results[0] == results[1]
    with engine.begin() as db:
        assert db.execute(text("SELECT COUNT(*) FROM media_sources WHERE upload_request_key = :key"), {"key": key}).scalar_one() == 1
        assert db.execute(text("SELECT COUNT(*) FROM media_processing_jobs WHERE source_id = (SELECT id FROM media_sources WHERE upload_request_key = :key)"), {"key": key}).scalar_one() == 1
