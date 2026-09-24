"""Real independent transactions in explicitly disposable loopback PostgreSQL."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.models.account_deletion import AccountDeletion
from app.models.legacy import Legacy
from app.models.user import User
from app.services.account_deletion import finalize_account, request_account_deletion
from app.services.legacy_deletion import finalize_one
from app.services.legacy_setup import create_collecting_legacy
from app.services.media_storage import LocalSourceStorage

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pg_account(tmp_path, monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("VOICE_TEMP_PATH", str(tmp_path / "private-voice"))
    get_settings.cache_clear()
    url = os.environ.get("PLAY_DELETION_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires explicit disposable PLAY_DELETION_TEST_POSTGRES_URL")
    parsed = sa.engine.make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost"} and parsed.database == "play_account_deletion_test"
    admin = sa.create_engine(url)
    schema = "play_delete_" + uuid4().hex
    with admin.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = sa.create_engine(url, pool_size=8, connect_args={"options": f'-csearch_path="{schema}" -clock_timeout=5000'})
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            for revision in reversed(list(ScriptDirectory(str(ROOT / "alembic")).walk_revisions())):
                revision.module.upgrade()
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield sessions, LocalSourceStorage(str(tmp_path / "objects")), engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()
        get_settings.cache_clear()


def seed(sessions):
    with sessions.begin() as db:
        first = User(full_name="Synthetic A", email="pg-a@example.invalid", password_hash="unused", is_verified=True)
        second = User(full_name="Synthetic B", email="pg-b@example.invalid", password_hash="unused", is_verified=True)
        db.add_all([first, second]); db.flush()
        db.add_all([Legacy(owner_user_id=first.id, subject_name="Owned", setup_status="active"),
                    Legacy(owner_user_id=second.id, subject_name="Other", setup_status="active")])
        return first.id, second.id


def test_pg_migration_downgrade_reupgrade_and_active_terminal_guards(pg_account):
    sessions, storage, engine = pg_account
    migration = ScriptDirectory(str(ROOT / "alembic")).get_revision("0027_account_deletion").module
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
            assert "account_deletions" not in sa.inspect(conn).get_table_names()
            migration.upgrade()
            assert "account_deletions" in sa.inspect(conn).get_table_names()
    first, _ = seed(sessions)
    with sessions.begin() as db:
        request_id = request_account_deletion(db, first, verified_support=True).id
    with pytest.raises(RuntimeError, match="Cannot discard"):
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
    assert finalize_one(sessions, storage) == "legacy_erased"
    assert finalize_account(sessions, storage, request_id) == "completed"
    with pytest.raises(RuntimeError, match="Cannot discard"):
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()


def test_pg_duplicate_request_and_two_finalizers_are_idempotent(pg_account):
    sessions, storage, _ = pg_account
    first, second = seed(sessions)
    barrier = threading.Barrier(2)
    def request():
        barrier.wait(timeout=10)
        with sessions.begin() as db:
            return request_account_deletion(db, first, verified_support=True).id
    with ThreadPoolExecutor(2) as pool:
        one, two = pool.submit(request), pool.submit(request)
        ids = [one.result(timeout=15), two.result(timeout=15)]
    assert ids[0] == ids[1]
    assert finalize_one(sessions, storage) == "legacy_erased"
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(finalize_account, sessions, storage, ids[0]) for _ in range(2)]
        results = [f.result(timeout=15) for f in futures]
        # A competing scratch-directory sweep may defer without blocking or
        # claiming absence. Its durable retry must converge on the same receipt.
        assert "completed" in results
        assert set(results) <= {"completed", "waiting_for_purge"}
    assert finalize_account(sessions, storage, ids[0]) == "completed"
    with sessions() as db:
        assert db.get(User, first) is None and db.get(User, second) is not None
        assert db.scalar(sa.select(sa.func.count()).select_from(AccountDeletion)) == 1


@pytest.mark.parametrize("iteration", range(5))
def test_pg_deletion_and_new_legacy_cannot_miss_owned_scope(pg_account, iteration):
    sessions, storage, _ = pg_account
    first, _ = seed(sessions)
    barrier = threading.Barrier(2)
    def late_create():
        with sessions.begin() as db:
            db.info["account_actor_id"] = first
            actor = db.get(User, first)
            barrier.wait(timeout=10)
            try:
                return create_collecting_legacy(db, actor).id
            except HTTPException as exc:
                db.rollback()
                assert exc.status_code in (401, 409)
                return None
    def deletion():
        barrier.wait(timeout=10)
        with sessions.begin() as db:
            return request_account_deletion(db, first, verified_support=True).id
    with ThreadPoolExecutor(2) as pool:
        write, erase = pool.submit(late_create), pool.submit(deletion)
        write.result(timeout=15)
        request_id = erase.result(timeout=15)
    with sessions() as db:
        assert all(legacy.deletion_requested_at is not None for legacy in db.scalars(sa.select(Legacy).where(Legacy.owner_user_id == first)))
    for _ in range(3):
        finalize_one(sessions, storage)
    assert finalize_account(sessions, storage, request_id) == "completed"


def test_pg_late_authenticated_bulk_mutation_is_denied(pg_account):
    sessions, storage, _ = pg_account
    first, _ = seed(sessions)
    with sessions() as stale:
        stale.info["account_actor_id"] = first
        stale.get(User, first)
        with sessions.begin() as db:
            request_account_deletion(db, first, verified_support=True)
        with pytest.raises(HTTPException) as failure:
            stale.execute(sa.update(User).where(User.id == first).values(full_name="late"))
        assert failure.value.status_code == 401


def test_pg_atomic_failure_can_retry_without_partial_deactivation(pg_account):
    sessions, storage, _ = pg_account
    first, _ = seed(sessions)
    with pytest.raises(RuntimeError):
        with sessions.begin() as db:
            request_account_deletion(db, first, verified_support=True)
            raise RuntimeError("synthetic transaction failure")
    with sessions() as db:
        assert db.get(User, first).deletion_requested_at is None
        assert db.scalar(sa.select(AccountDeletion.id)) is None
    with sessions.begin() as db:
        assert request_account_deletion(db, first, verified_support=True).state == "queued"


def test_pg_storage_io_holds_no_actor_source_or_legacy_lock(pg_account,monkeypatch):
    from app.models.collaboration import LegacyCollaborator
    from app.models.media_source import MediaSource
    from app.services.media_sources import MediaSourceService
    from app.services.media_worker import MediaWorker
    from tests.test_media_sources_l16 import _reserve
    sessions,storage,_=pg_account
    owner,visitor=seed(sessions)
    with sessions.begin() as db:
        db.add(LegacyCollaborator(legacy_id=1,user_id=visitor,status="active"))
    source_id=_reserve(sessions,storage,user_id=visitor)
    with sessions() as db:
        MediaSourceService(storage).receive(db,db.get(User,visitor),1,source_id,b"Hello")
    with sessions.begin() as db:
        request_id=request_account_deletion(db,visitor,verified_support=True).id
    checked=[];erase=storage.delete
    def assert_unlocked(key,**options):
        with sessions.begin() as probe:
            probe.scalar(sa.select(User.id).where(User.id==visitor).with_for_update(nowait=True))
            probe.scalar(sa.select(Legacy.id).where(Legacy.id==1).with_for_update(nowait=True))
            probe.scalar(sa.select(MediaSource.id).where(MediaSource.id==source_id).with_for_update(nowait=True))
        checked.append(True)
        return erase(key,**options)
    monkeypatch.setattr(storage,"delete",assert_unlocked)
    assert MediaWorker(sessions,storage,purge_only=True).run_once()=="purged"
    while finalize_one(sessions,storage)=="legacy_erased":
        pass
    assert finalize_account(sessions,storage,request_id)=="completed"
    assert len(checked)>=2
