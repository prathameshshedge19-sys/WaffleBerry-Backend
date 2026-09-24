"""Real independent SQL transactions; deterministic in-memory S3 only."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.media_source import MediaSource
from app.models.storage_write import StorageWrite
from app.models.user import User
from app.models.legacy import Legacy
from app.services.account_deletion import request_account_deletion, finalize_account, now
from app.services.legacy_deletion import finalize_one
from app.services.media_sources import MediaSourceService
from app.services.media_storage import StorageError
from app.services.media_worker import MediaWorker
from tests.test_account_deletion_postgresql import pg_account, seed
from tests.test_storage_writes import boundary, drain


@pytest.mark.parametrize("lost", ["begin", "part", "complete-before", "complete-after"])
def test_pg_restart_can_cancel_each_ambiguous_stage(pg_account, lost):
    sessions, _, _ = pg_account
    storage, client = boundary(sessions)
    client.lost.add(lost)
    key = "legarya/synthetic/pg/" + str(uuid4())
    with pytest.raises(StorageError):
        storage.put(key, b"synthetic", "audio/wav")
    restarted, _ = boundary(sessions, client)
    drain(restarted)
    restarted.erase(key)
    assert not client.objects and not client.uploads
    with sessions() as db:
        assert db.scalar(select(StorageWrite.state)) == "erased"


def test_pg_deletion_during_part_has_no_sql_locks_or_stale_publication(pg_account):
    sessions, _, _ = pg_account
    owner, other = seed(sessions)
    storage, client = boundary(sessions)
    service = MediaSourceService(storage.storage)
    with sessions() as db:
        source_id = service.create(db, db.get(User, owner), 1, kind="document", filename="synthetic.txt",
            mime_type="text/plain", size_bytes=5, upload_request_key=str(uuid4())).id
    original = client.upload_part
    requests = []
    def during_part(**kw):
        with sessions.begin() as db:
            db.scalar(select(User).where(User.id == owner).with_for_update(nowait=True))
            db.scalar(select(Legacy).where(Legacy.id == 1).with_for_update(nowait=True))
            db.scalar(select(MediaSource).where(MediaSource.id == source_id).with_for_update(nowait=True))
            requests.append(request_account_deletion(db, owner, verified_support=True).id)
        return original(**kw)
    client.upload_part = during_part
    with sessions() as db, pytest.raises(HTTPException):
        service.receive(db, db.get(User, owner), 1, source_id, b"Hello")
    assert requests
    assert MediaWorker(sessions, storage.storage, purge_only=True).run_once() == "purged"
    assert finalize_one(sessions, storage.storage) == "legacy_erased"
    assert finalize_account(sessions, storage.storage, requests[0]) == "completed"
    with sessions() as db:
        assert db.get(User, owner) is None and db.get(User, other) is not None
    assert not client.objects and not client.uploads
