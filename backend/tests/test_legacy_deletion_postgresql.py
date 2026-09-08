"""Destructive workflow acceptance strictly in disposable loopback PostgreSQL."""
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import Base
from app.models.user import User
from app.models.legacy import Legacy
from app.models.collaboration import LegacyCollaborator
from app.models.media_source import MediaSource
from app.services.media_storage import LocalSourceStorage
from app.services.media_sources import MediaSourceService
from app.services.memory import LivingMemoryService
from app.services.legacy_deletion import request_deletion, finalize_one
from tests.test_visual_postgresql_l19 import ordered_race
from tests.test_legacy_deletion import (
    test_preview_names_target_counts_and_never_mutates,
    test_wrong_role_or_confirmation_cannot_delete,
    test_request_is_durable_idempotent_and_revokes_all_roles,
    test_file_erasure_precedes_registry_and_canonical_deletion,
    test_storage_failure_keeps_durable_registry_for_retry,
    test_stale_source_and_canonical_writers_cannot_resurrect_deleting_legacy,
    test_pending_file_cleanup_does_not_starve_another_legacy,
    test_http_requires_acknowledgement_hides_deleting_legacy_and_preserves_account,
    test_complete_evidence_timeline_story_graph_is_removed_in_fk_order,
    test_visual_profile_waits_for_actual_worker_purge,
)


@pytest.fixture
def pg(tmp_path, monkeypatch):
    url = os.environ.get('LEGACY_DELETE_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Requires explicit disposable LEGACY_DELETE_TEST_POSTGRES_URL')
    parsed = sa.engine.make_url(url)
    assert parsed.host == '127.0.0.1' and parsed.database == 'legacy_delete_test'
    admin = sa.create_engine(url)
    schema = 'delete_qa_' + uuid4().hex
    with admin.begin() as db:
        assert db.exec_driver_sql('SELECT current_database()').scalar_one() == 'legacy_delete_test'
        assert db.exec_driver_sql('SELECT version_num FROM public.alembic_version').scalar_one() == '0022_legacy_deletion'
        db.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = sa.create_engine(url, connect_args={'options': f'-csearch_path={schema} -clock_timeout=15000 -cstatement_timeout=20000'})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setenv('MEDIA_ENABLED', 'true')
    monkeypatch.setenv('MEDIA_LOCAL_STORAGE_PATH', str(tmp_path/'objects'))
    get_settings.cache_clear()
    storage = LocalSourceStorage(str(tmp_path/'objects'))
    with sessions.begin() as db:
        db.add_all([User(id=i, full_name='Synthetic QA', email=f'delete-{i}@example.invalid', password_hash='not-credentials') for i in (1,2,3)])
        db.flush()
        db.add_all([Legacy(id=1, owner_user_id=1, subject_name='Asha', setup_status='active'), Legacy(id=2, owner_user_id=3, subject_name='Other', setup_status='active')])
        db.flush()
        db.add(LegacyCollaborator(legacy_id=1, user_id=2, role='collaborator', status='active'))
    try:
        yield SimpleNamespace(engine=engine, sessions=sessions, storage=storage)
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()
        get_settings.cache_clear()


@pytest.fixture
def media_db(pg):
    return pg.sessions, pg.storage, get_settings()


def deletion(h):
    def run():
        with h.sessions() as db:
            return request_deletion(db, db.get(User,1), 1, 'DELETE LEGACY 1', source_service=MediaSourceService(storage=h.storage))
    return run


@pytest.mark.parametrize('delete_first', [True, False])
@pytest.mark.parametrize('operation', ['upload', 'canonical', 'delete'])
def test_deletion_serializes_admission_both_winning_orders(pg, delete_first, operation):
    def writer():
        with pg.sessions() as db:
            if operation == 'upload':
                return MediaSourceService(storage=pg.storage).create(db, db.get(User,1), 1, kind='document',
                    filename='qa.txt', mime_type='text/plain', size_bytes=5, upload_request_key=str(uuid4())).id
            if operation == 'delete':
                return deletion(pg)()
            try:
                LivingMemoryService.lock_canonical_legacy(db, 1)
            except ValueError:
                raise HTTPException(409, 'Legacy unavailable')
            # The canonical mutation fence is the same one used by normal save/edit.
            db.commit()
            return 'admitted'
    first, second = (deletion(pg), writer) if delete_first else (writer, deletion(pg))
    result = ordered_race(pg, first, second)
    assert result[0][0] == 'ok'
    if delete_first and operation != 'delete':
        assert result[1][0] == 'http'
    else:
        assert result[1][0] == 'ok'
    with pg.sessions() as db:
        assert db.get(Legacy,1).deletion_requested_at is not None
        assert db.get(Legacy,2).deletion_requested_at is None
        sources = db.scalars(sa.select(MediaSource).where(MediaSource.legacy_id==1)).all()
        assert all(source.state == 'deleting' for source in sources)
        assert len(sources) == int(operation == 'upload' and not delete_first)
