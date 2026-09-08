"""Worker lifecycle tests; PostgreSQL cases use their own disposable schema.

Set L19_TEST_POSTGRES_URL to loopback l19_test_phase_b for real independent
connection races. The lifecycle fake isolates these tests from native geometry;
the provider's full output validation has separate coverage.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import Base, build_engine
from app.models.legacy import Legacy
from app.models.media_source import MediaArtifact, MediaSource
from app.models.user import User
from app.models.visual_companion import (
    VisualCompanion, VisualCompanionVersion as Version,
    VisualCompanionAsset as Asset, VisualGenerationJob as Job,
)
from app.schemas.visual_companion import Activation, VersionCreate
from app.services.media_sources import MediaSourceService
from app.services.media_storage import LocalSourceStorage, StorageError
from app.services.visual_companions import VisualCompanionService, digest, enqueue_purge, lock_scope
from app.services.visual_storage import VisualStorage
from app.services.visual_worker import VisualWorker, StaleClaim

VALIDATE_SPECS = VisualWorker._specs


class Clock:
    def __init__(self):
        self.value = datetime.now(timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class LifecycleProvider:
    provider_name = "fake-test-only"
    model_digest = hashlib.sha256(b"l19-fake-test-only").hexdigest()

    def prepare(self, data, crop, request_identity):
        rig = json.dumps({"request_identity_sha256": digest(request_identity)}).encode()
        return SimpleNamespace(assets={"poster": b"poster", "texture_atlas": b"atlas", "rig": rig})


def lifecycle_specs(bundle):
    if set(bundle.assets) != {"poster", "texture_atlas", "rig"}:
        raise ValueError("incomplete")
    return [{"logical_role": role, "mime_type": "application/json" if role == "rig" else "image/png"}
            for role in sorted(bundle.assets)]


@pytest.fixture(params=["sqlite", "postgresql"])
def harness(request, tmp_path, monkeypatch):
    schema = None
    if request.param == "postgresql":
        url = os.environ.get("L19_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Requires disposable loopback L19_TEST_POSTGRES_URL")
        parsed = make_url(url)
        assert parsed.host in {"127.0.0.1", "localhost", "::1"}
        assert parsed.database == "l19_test_phase_b"
        schema = "visual_worker_" + uuid4().hex
        admin = create_engine(url)
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema} -clock_timeout=5000"})
    else:
        engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    clock = Clock()
    source = LocalSourceStorage(str(tmp_path / "sources"))
    storage = VisualStorage(LocalSourceStorage(str(tmp_path / "derivatives")))
    provider = LifecycleProvider()
    monkeypatch.setattr(VisualWorker, "_specs", staticmethod(lifecycle_specs))
    worker = VisualWorker(sessions, storage, provider=provider, source_storage=source, clock=clock)
    service = VisualCompanionService(provider_name=provider.provider_name, model_digest=provider.model_digest)

    def seed():
        with sessions.begin() as db:
            owner = User(full_name="Synthetic Owner", email=f"{uuid4()}@example.test", password_hash="unused", is_verified=True)
            db.add(owner)
            db.flush()
            legacy = Legacy(owner_user_id=owner.id, subject_name="Synthetic subject", setup_status="active")
            db.add(legacy)
            db.flush()
            source_id, artifact_id = str(uuid4()), str(uuid4())
            from PIL import Image
            encoded = io.BytesIO()
            Image.new("RGB", (256, 256), (80, 100, 120)).save(encoded, "PNG")
            data = encoded.getvalue()
            sha = hashlib.sha256(data).hexdigest()
            src = MediaSource(id=source_id, legacy_id=legacy.id, uploader_user_id=owner.id,
                kind="image", processing_purpose="visual_reference", original_filename="synthetic.png",
                declared_mime_type="image/png", detected_mime_type="image/png",
                declared_size_bytes=len(data), size_bytes=len(data), sha256=sha,
                state="ready", safety_state="clean", generation=1,
                upload_request_key=str(uuid4()), upload_request_digest="0" * 64,
                upload_expires_at=clock() + timedelta(hours=1))
            db.add(src)
            db.flush()
            db.add(MediaArtifact(id=artifact_id, legacy_id=legacy.id, source_id=source_id,
                generation=1, kind="original", logical_key="original", storage_backend="local",
                object_key=source_id, state="available", byte_size=len(data), sha256=sha, mime_type="image/png"))
            db.flush()
            version = service.admit(db, owner.id, legacy.id, VersionCreate(
                source_id=source_id, crop={"x": 0, "y": 0, "width": 1, "height": 1},
                confirmed=True, confirmation_copy_version="l19-likeness-v1",
                request_key=uuid4(), expected_revision=0))
            db.flush()
            job_id = db.scalar(select(Job.id).where(Job.version_id == version.id, Job.kind == "prepare"))
            ids = SimpleNamespace(legacy=legacy.id, owner=owner.id, source=source_id,
                                  artifact=artifact_id, version=version.id, profile=version.companion_id, job=job_id)
        source.put(source_id, data, content_type="image/png")
        # Domain admission uses the wall clock; move deterministic time just past it.
        clock.value = max(clock.value, datetime.now(timezone.utc))
        return ids

    def reserved():
        ids = seed()
        claim = worker.claim()
        assert claim and claim[0] == ids.job
        bundle = make_bundle(ids)
        rows = worker.reserve_assets(*claim, bundle)
        return ids, claim, bundle, rows

    def make_bundle(ids):
        with sessions.begin() as db:
            scope = worker._scope(db, ids.job)
            identity = worker._identity(scope[2])
        return provider.prepare(None, None, identity)

    def upload(claim, bundle, rows):
        for row in rows:
            stored = storage.put(row.key, bundle.assets[row.role], row.mime_type)
            worker._record_put(*claim, row.id, stored)

    def tombstone(ids):
        with sessions.begin() as db:
            _, profile, versions, _ = lock_scope(db, ids.legacy)
            profile.enabled = False
            profile.current_version_id = profile.desired_version_id = None
            profile.deleted_at = clock()
            enqueue_purge(db, profile, versions[ids.version], clock())

    try:
        yield SimpleNamespace(worker=worker, sessions=sessions, clock=clock, storage=storage,
            source=source, provider=provider, seed=seed, reserved=reserved, upload=upload,
            tombstone=tombstone, bundle=make_bundle, engine=engine, postgres=request.param == "postgresql")
    finally:
        engine.dispose()
        if schema:
            # This random schema was created by this fixture, never public.
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def test_ready_is_atomic_private_and_reservations_precede_put(harness, monkeypatch):
    h = harness
    ids = h.seed()
    original_put = h.storage.put
    calls = []

    def put(key, data, mime):
        with h.sessions() as db:
            assets = db.scalars(select(Asset).where(Asset.version_id == ids.version)).all()
            assert len(assets) == 3
            assert {a.state for a in assets} == {"reserved"}
            calls.append(key)
        return original_put(key, data, mime)

    monkeypatch.setattr(h.storage, "put", put)
    assert h.worker.run_once() == "ready"
    with h.sessions() as db:
        profile = db.get(VisualCompanion, ids.profile)
        assert not profile.enabled and profile.current_version_id is None
        assert profile.desired_version_id == ids.version
        assert db.get(Job, ids.job).state == "succeeded"
        assert {a.state for a in db.scalars(select(Asset))} == {"available"}
        # Source remains private and unchanged; presentation creates no facts.
        for name in ("memories", "source_evidence", "conversations", "messages"):
            if name in Base.metadata.tables:
                assert db.scalar(select(func.count()).select_from(Base.metadata.tables[name])) == 0
    assert len(set(calls)) == 3 and h.source.exists(ids.source)


def test_reservation_rollback_leaves_no_untracked_put(harness):
    h = harness
    ids = h.seed()
    claim = h.worker.claim()

    def fail_assets(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO visual_companion_assets"):
            raise RuntimeError("injected transaction rollback")

    event.listen(h.engine, "before_cursor_execute", fail_assets)
    try:
        with pytest.raises(RuntimeError):
            h.worker.reserve_assets(*claim, h.bundle(ids))
    finally:
        event.remove(h.engine, "before_cursor_execute", fail_assets)
    with h.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 0
        assert db.get(Job, ids.job).state == "running"


@pytest.mark.parametrize("change", ["lease", "source_generation", "source_hash", "artifact", "owner", "desired", "expiry", "delete"])
def test_publication_rechecks_every_fence(harness, change):
    h = harness
    ids, claim, bundle, rows = h.reserved()
    h.upload(claim, bundle, rows)
    with h.sessions.begin() as db:
        _, profile, versions, sources = lock_scope(db, ids.legacy)
        version = versions[ids.version]
        if change == "lease":
            db.get(Job, ids.job).lease_token = str(uuid4())
        elif change == "source_generation":
            sources[ids.source].generation += 1
        elif change == "source_hash":
            sources[ids.source].sha256 = "f" * 64
        elif change == "artifact":
            db.get(MediaArtifact, ids.artifact).state = "purge_pending"
        elif change == "owner":
            version.confirmed_by_user_id = None
        elif change == "desired":
            profile.desired_version_id = None
        elif change == "expiry":
            version.expires_at = h.clock() - timedelta(seconds=1)
        else:
            profile.desired_version_id = None
            profile.deleted_at = h.clock()
    assert h.worker.publish(*claim, bundle) == "stale"
    with h.sessions() as db:
        assert {a.state for a in db.scalars(select(Asset))} == {"purge_pending"}
        assert db.scalar(select(Job).where(Job.kind == "purge")).state == "queued"


def test_unexpired_writer_blocks_global_claim_after_lease_expiry(harness):
    h = harness
    first = h.seed()
    second = h.seed()
    claim = h.worker.claim()
    assert claim[0] in {first.job, second.job}
    h.clock.advance(31)
    assert h.worker.claim() is None
    h.clock.advance(90)
    next_claim = h.worker.claim()
    assert next_claim is not None and next_claim[1] != claim[1]


def test_cancelled_provider_deadline_still_blocks_another_prepare(harness):
    h = harness
    ids = h.seed()
    h.worker.claim()
    h.tombstone(ids)
    h.seed()
    # Purge may run, but cannot call erasure complete while CPU writer lives.
    assert h.worker.run_once() == "retry_wait"
    assert h.worker.claim() is None
    h.clock.advance(121)
    assert h.worker.run_once() == "purged"
    assert h.worker.claim() is not None


def test_late_put_is_swept_after_deadline_and_stale_lease(harness):
    h = harness
    ids, claim, bundle, rows = h.reserved()
    h.tombstone(ids)
    assert h.worker.run_once() == "retry_wait"
    # Object arrives after delete committed and invalidated the preparation lease.
    h.upload(claim, bundle, rows)
    assert h.worker.publish(*claim, bundle) == "stale"
    h.clock.advance(121)
    assert h.worker.run_once() == "purged"
    assert all(not h.storage.storage.exists(a.key) for a in rows)
    with h.sessions() as db:
        assert {a.state for a in db.scalars(select(Asset))} == {"purged"}
        assert db.get(VisualCompanion, ids.profile).deleted_at is not None


def test_stale_cleanup_rearms_already_completed_purge(harness):
    h = harness
    ids, claim, bundle, rows = h.reserved()
    h.tombstone(ids)
    h.clock.advance(121)
    assert h.worker.run_once() == "purged"
    h.upload(claim, bundle, rows)
    h.worker.cleanup_attempt(*claim)
    assert h.worker.run_once() == "purged"
    assert all(not h.storage.storage.exists(a.key) for a in rows)


def test_purge_retries_beyond_preparation_cap(harness, monkeypatch):
    h = harness
    ids, claim, bundle, rows = h.reserved()
    h.upload(claim, bundle, rows)
    h.tombstone(ids)
    h.clock.advance(121)
    erase = h.storage.erase

    def outage(key):
        raise StorageError("test_private_error")

    monkeypatch.setattr(h.storage, "erase", outage)
    for _ in range(4):
        assert h.worker.run_once() == "retry_wait"
        with h.sessions() as db:
            assert db.scalar(select(Job).where(Job.kind == "purge")).last_error_code == "visual_erasure_unconfirmed"
        h.clock.advance(301)
    monkeypatch.setattr(h.storage, "erase", erase)
    assert h.worker.run_once() == "purged"
    with h.sessions() as db:
        purge = db.scalar(select(Job).where(Job.kind == "purge"))
        assert purge.attempts == 5 and purge.state == "succeeded"


def test_uncertain_put_retry_uses_new_keys(harness, monkeypatch):
    h = harness
    ids = h.seed()
    put = h.storage.put
    keys = []

    def uncertain(key, data, mime):
        keys.append(key)
        put(key, data, mime)
        raise StorageError("unknown_write_outcome")

    monkeypatch.setattr(h.storage, "put", uncertain)
    assert h.worker.run_once() == "retry_wait"
    h.clock.advance(121)
    assert h.worker.run_once() == "purged"  # only failed attempt assets
    monkeypatch.setattr(h.storage, "put", put)
    assert h.worker.run_once() == "ready"
    with h.sessions() as db:
        assets = list(db.scalars(select(Asset)))
        assert len(assets) == 6 and len({a.object_key for a in assets}) == 6
        assert len({a.attempt_id for a in assets}) == 2
        assert db.get(Job, ids.job).attempts == 2
        assert sum(a.state == "available" for a in assets) == 3
    assert not h.storage.storage.exists(keys[0])


def test_source_bytes_and_storage_identity_checked_before_provider(harness, monkeypatch):
    h = harness
    ids = h.seed()
    with h.sessions.begin() as db:
        db.get(MediaArtifact, ids.artifact).encryption_key_id = "unexpected-key"
    monkeypatch.setattr(h.provider, "prepare", lambda *a: pytest.fail("provider must not run"))
    assert h.worker.run_once() == "failed"
    with h.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 0


def test_changed_source_bytes_never_reach_provider(harness, monkeypatch):
    h = harness
    ids = h.seed()
    monkeypatch.setattr(h.source, "open", lambda key: io.BytesIO(b"different source"))
    monkeypatch.setattr(h.provider, "prepare", lambda *a: pytest.fail("provider must not run"))
    assert h.worker.run_once() == "failed"
    with h.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 0


def test_wrong_request_bundle_rejected_before_reservation(harness):
    h = harness
    h.seed()
    claim = h.worker.claim()
    wrong_bundle = h.provider.prepare(None, None, {"request_digest": "a" * 64})
    with pytest.raises(ValueError, match="identity_mismatch"):
        h.worker.reserve_assets(*claim, wrong_bundle)
    with h.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Asset)) == 0


def test_verification_failure_exposes_no_partial_bundle(harness, monkeypatch):
    h = harness
    ids = h.seed()
    monkeypatch.setattr(h.storage, "verify", lambda *a, **k: False)
    assert h.worker.run_once() == "retry_wait"
    with h.sessions() as db:
        assert not any(a.state == "available" for a in db.scalars(select(Asset)))
        assert db.get(VisualCompanion, ids.profile).current_version_id is None


def test_two_transient_preparation_attempts_then_terminal(harness, monkeypatch):
    h = harness
    ids = h.seed()

    def unavailable(key):
        raise StorageError("source_unavailable")

    monkeypatch.setattr(h.source, "open", unavailable)
    assert h.worker.run_once() == "retry_wait"
    h.clock.advance(31)
    assert h.worker.run_once() == "purged"  # empty asset-only cleanup job
    assert h.worker.run_once() == "failed"
    h.clock.advance(301)
    assert h.worker.run_once() == "purged"
    assert h.worker.run_once() == "idle"
    with h.sessions() as db:
        assert db.get(Job, ids.job).attempts == 2


def test_fake_bundle_interface_end_to_end(harness, monkeypatch):
    from app.services import visual_provider, visual_reference
    h = harness
    ids = h.seed()
    h.worker.provider = visual_provider.FakePortraitRigProvider()
    # Windows has no approved decoder sandbox. Only this synthetic fixture
    # injects the pure decoder; production must keep failing closed.
    monkeypatch.setattr(visual_provider, "normalize_crop", visual_reference._decode_image)
    monkeypatch.setattr(VisualWorker, "_specs", staticmethod(VALIDATE_SPECS))
    assert h.worker.run_once() == "ready"
    with h.sessions() as db:
        assets = list(db.scalars(select(Asset)))
        assert len(assets) == 3
        assert next(a for a in assets if a.logical_role == "poster").width == 256
        assert next(a for a in assets if a.logical_role == "texture_atlas").width == 512
        assert not db.get(VisualCompanion, ids.profile).enabled


def test_s3_source_uses_bounded_registered_original_reader(harness, monkeypatch):
    h = harness
    ids = h.seed()
    with h.sessions.begin() as db:
        db.get(MediaArtifact, ids.artifact).storage_backend = "s3"
    with h.source.open(ids.source) as stream:
        data = stream.read()
    monkeypatch.setattr(h.source, "backend_name", "s3")
    monkeypatch.setattr(h.source, "open", lambda *a: pytest.fail("unbounded S3 read"))
    calls = []
    def bounded(storage, key, version):
        assert storage.backend_name == 's3' and key == ids.source and version is None
        calls.append(key)
        return data
    monkeypatch.setattr(VisualStorage, 'read_original', bounded)
    assert h.worker.run_once() == "ready"
    assert calls == [ids.source]


def test_s3_absence_observation_never_claims_final_remote_erasure(harness, monkeypatch):
    h = harness
    ids, claim, bundle, rows = h.reserved()
    for row in rows:
        h.worker.begin_put(*claim, row.id)
    h.tombstone(ids)
    h.clock.advance(121)
    local_operations = VisualStorage(h.storage.storage)
    monkeypatch.setattr(h.storage, "backend_name", "s3")
    monkeypatch.setattr(h.storage, 'bucket_name', 'synthetic-qa')
    with h.sessions.begin() as db:
        for asset in db.scalars(select(Asset)):
            asset.storage_backend, asset.storage_bucket = 's3', 'synthetic-qa'
    # Simulate a truthful S3 absence observation without invoking a network
    # adapter: only the worker's remote-completion decision is under test.
    monkeypatch.setattr(h.storage, "erase", local_operations.erase)
    monkeypatch.setattr(h.storage, 'reconcile_write', local_operations.verify)
    assert h.worker.run_once() == "retry_wait"
    with h.sessions() as db:
        assert {a.state for a in db.scalars(select(Asset))} == {"purge_pending"}
        job = db.scalar(select(Job).where(Job.kind == "purge"))
        assert job.last_error_code == "visual_remote_erasure_unproven"
    # Even a remote commit later than the client deadline remains registered.
    local_operations.put(rows[0].key, bundle.assets[rows[0].role], rows[0].mime_type)
    h.clock.advance(301)
    assert h.worker.run_once() == "retry_wait"
    assert not h.storage.storage.exists(rows[0].key)
    with h.sessions() as db:
        assert db.get(Asset, rows[0].id).write_state == 'confirmed'
        assert db.get(Version, ids.version).state == 'purge_pending'


def test_confirmed_remote_writes_finish_only_after_stable_absence(harness, monkeypatch):
    h = harness
    ids, claim, bundle, rows = h.reserved()
    h.upload(claim, bundle, rows)
    local = VisualStorage(h.storage.storage)
    monkeypatch.setattr(h.storage, 'backend_name', 's3')
    monkeypatch.setattr(h.storage, 'bucket_name', 'synthetic-qa')
    monkeypatch.setattr(h.storage, 'erase', local.erase)
    with h.sessions.begin() as db:
        for asset in db.scalars(select(Asset)):
            asset.storage_backend, asset.storage_bucket = 's3', 'synthetic-qa'
    h.tombstone(ids)
    h.clock.advance(121)
    assert h.worker.run_once() == 'retry_wait'
    h.clock.advance(61)
    assert h.worker.run_once() == 'purged'
    with h.sessions() as db:
        assert db.get(Version, ids.version).state == 'purged'
        assert all(a.absence_checks >= 2 for a in db.scalars(select(Asset)))
    assert all(not h.storage.storage.exists(row.key) for row in rows)


def test_put_dispatch_is_durable_and_never_replayed(harness):
    h = harness
    ids, claim, bundle, rows = h.reserved()
    h.worker.begin_put(*claim, rows[0].id)
    with pytest.raises(StaleClaim):
        h.worker.begin_put(*claim, rows[0].id)
    with h.sessions() as db:
        assert db.get(Asset, rows[0].id).write_state == 'dispatching'


def test_storage_scope_change_never_dispatches_or_finalizes_purge(harness, monkeypatch):
    h = harness
    ids, claim, bundle, rows = h.reserved()
    with h.sessions.begin() as db:
        for asset in db.scalars(select(Asset)):
            asset.storage_backend = 's3'
            asset.storage_bucket = 'registered-private-bucket'
    with pytest.raises(StorageError):
        h.worker.begin_put(*claim, rows[0].id)
    with pytest.raises(StorageError):
        h.worker.publish(*claim, bundle)
    h.tombstone(ids)
    h.clock.advance(121)
    monkeypatch.setattr(h.storage, 'erase', lambda key: pytest.fail('Wrong storage must not be touched'))
    assert h.worker.run_once() == 'retry_wait'
    with h.sessions() as db:
        assert db.get(Version, ids.version).state == 'purge_pending'
        assert all(a.state=='purge_pending' for a in db.scalars(select(Asset)))


def test_expired_candidate_purges_with_preparation_disabled(harness):
    h = harness
    ids = h.seed()
    h.worker.provider = None
    h.clock.advance(7 * 86400 + 1)
    assert h.worker.run_once() == "purged"
    with h.sessions() as db:
        assert db.get(VisualCompanion, ids.profile).desired_version_id is None
        assert db.get(Job, ids.job).state == "cancelled"


def test_heartbeat_never_extends_hard_deadline(harness):
    h = harness
    ids = h.seed()
    claim = h.worker.claim()
    for _ in range(11):
        h.clock.advance(10)
        assert h.worker.heartbeat(*claim)
    with h.sessions() as db:
        job = db.get(Job, ids.job)
        assert job.lease_expires_at == job.writer_deadline
    h.clock.advance(10)
    assert not h.worker.heartbeat(*claim)


def test_two_postgresql_workers_claim_only_one_global_prepare(harness):
    h = harness
    if not h.postgres:
        pytest.skip("Row/advisory-lock concurrency requires PostgreSQL")
    h.seed()
    h.seed()
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        return h.worker.claim()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: claim(), range(2)))
    assert sum(outcome is not None for outcome in outcomes) == 1


def test_delete_commits_while_provider_has_no_sql_locks(harness, monkeypatch):
    h = harness
    if not h.postgres:
        pytest.skip("Independent concurrent transactions require PostgreSQL")
    ids = h.seed()
    entered, release = threading.Event(), threading.Event()
    prepare = h.provider.prepare

    def blocked(*args):
        entered.set()
        assert release.wait(timeout=10)
        return prepare(*args)

    monkeypatch.setattr(h.provider, "prepare", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(h.worker.run_once)
        assert entered.wait(timeout=5)
        try:
            h.tombstone(ids)  # Must commit before releasing the provider.
        finally:
            release.set()
        assert result.result(timeout=10) == "stale"
    with h.sessions() as db:
        assert db.get(VisualCompanion, ids.profile).deleted_at is not None
        assert db.scalar(select(func.count()).select_from(Asset)) == 0


@pytest.fixture
def pg_worker_race(harness, monkeypatch):
    """Real bundle bytes and validation, with only synthetic decoding injected."""
    if not harness.postgres:
        pytest.skip("Observed PostgreSQL lock races require independent live connections")
    from app.services import visual_provider, visual_reference
    h = harness
    monkeypatch.setattr(visual_provider, "normalize_crop", visual_reference._decode_image)
    monkeypatch.setattr(VisualWorker, "_specs", staticmethod(VALIDATE_SPECS))
    h.worker.provider = visual_provider.FakePortraitRigProvider()

    def bundle(ids):
        with h.sessions.begin() as db:
            scope = h.worker._scope(db, ids.job)
            version = scope[2]
            original = db.get(MediaArtifact, version.source_artifact_id)
            key, crop, identity = original.object_key, dict(version.crop_json), h.worker._identity(version)
        with h.source.open(key) as stream:
            data = stream.read()
        return h.worker.provider.prepare(data, crop, identity)

    h.bundle = bundle
    return h


def independent_worker(h):
    # Distinct Session factories; the ordered-race helper also proves distinct
    # PostgreSQL backend PIDs and observes pg_blocking_pids before release.
    sessions = sessionmaker(bind=h.engine, autoflush=False, expire_on_commit=False)
    return VisualWorker(sessions, h.storage, provider=h.worker.provider,
                        source_storage=h.source, clock=h.clock)


def prepare_registered(h, ids, worker=None):
    worker = worker or h.worker
    claim = worker.claim()
    assert claim and claim[0] == ids.job
    bundle = h.bundle(ids)
    rows = worker.reserve_assets(*claim, bundle)
    h.upload(claim, bundle, rows)
    return claim, bundle, rows


def ordered_worker_race(h, first, second, *, gate_occurrence):
    # Reuse the PG agent's public gate; no domain ready_fixture is used here.
    from tests.test_visual_postgresql_l19 import ordered_race
    return ordered_race(h, first, second, gate_occurrence=gate_occurrence)


@pytest.mark.parametrize('delete_first', [True, False])
def test_dispatch_fence_vs_delete_both_lock_orders(pg_worker_race, delete_first):
    h = pg_worker_race
    ids = h.seed()
    claim = h.worker.claim()
    rows = h.worker.reserve_assets(*claim, h.bundle(ids))
    other = independent_worker(h)
    def dispatch():
        try:
            other.begin_put(*claim, rows[0].id)
            return 'dispatched'
        except StaleClaim:
            return 'stale'
    actions = (lambda: h.tombstone(ids), dispatch) if delete_first else (dispatch, lambda: h.tombstone(ids))
    result = ordered_worker_race(h, *actions, gate_occurrence=1)
    assert result[1 if delete_first else 0] == ('ok', 'stale' if delete_first else 'dispatched')
    with h.sessions() as db:
        asset = db.get(Asset, rows[0].id)
        assert asset.write_state == ('reserved' if delete_first else 'dispatching')
        assert asset.state == 'purge_pending'
        assert db.get(Version, ids.version).state == 'purge_pending'
        assert db.get(Job, ids.job).state == 'cancelled'
        assert not db.get(VisualCompanion, ids.profile).enabled


def test_duplicate_dispatch_has_one_committed_winner(pg_worker_race):
    h = pg_worker_race
    ids = h.seed()
    claim = h.worker.claim()
    rows = h.worker.reserve_assets(*claim, h.bundle(ids))
    other = independent_worker(h)
    def dispatch(worker):
        try:
            worker.begin_put(*claim, rows[0].id)
            return 'dispatched'
        except StaleClaim:
            return 'stale'
    assert ordered_worker_race(h, lambda: dispatch(h.worker), lambda: dispatch(other),
        gate_occurrence=1) == (('ok','dispatched'),('ok','stale'))
    with h.sessions() as db:
        assert db.get(Asset, rows[0].id).write_state == 'dispatching'
        assert db.scalar(select(func.count()).select_from(Asset)) == 3


@pytest.mark.parametrize("delete_first", [True, False])
def test_source_delete_vs_publication_both_lock_orders(pg_worker_race, delete_first):
    h = pg_worker_race
    ids = h.seed()
    claim, bundle, rows = prepare_registered(h, ids)

    def delete():
        with h.sessions() as db:
            MediaSourceService(storage=h.source).delete(db, db.get(User, ids.owner), ids.legacy, ids.source)
        return "deleted"

    publish = lambda: h.worker.publish(*claim, bundle)
    results = ordered_worker_race(h, *((delete, publish) if delete_first else (publish, delete)),
                                 gate_occurrence=1 if delete_first else 2)
    assert all(status == "ok" for status, _ in results)
    assert results[1 if delete_first else 0][1] == ("stale" if delete_first else "ready")
    with h.sessions() as db:
        profile = db.get(VisualCompanion, ids.profile)
        assert profile.current_version_id is None and profile.desired_version_id is None
        assert not profile.enabled
        assert db.get(MediaSource, ids.source).state == "deleting"
        assert db.get(MediaSource, ids.source).generation == 2
        assert db.get(Version, ids.version).state == "purge_pending"
        assert db.get(Job, ids.job).state == "cancelled"
        assert {a.state for a in db.scalars(select(Asset))} == {"purge_pending"}
        assert db.scalar(select(Job).where(Job.kind == "purge")) is not None


@pytest.mark.parametrize("activate_first", [True, False])
def test_new_activation_vs_stale_completion_both_lock_orders(pg_worker_race, activate_first):
    h = pg_worker_race
    ids = h.seed()
    old_claim, old_bundle, _ = prepare_registered(h, ids)
    service = VisualCompanionService()
    with h.sessions.begin() as db:
        revision = db.get(VisualCompanion, ids.profile).revision
        version = service.admit(db, ids.owner, ids.legacy, VersionCreate(
            source_id=ids.source, crop={"x": 0, "y": 0, "width": 1, "height": 1},
            confirmed=True, confirmation_copy_version="l19-likeness-v1",
            request_key=uuid4(), expected_revision=revision))
        db.flush()
        new_id = version.id
        new_job = db.scalar(select(Job.id).where(Job.version_id == new_id, Job.kind == "prepare"))
    new_ids = SimpleNamespace(**{**vars(ids), "version": new_id, "job": new_job})
    h.clock.advance(121)
    # A claimed purge may be in flight while another preparation runs. Leave
    # erasure pending so the stale completion has real old objects to verify.
    purge_claim = h.worker.claim()
    with h.sessions() as db:
        assert db.get(Job, purge_claim[0]).kind == "purge"
    new_worker = independent_worker(h)
    new_claim, new_bundle, _ = prepare_registered(h, new_ids, new_worker)
    assert new_worker.publish(*new_claim, new_bundle) == "ready"
    with h.sessions() as db:
        expected_revision = db.get(VisualCompanion, ids.profile).revision
        payload = Activation(version_id=new_id, expected_revision=expected_revision,
            bundle_digest=db.get(Version, new_id).bundle_digest, approved=True)

    def activate():
        with h.sessions.begin() as db:
            service.activate(db, ids.owner, ids.legacy, payload)
        return "activated"

    stale_publish = lambda: h.worker.publish(*old_claim, old_bundle)
    results = ordered_worker_race(h, *((activate, stale_publish) if activate_first else (stale_publish, activate)),
                                 gate_occurrence=1 if activate_first else 2)
    assert all(status == "ok" for status, _ in results)
    assert results[1 if activate_first else 0][1] == "stale"
    with h.sessions() as db:
        profile, new_version = db.get(VisualCompanion, ids.profile), db.get(Version, new_id)
        assert profile.current_version_id == profile.desired_version_id == new_id
        assert profile.enabled and profile.revision == expected_revision + 1
        assert new_version.state == "ready" and new_version.approved_by_user_id == ids.owner
        assert db.get(Version, ids.version).state == "purge_pending"
        assets = list(db.scalars(select(Asset)))
        assert len(assets) == 6
        assert {a.state for a in assets if a.version_id == ids.version} == {"purge_pending"}
        assert {a.state for a in assets if a.version_id == new_id} == {"available"}


@pytest.mark.parametrize("new_first", [True, False])
def test_expired_lease_independent_worker_completions(pg_worker_race, new_first):
    h = pg_worker_race
    ids = h.seed()
    old_claim, bundle, _ = prepare_registered(h, ids)
    second = independent_worker(h)
    h.clock.advance(31)
    assert second.claim() is None  # Expired lease does not retire the writer.
    h.clock.advance(90)
    new_claim = second.claim()
    assert new_claim[0] == old_claim[0] and new_claim[1] != old_claim[1]
    rows = second.reserve_assets(*new_claim, bundle)
    h.upload(new_claim, bundle, rows)
    old_publish = lambda: h.worker.publish(*old_claim, bundle)
    new_publish = lambda: second.publish(*new_claim, bundle)
    results = ordered_worker_race(h, *((new_publish, old_publish) if new_first else (old_publish, new_publish)),
                                 gate_occurrence=2)
    assert results == ((("ok", "ready"), ("ok", "stale")) if new_first else
                       (("ok", "stale"), ("ok", "ready")))
    with h.sessions() as db:
        version, profile, job = db.get(Version, ids.version), db.get(VisualCompanion, ids.profile), db.get(Job, ids.job)
        assert version.state == "ready" and version.approved_at is None
        assert not profile.enabled and profile.current_version_id is None
        assert job.state == "succeeded" and job.attempts == 2 and job.lease_token is None
        assets = list(db.scalars(select(Asset)))
        assert len(assets) == 6
        assert {a.state for a in assets if a.attempt_id == old_claim[1]} == {"purge_pending"}
        assert {a.logical_role for a in assets if a.state == "available"} == {"poster", "texture_atlas", "rig"}
        assert all(a.attempt_id == new_claim[1] for a in assets if a.state == "available")


def test_reservation_insert_rollback_seen_by_independent_worker(pg_worker_race):
    h = pg_worker_race
    ids = h.seed()
    claim, bundle, _ = prepare_registered(h, ids)
    second = independent_worker(h)
    h.clock.advance(121)
    new_claim = second.claim()
    assert new_claim[0] == claim[0] and new_claim[1] != claim[1]

    def fail_after_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("INSERT INTO visual_companion_assets"):
            raise RuntimeError("rollback after successful asset INSERT")

    event.listen(h.engine, "after_cursor_execute", fail_after_insert)
    try:
        with pytest.raises(RuntimeError, match="successful asset INSERT"):
            second.reserve_assets(*new_claim, bundle)
    finally:
        event.remove(h.engine, "after_cursor_execute", fail_after_insert)
    with h.sessions() as db:
        assets = list(db.scalars(select(Asset)))
        assert len(assets) == 3 and all(a.attempt_id == claim[1] for a in assets)
        assert {a.state for a in assets} == {"purge_pending"}
        assert db.get(Job, ids.job).lease_token == new_claim[1]
        assert db.scalar(select(Job).where(Job.kind == "purge")) is not None
    # No PUT was dispatched for the rolled-back reservation, so registration
    # can be retried without orphaning objects or reusing an external write.
    rows = second.reserve_assets(*new_claim, bundle)
    h.upload(new_claim, bundle, rows)
    assert second.publish(*new_claim, bundle) == "ready"


@pytest.mark.parametrize("purge_before_put", [False, True])
def test_blocked_put_returns_after_delete_before_or_after_purge(pg_worker_race, monkeypatch, purge_before_put):
    h = pg_worker_race
    ids = h.seed()
    entered, release = threading.Event(), threading.Event()
    real_put, keys = h.storage.put, []
    cleaner = independent_worker(h)

    def blocked_put(key, data, mime):
        keys.append(key)
        entered.set()
        assert release.wait(timeout=15)
        return real_put(key, data, mime)

    monkeypatch.setattr(h.storage, "put", blocked_put)
    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(h.worker.run_once)
        try:
            assert entered.wait(timeout=10)
            with h.sessions() as db:
                assert len(list(db.scalars(select(Asset)))) == 3
            h.tombstone(ids)  # Independent transaction commits while PUT blocks.
            if purge_before_put:
                # Explicitly adversarial fake: completion beyond the client
                # deadline. This tests returned-callback rearming, NOT a final
                # remote-erasure guarantee if that callback never returns.
                h.clock.advance(121)
                assert cleaner.run_once() == "purged"
            else:
                assert cleaner.run_once() == "retry_wait"
                with h.sessions() as db:
                    purge = db.scalar(select(Job).where(Job.kind == "purge"))
                    assert purge.last_error_code == "visual_writer_pending"
                    assert {a.state for a in db.scalars(select(Asset))} == {"purge_pending"}
        finally:
            release.set()
        assert writer.result(timeout=10) == "stale"
    assert len(keys) == 1 and h.storage.storage.exists(keys[0])
    with h.sessions() as db:
        assert db.get(VisualCompanion, ids.profile).deleted_at is not None
        assert {a.state for a in db.scalars(select(Asset))} == {"purge_pending"}
        purge = db.scalar(select(Job).where(Job.kind == "purge"))
        assert purge.state in {"queued", "retry_wait"}
    h.clock.advance(121)
    assert cleaner.run_once() == "purged"
    assert not h.storage.storage.exists(keys[0])
    with h.sessions() as db:
        assert {a.state for a in db.scalars(select(Asset))} == {"purged"}
        assert db.get(Version, ids.version).state == "purged"
        assert not db.get(VisualCompanion, ids.profile).enabled
