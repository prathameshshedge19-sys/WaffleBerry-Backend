from sqlalchemy import select

from app.models.storage_write import StorageWrite
from app.models.visual_companion import VisualCompanionAsset as Asset, VisualCompanionVersion as Version
from tests.test_storage_writes import boundary
from tests.test_visual_worker_l19 import harness


def test_visual_crash_after_dispatch_admission_never_becomes_unresolvable(harness):
    h = harness
    ids, claim, _, rows = h.reserved()
    storage, client = boundary(h.sessions)
    storage.writes.clock = h.clock
    h.worker.storage = storage
    with h.sessions.begin() as db:
        for asset in db.scalars(select(Asset)):
            asset.storage_backend = "s3"
            asset.storage_bucket = storage.bucket_name
            asset.encryption_key_id = storage.encryption_key_id
    h.worker.begin_put(*claim, rows[0].id)
    # Crash before storage.put: the same SQL commit already reserved its handle.
    with h.sessions() as db:
        assert db.scalar(select(StorageWrite.state)) == "reserved"
        assert db.get(Asset, rows[0].id).write_state == "dispatching"
    h.tombstone(ids)
    h.clock.advance(121)
    assert h.worker.run_once() == "retry_wait"
    h.clock.advance(61)
    assert h.worker.run_once() == "purged"
    with h.sessions() as db:
        assert db.get(Version, ids.version).state == "purged"
        assert db.get(Asset, rows[0].id).write_confirmed_at is None
    assert not client.objects and not client.uploads
