"""L19 real PostgreSQL races in disposable per-test schemas.

Both winning orders are forced at an actual parent-row lock. A third connection
observes pg_blocking_pids before releasing the winner; time passing is never
accepted as proof of contention. The worker suite supplies the complementary
global-claim, lease, late-write and provider-without-SQL-lock scenarios.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.legacy import Legacy
from app.models.user import User
from app.models.media_source import MediaSource, MediaArtifact
from app.models.visual_companion import (
    VisualCompanion as Profile, VisualCompanionVersion as Version,
    VisualCompanionAsset as Asset, VisualGenerationJob as Job,
)
from app.services.media_sources import MediaSourceService
from app.services.media_storage import LocalSourceStorage
from app.services.visual_companions import VisualCompanionService, utcnow
from app.services.visual_provider import FakePortraitRigProvider
from app.services.visual_storage import VisualStorage
from app.services.visual_worker import VisualWorker
from tests.visual_l19_helpers import seed, source, command, admission, approval, factual_snapshot
from tests.test_visual_companions_l19 import ready_fixture


@pytest.fixture
def pg(tmp_path, monkeypatch):
    url = os.environ.get("L19_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires explicit disposable L19_TEST_POSTGRES_URL")
    parsed = sa.engine.make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost"} and parsed.database == "l19_test_phase_b"
    admin = sa.create_engine(url)
    schema = "l19_races_" + uuid4().hex
    with admin.begin() as conn:
        assert conn.exec_driver_sql("SELECT current_database()").scalar_one() == parsed.database
        assert conn.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one() == "0021_visual_companions"
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = sa.create_engine(url, connect_args={"options": f"-csearch_path={schema} -clock_timeout=15000 -cstatement_timeout=20000"})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    storage = LocalSourceStorage(str(tmp_path / "sources"))
    derivatives = VisualStorage(LocalSourceStorage(str(tmp_path / "derivatives")))
    service = VisualCompanionService()
    # Synthetic, locally generated pixels only, matching the provider unit-test
    # fixture. Production deliberately has no unconfined Windows decoder.
    from app.services import visual_provider, visual_reference
    monkeypatch.setattr(visual_provider, "normalize_crop", visual_reference._decode_image)
    worker = VisualWorker(sessions, derivatives, provider=FakePortraitRigProvider(), source_storage=storage)
    with sessions() as db:
        seed(db)
        source_id = source(db, storage)
        before = factual_snapshot(db)
    h = SimpleNamespace(engine=engine, sessions=sessions, storage=storage, derivatives=derivatives,
        service=service, worker=worker, source=source_id, before=before)
    try:
        yield h
        with sessions() as db:
            assert factual_snapshot(db) == before
    finally:
        engine.dispose()
        # Only the schema generated above, inside the explicitly guarded DB.
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def transaction(h, operation):
    def run():
        with h.sessions() as db:
            result = operation(db)
            db.commit()
            return result
    return run


def ordered_race(h, first, second, *, gate_occurrence=1):
    """Hold the first transaction after its chosen Legacy FOR UPDATE executes."""
    locked, release, started = threading.Event(), threading.Event(), threading.Barrier(2)
    identities, pids, occurrences = {}, {}, [0]

    def observe(conn, cursor, statement, parameters, context, executemany):
        label = identities.get(threading.get_ident())
        if label is None:
            return
        pids[label] = conn.connection.driver_connection.get_backend_pid()
        if label == "first" and "FOR UPDATE" in statement.upper() and ("FROM legacies" in statement or ".legacies" in statement):
            occurrences[0] += 1
            if occurrences[0] == gate_occurrence:
                locked.set()
                assert release.wait(12), "Contending command did not reach the observed lock"

    def run(label, action):
        identities[threading.get_ident()] = label
        if label == "second":
            started.wait(timeout=10)
        try:
            return ("ok", action())
        except HTTPException as exc:
            return ("http", exc.status_code)

    sa.event.listen(h.engine, "after_cursor_execute", observe)
    # Record PID before execution too: a blocked SQL statement has not returned.
    def identify(conn, cursor, statement, parameters, context, executemany):
        label = identities.get(threading.get_ident())
        if label:
            pids[label] = conn.connection.driver_connection.get_backend_pid()
    sa.event.listen(h.engine, "before_cursor_execute", identify)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            winner = pool.submit(run, "first", first)
            assert locked.wait(10), "First command never acquired the parent lock"
            loser = pool.submit(run, "second", second)
            started.wait(timeout=10)
            try:
                deadline = time.monotonic() + 10
                observed = False
                with h.engine.connect() as monitor:
                    while time.monotonic() < deadline:
                        if "second" in pids:
                            blockers = monitor.execute(sa.text("SELECT pg_blocking_pids(:pid)"), {"pid": pids["second"]}).scalar_one()
                            if pids["first"] in blockers:
                                observed = True
                                break
                        assert not loser.done(), "Competing command completed without contending"
                        # Yield between catalog probes; correctness uses the lock
                        # observation above, never elapsed time or this wait.
                        release.wait(0.005)
                assert observed, "No PostgreSQL row-lock contention observed"
                assert pids["first"] != pids["second"]
            finally:
                release.set()
            return winner.result(timeout=20), loser.result(timeout=20)
    finally:
        release.set()
        sa.event.remove(h.engine, "after_cursor_execute", observe)
        sa.event.remove(h.engine, "before_cursor_execute", identify)


def candidate(h, *, source_id=None, ready=True):
    with h.sessions() as db:
        profile = db.scalar(sa.select(Profile).where(Profile.legacy_id == 1))
        revision = profile.revision if profile else 0
        version_id = admission(db, source_id or h.source, revision)
        if ready:
            ready_fixture(db, version_id, h.storage)
            db.execute(sa.update(Job).where(Job.version_id == version_id, Job.kind == "prepare").values(state="succeeded"))
            db.commit()
        profile = db.scalar(sa.select(Profile).where(Profile.legacy_id == 1))
        return version_id, profile.revision


def activation(h, version_id, revision):
    def activate(db):
        payload = approval(db.get(Version, version_id), revision)
        return h.service.activate(db, 1, 1, payload).current_version_id
    return transaction(h, activate)


def deletion(h, revision):
    return transaction(h, lambda db: h.service.delete(db, 1, 1, revision).id)


def source_deletion(h, source_id=None):
    return transaction(h, lambda db: MediaSourceService(storage=h.storage).delete(db, db.get(User, 1), 1, source_id or h.source).state)


@pytest.mark.parametrize("delete_first", [False, True])
def test_regenerate_vs_companion_delete(pg, delete_first):
    h = pg
    old_id, revision = candidate(h)
    payload = command(h.source, revision)
    regenerate = transaction(h, lambda db: h.service.admit(db, 1, 1, payload).id)
    delete = deletion(h, revision)
    results = ordered_race(h, *( (delete, regenerate) if delete_first else (regenerate, delete)))
    assert results[0][0] == "ok" and results[1] == ("http", 409)
    with h.sessions() as db:
        profile = db.scalar(sa.select(Profile))
        assert profile.revision == revision + 1
        if delete_first:
            assert profile.deleted_at and not profile.enabled
            assert profile.desired_version_id is None
            assert db.get(Version, old_id).state == "purge_pending"
        else:
            assert profile.deleted_at is None and profile.desired_version_id == results[0][1]
            assert db.get(Version, old_id).state == "purge_pending"


@pytest.mark.parametrize("delete_first", [False, True])
def test_source_delete_vs_activation(pg, delete_first):
    h = pg
    version_id, revision = candidate(h)
    activate, delete = activation(h, version_id, revision), source_deletion(h)
    results = ordered_race(h, *((delete, activate) if delete_first else (activate, delete)))
    assert results[0][0] == "ok"
    assert results[1] == ("http", 409) if delete_first else results[1][0] == "ok"
    with h.sessions() as db:
        profile, version = db.scalar(sa.select(Profile)), db.get(Version, version_id)
        assert not profile.enabled and profile.current_version_id is None and profile.desired_version_id is None
        assert version.state == "purge_pending"
        assert db.get(MediaSource, h.source).state == "deleting"
        assert db.scalar(sa.select(Job).where(Job.version_id == version_id, Job.kind == "purge"))


@pytest.mark.parametrize("winner", [0, 1])
def test_simultaneous_activation_one_revision_winner(pg, winner):
    h = pg
    version_id, revision = candidate(h)
    actions = [activation(h, version_id, revision), activation(h, version_id, revision)]
    assert ordered_race(h, actions[winner], actions[1 - winner]) == (("ok", version_id), ("http", 409))
    with h.sessions() as db:
        profile, version = db.scalar(sa.select(Profile)), db.get(Version, version_id)
        assert profile.current_version_id == version_id and profile.enabled and profile.revision == revision + 1
        assert version.approved_by_user_id == 1 and version.approved_at


@pytest.mark.parametrize("winner", [0, 1])
@pytest.mark.parametrize("different_payload", [False, True])
def test_duplicate_request_key_admission(pg, winner, different_payload):
    h = pg
    key = str(uuid4())
    payloads = [command(h.source, key=key), command(h.source, key=key,
        **({"crop": {"x": 0, "y": 0, "width": .75, "height": 1, "rotation": 0}} if different_payload else {}))]
    actions = [transaction(h, lambda db, p=p: h.service.admit(db, 1, 1, p).id) for p in payloads]
    results = ordered_race(h, actions[winner], actions[1 - winner])
    assert results[0][0] == "ok"
    assert results[1] == (("http", 409) if different_payload else results[0])
    with h.sessions() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Version)) == 1
        assert db.scalar(sa.select(sa.func.count()).select_from(Job)) == 1
        assert db.scalar(sa.select(Profile)).desired_version_id == results[0][1]


@pytest.mark.parametrize("replay_first", [False, True])
def test_old_request_replay_vs_newer_activation(pg, replay_first):
    h = pg
    old_id, revision = candidate(h)
    activation(h, old_id, revision)()
    with h.sessions() as db:
        old = db.get(Version, old_id)
        payload = command(h.source, key=old.request_key)
    new_id, revision = candidate(h)
    replay = transaction(h, lambda db: h.service.admit(db, 1, 1, payload).id)
    activate = activation(h, new_id, revision)
    results = ordered_race(h, *((replay, activate) if replay_first else (activate, replay)))
    assert all(result[0] == "ok" for result in results)
    with h.sessions() as db:
        profile = db.scalar(sa.select(Profile))
        assert profile.current_version_id == profile.desired_version_id == new_id
        assert profile.enabled and profile.revision == revision + 1
        assert db.get(Version, old_id).state == "purge_pending"


@pytest.mark.parametrize("delete_first", [False, True])
def test_delete_source_a_vs_activate_candidate_b(pg, delete_first):
    h = pg
    old_id, revision = candidate(h)
    activation(h, old_id, revision)()
    with h.sessions() as db:
        source_b = source(db, h.storage)
    new_id, revision = candidate(h, source_id=source_b)
    activate, delete = activation(h, new_id, revision), source_deletion(h)
    results = ordered_race(h, *((delete, activate) if delete_first else (activate, delete)))
    assert results[0][0] == "ok"
    with h.sessions() as db:
        profile = db.scalar(sa.select(Profile))
        assert db.get(Version, old_id).state == "purge_pending"
        assert db.get(Version, new_id).state == "ready"
        assert profile.desired_version_id == new_id
        if delete_first:
            assert results[1] == ("http", 409)
            assert not profile.enabled and profile.current_version_id is None
            fresh_revision = profile.revision
        else:
            assert results[1][0] == "ok" and profile.current_version_id == new_id and profile.enabled
            fresh_revision = None
    if fresh_revision is not None:
        assert activation(h, new_id, fresh_revision)() == new_id


@pytest.mark.parametrize("operation", ["confirmation", "admission", "activation", "delete"])
def test_transaction_rollback_no_partial_receipt_or_pointer(pg, operation):
    h = pg
    version_id, revision = candidate(h)
    if operation == "delete":
        activation(h, version_id, revision)()
        revision += 1
    def snapshot(db):
        return {table.name: sorted(repr(tuple(row)) for row in db.execute(sa.select(table)).all())
            for table in (Profile.__table__, Version.__table__, Asset.__table__, Job.__table__)}
    with h.sessions() as db:
        before = snapshot(db)
    with pytest.raises(RuntimeError, match="injected rollback"):
        with h.sessions.begin() as db:
            if operation in {"confirmation", "admission"}:
                h.service.admit(db, 1, 1, command(h.source, revision))
            elif operation == "activation":
                h.service.activate(db, 1, 1, approval(db.get(Version, version_id), revision))
            else:
                h.service.delete(db, 1, 1, revision)
            db.flush()
            raise RuntimeError("injected rollback")
    with h.sessions() as db:
        assert snapshot(db) == before


def reserved_bundle(h, *, source_id=None, write=True):
    version_id, revision = candidate(h, source_id=source_id, ready=False)
    claim = h.worker.claim()
    assert claim is not None
    with h.sessions() as db:
        version = db.get(Version, version_id)
        original = db.get(MediaArtifact, version.source_artifact_id)
        with h.storage.open(original.object_key) as stream:
            data = stream.read()
        bundle = h.worker.provider.prepare(data, version.crop_json, h.worker._identity(version))
    reservations = h.worker.reserve_assets(*claim, bundle)
    if write:
        upload(h, claim, bundle, reservations)
    return version_id, revision, claim, bundle


def upload(h, claim, bundle, reservations):
    for row in reservations:
        stored = h.derivatives.put(row.key, bundle.assets[row.role], row.mime_type)
        h.worker._record_put(*claim, row.id, stored)


@pytest.mark.parametrize("delete_first", [False, True])
def test_source_delete_vs_worker_publication(pg, delete_first):
    h = pg
    version_id, _, claim, bundle = reserved_bundle(h)
    publish = lambda: h.worker.publish(*claim, bundle)
    delete = source_deletion(h)
    results = ordered_race(h, *((delete, publish) if delete_first else (publish, delete)),
        gate_occurrence=1 if delete_first else 2)
    assert all(result[0] == "ok" for result in results)
    assert results[1 if delete_first else 0][1] == ("stale" if delete_first else "ready")
    with h.sessions() as db:
        profile = db.scalar(sa.select(Profile))
        assert profile.current_version_id is None and profile.desired_version_id is None and not profile.enabled
        assert db.get(Version, version_id).state == "purge_pending"
        assert all(a.state == "purge_pending" for a in db.scalars(sa.select(Asset)))
        assert db.get(Job, claim[0]).state == "cancelled"


@pytest.mark.parametrize("activate_first", [False, True])
def test_new_activation_vs_stale_old_worker_publication(pg, activate_first):
    h = pg
    old_id, _, claim, bundle = reserved_bundle(h)
    new_id, revision = candidate(h)
    publish = lambda: h.worker.publish(*claim, bundle)
    activate = activation(h, new_id, revision)
    results = ordered_race(h, *((activate, publish) if activate_first else (publish, activate)),
        gate_occurrence=1 if activate_first else 2)
    assert all(result[0] == "ok" for result in results)
    assert results[1 if activate_first else 0][1] == "stale"
    with h.sessions() as db:
        profile = db.scalar(sa.select(Profile))
        assert profile.current_version_id == profile.desired_version_id == new_id
        assert profile.enabled and profile.revision == revision + 1
        assert db.get(Version, old_id).state == "purge_pending"
        assert db.get(Version, new_id).approved_by_user_id == 1


@pytest.mark.parametrize("new_first", [False, True])
def test_expired_lease_two_worker_completions(pg, new_first):
    h = pg
    version_id, revision, old_claim, bundle = reserved_bundle(h)
    later = utcnow() + timedelta(seconds=130)
    h.worker.clock = lambda: later
    new_claim = h.worker.claim()
    assert new_claim is not None and new_claim[0] == old_claim[0] and new_claim[1] != old_claim[1]
    reservations = h.worker.reserve_assets(*new_claim, bundle)
    upload(h, new_claim, bundle, reservations)
    old_publish = lambda: h.worker.publish(*old_claim, bundle)
    new_publish = lambda: h.worker.publish(*new_claim, bundle)
    results = ordered_race(h, *((new_publish, old_publish) if new_first else (old_publish, new_publish)), gate_occurrence=2)
    assert results == ((("ok", "ready"), ("ok", "stale")) if new_first else (("ok", "stale"), ("ok", "ready")))
    with h.sessions() as db:
        profile, version = db.scalar(sa.select(Profile)), db.get(Version, version_id)
        assert version.state == "ready" and version.approved_at is None
        assert profile.revision == revision and profile.current_version_id is None and not profile.enabled
        assets = db.scalars(sa.select(Asset).where(Asset.version_id == version_id)).all()
        assert len(assets) == 6
        assert {a.logical_role for a in assets if a.state == "available"} == {"poster", "texture_atlas", "rig"}
        assert all(a.state == "purge_pending" for a in assets if a.attempt_id == old_claim[1])
        assert all(a.state == "available" for a in assets if a.attempt_id == new_claim[1])


def test_asset_registration_rollback_remains_discoverable(pg):
    h = pg
    version_id, _, claim, bundle = reserved_bundle(h)
    # A fresh attempt is required; the previously committed reservations remain
    # discoverable even if registering the next attempt rolls back.
    later = utcnow() + timedelta(seconds=130)
    h.worker.clock = lambda: later
    next_claim = h.worker.claim()
    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("INSERT INTO visual_companion_assets"):
            raise RuntimeError("injected reservation rollback")
    sa.event.listen(h.engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected reservation rollback"):
            h.worker.reserve_assets(*next_claim, bundle)
    finally:
        sa.event.remove(h.engine, "before_cursor_execute", fail)
    with h.sessions() as db:
        assets = db.scalars(sa.select(Asset).where(Asset.version_id == version_id)).all()
        assert len(assets) == 3 and all(a.attempt_id == claim[1] for a in assets)
        assert all(a.state == "purge_pending" for a in assets)
        assert db.scalar(sa.select(Job).where(Job.version_id == version_id, Job.kind == "purge"))


@pytest.mark.parametrize("write_first", [False, True])
def test_cleanup_vs_inflight_late_put(pg, write_first):
    h = pg
    version_id, revision, claim, bundle = reserved_bundle(h, write=False)
    rows = h.worker._attempt(*claim)
    dispatched, complete = threading.Barrier(2), threading.Event()
    def delayed_put():
        # A dispatched writer holds no SQL locks. Its registered reservations
        # must survive a concurrent delete/purge, regardless of PUT completion.
        dispatched.wait(timeout=10)
        assert complete.wait(10)
        upload(h, claim, bundle, rows)
        h.worker.cleanup_attempt(*claim)
    with ThreadPoolExecutor(max_workers=1) as pool:
        writer = pool.submit(delayed_put)
        dispatched.wait(timeout=10)
        try:
            deletion(h, revision)()
            if write_first:
                complete.set()
                writer.result(timeout=10)
            assert h.worker.run_once() == "retry_wait"
            with h.sessions() as db:
                assert db.get(Version, version_id).state == "purge_pending"
                assert {a.state for a in db.scalars(sa.select(Asset))} == {"purge_pending"}
            if not write_first:
                complete.set()
                writer.result(timeout=10)
        finally:
            complete.set()
    assert all(h.derivatives.storage.exists(row.key) for row in rows)
    assert h.worker.publish(*claim, bundle) == "stale"
    later = utcnow() + timedelta(seconds=130)
    h.worker.clock = lambda: later
    assert h.worker.run_once() == "purged"
    assert all(not h.derivatives.storage.exists(row.key) for row in rows)
    with h.sessions() as db:
        assert db.get(Version, version_id).state == "purged"
        assert db.scalar(sa.select(Profile)).deleted_at is not None
        assert {a.state for a in db.scalars(sa.select(Asset))} == {"purged"}


@pytest.mark.parametrize("delete_first", [False, True])
def test_delete_current_source_a_vs_prepare_b_then_explicit_activate(pg, delete_first):
    h = pg
    old_id, revision = candidate(h)
    activation(h, old_id, revision)()
    with h.sessions() as db:
        source_b = source(db, h.storage)
    new_id, _, claim, bundle = reserved_bundle(h, source_id=source_b)
    publish = lambda: h.worker.publish(*claim, bundle)
    delete = source_deletion(h)
    results = ordered_race(h, *((delete, publish) if delete_first else (publish, delete)),
        gate_occurrence=1 if delete_first else 2)
    assert all(r[0] == "ok" for r in results)
    assert results[1 if delete_first else 0][1] == "ready"
    with h.sessions() as db:
        profile = db.scalar(sa.select(Profile))
        assert not profile.enabled and profile.current_version_id is None
        assert profile.desired_version_id == new_id
        assert db.get(Version, new_id).approved_at is None
        assert db.get(Version, old_id).state == "purge_pending"
        revision = profile.revision
    assert activation(h, new_id, revision)() == new_id
