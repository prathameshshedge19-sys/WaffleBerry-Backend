"""Independent-transaction L21 races.

Set L21_TEST_POSTGRES_URL to a disposable loopback database named
``l21_test_phase_b``. These tests never accept a production host/database.
"""

import asyncio
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.models.legacy import Legacy
from app.models.user import User
from app.models.voice_profile import VoiceAsset, VoiceConsentReceipt, VoiceJob, VoiceProfile, VoiceProfileVersion
from app.services.legacy_deletion import request_deletion
from app.services.media_sources import MediaSourceService
from app.services.voice_profiles import (
    StaleVoiceClaim, VoiceEnrollmentService, VoiceJobService, VoiceProfileService,
    canonical_digest, utcnow,
)
from app.services.voice_providers import FakeClonedSpeechProvider, FakeReferencePreparationProvider
from tests.voice_l21_helpers import activate, intent, prepare, reserve

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pg_voice():
    url = os.environ.get("L21_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires explicit disposable L21_TEST_POSTGRES_URL")
    parsed = sa.engine.make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost"}
    assert parsed.database == "l21_test_phase_b"
    admin = sa.create_engine(url)
    schema = "l21_voice_" + uuid4().hex
    with admin.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = sa.create_engine(url, pool_size=8, max_overflow=4,
        connect_args={"options": f'-csearch_path="{schema}"'})

    with engine.begin() as conn:
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            for old in reversed(list(ScriptDirectory(str(ROOT / "alembic")).walk_revisions(base="base", head="0027_account_deletion"))):
                old.module.upgrade()
        conn.execute(sa.insert(User), [
            {"id": i, "full_name": f"L21 PG user {i}", "email": f"l21-pg-{i}@example.invalid",
             "password_hash": "unused", "voice_preference": "marin"} for i in (1, 2, 3)])
        conn.execute(sa.insert(Legacy), [
            {"id": i, "owner_user_id": 1, "subject_name": f"PG Legacy {i}", "setup_status": "active",
             "collaborator_code_enabled": False, "viewer_code_enabled": False} for i in (1, 2, 3)])
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def parallel(*functions):
    barrier = threading.Barrier(len(functions))
    def run(fn):
        barrier.wait(timeout=10)
        return fn()
    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        futures = [pool.submit(run, fn) for fn in functions]
        return [future.result(timeout=20) for future in futures]


def outcome(factory, command):
    def call():
        with factory() as db:
            try:
                value = command(db)
                db.commit()
                return "ok", getattr(value, "id", None)
            except HTTPException as exc:
                db.rollback()
                return f"http-{exc.status_code}", None
            except StaleVoiceClaim:
                db.rollback()
                return "stale", None
    return call


def preparing_candidate(factory, legacy_id):
    source = f"pg-reference-{legacy_id}".encode()
    with factory() as db:
        version = reserve(db, legacy_id=legacy_id)
        result = asyncio.run(FakeReferencePreparationProvider().prepare(
            source=source, language="mr", operation_generation=version.operation_generation))
        asset = VoiceAsset(id=str(uuid4()), legacy_id=legacy_id,
            voice_profile_id=version.voice_profile_id, version_id=version.id,
            kind="reference", state="available", storage_backend="local",
            object_key=f"legarya/legacies/{legacy_id}/voice/{version.id}/reference.wav",
            sha256=hashlib.sha256(source).hexdigest(), byte_count=len(source), mime_type="audio/wav",
            sample_rate=24000, channels=1, duration_ms=1000, created_at=utcnow())
        version.status = "queued"
        db.add(asset)
        db.flush()
        job = VoiceJobService().enqueue(db, legacy_id=legacy_id,
            profile_id=version.voice_profile_id, version_id=version.id, kind="prepare",
            request_key=f"prepare-race-{legacy_id}", request_digest="d" * 64)
        db.commit()
        claimed = VoiceJobService().claim(db, "prepare")
        assert claimed.id == job.id
        token = claimed.lease_token
        db.commit()
        return version.id, version.voice_profile_id, asset.id, job.id, token, result


def test_duplicate_and_simultaneous_enrollment_intentions(pg_voice):
    factory = pg_voice
    key = str(uuid4())
    same = lambda db: VoiceEnrollmentService().reserve_intent(db, 1, 1, intent(key=key))
    results = parallel(outcome(factory, same), outcome(factory, same))
    assert [item[0] for item in results] == ["ok", "ok"]
    assert results[0][1] == results[1][1]
    with factory() as db:
        with pytest.raises(HTTPException) as changed:
            VoiceEnrollmentService().reserve_intent(db, 1, 1,
                intent(revision=1, key=key, authority_basis="authorized_representative"))
        assert changed.value.status_code == 409

    first = lambda db: VoiceEnrollmentService().reserve_intent(db, 1, 2, intent(key=str(uuid4())))
    second = lambda db: VoiceEnrollmentService().reserve_intent(db, 1, 2, intent(key=str(uuid4())))
    results = parallel(outcome(factory, first), outcome(factory, second))
    assert sorted(item[0] for item in results) == ["http-409", "ok"]


def test_prepare_vs_revoke_activation_vs_delete_and_simultaneous_activation(pg_voice):
    factory = pg_voice
    with factory() as db:
        version = reserve(db, legacy_id=1)
        version = prepare(db, version)
        version_id, profile_id = version.id, version.voice_profile_id
        revision = db.get(VoiceProfile, profile_id).revision
        binding = version.binding_digest
    from app.schemas.voice_profile import VoiceActivation
    activate_cmd = lambda db: VoiceProfileService().activate(db, 1, 1,
        VoiceActivation(version_id=version_id, expected_revision=revision, binding_digest=binding, approved=True))
    delete_cmd = lambda db: VoiceProfileService().delete(db, 1, 1, revision)
    results = parallel(outcome(factory, activate_cmd), outcome(factory, delete_cmd))
    assert sorted(item[0] for item in results) == ["http-409", "ok"]
    with factory() as db:
        profile = db.get(VoiceProfile, profile_id)
        assert profile.status in {"active", "deleting"}
        assert not (profile.status == "deleting" and profile.current_version_id)

    # A new Legacy isolates the simultaneous-activation CAS race.
    with factory() as db:
        candidate = reserve(db, legacy_id=3)
        candidate = prepare(db, candidate)
        candidate_id, candidate_profile = candidate.id, candidate.voice_profile_id
        revision = db.get(VoiceProfile, candidate_profile).revision
        binding = candidate.binding_digest
    activate_same = lambda db: VoiceProfileService().activate(db, 1, 3,
        VoiceActivation(version_id=candidate_id, expected_revision=revision, binding_digest=binding, approved=True))
    results = parallel(outcome(factory, activate_same), outcome(factory, activate_same))
    assert sorted(item[0] for item in results) == ["http-409", "ok"]


def test_lease_expiry_stale_completion_replacement_and_purge_fences(pg_voice):
    factory = pg_voice
    with factory() as db:
        version = reserve(db, legacy_id=1)
        version.status = "queued"
        job = VoiceJobService().enqueue(db, legacy_id=1, profile_id=version.voice_profile_id,
            version_id=version.id, kind="prepare", request_key="pg-lease",
            request_digest="b" * 64)
        db.commit()
        version_id, job_id = version.id, job.id
    now = utcnow()
    with factory() as db:
        first = VoiceJobService().claim(db, "prepare", now=now, lease_seconds=1)
        old_token = first.lease_token
        db.commit()
    with factory() as db:
        second = VoiceJobService().claim(db, "prepare", now=now + timedelta(seconds=2))
        assert second.id == job_id and second.lease_token != old_token and second.attempts == 2
        db.commit()
    output = asyncio.run(FakeReferencePreparationProvider().prepare(
        source=b"pg-fixture", language="mr", operation_generation=1))
    with factory() as db:
        with pytest.raises(StaleVoiceClaim):
            VoiceJobService().publish_prepared(db, job_id, old_token, output,
                reference_asset_id="missing", model_manifest={}, asr_manifest={}, inference_config={})

    # Replacing the desired candidate increments its operation generation and
    # cancels the old lease; neither prepare nor a late writer may publish.
    with factory() as db:
        profile = db.scalar(sa.select(VoiceProfile).where(VoiceProfile.legacy_id == 1))
        VoiceEnrollmentService().reserve_intent(db, 1, 1, intent(revision=profile.revision))
        db.commit()
    with factory() as db:
        old = db.get(VoiceProfileVersion, version_id)
        job = db.get(VoiceJob, job_id)
        assert old.status == "purge_pending" and job.state == "cancelled"
        with pytest.raises(StaleVoiceClaim):
            VoiceJobService().publish_prepared(db, job_id, second.lease_token, output,
                reference_asset_id="missing", model_manifest={}, asr_manifest={}, inference_config={})


def test_prepare_completion_vs_revoke_and_legacy_delete_vs_late_completion(pg_voice):
    factory = pg_voice
    version_id, profile_id, asset_id, job_id, token, prepared = preparing_candidate(factory, 1)
    publish = lambda db: VoiceJobService().publish_prepared(db, job_id, token, prepared,
        reference_asset_id=asset_id, model_manifest={"revision": "test"},
        asr_manifest={"revision": "test"}, inference_config={"sample_rate": 24000})
    revoke = lambda db: VoiceProfileService().revoke(db, 1, 1, 1)
    results = parallel(outcome(factory, publish), outcome(factory, revoke))
    assert results[1][0] == "ok" and results[0][0] in {"ok", "stale"}
    with factory() as db:
        profile = db.get(VoiceProfile, profile_id)
        assert profile.status == "revoked" and profile.current_version_id is None
        assert db.get(VoiceProfileVersion, version_id).status == "purge_pending"

    version_id, profile_id, asset_id, job_id, token, prepared = preparing_candidate(factory, 2)
    publish = lambda db: VoiceJobService().publish_prepared(db, job_id, token, prepared,
        reference_asset_id=asset_id, model_manifest={"revision": "test"},
        asr_manifest={"revision": "test"}, inference_config={"sample_rate": 24000})
    def delete_legacy(db):
        return request_deletion(db, db.get(User, 1), 2, "DELETE LEGACY 2",
            source_service=MediaSourceService())
    results = parallel(outcome(factory, publish), outcome(factory, delete_legacy))
    assert results[1][0] == "ok" and results[0][0] in {"ok", "stale"}
    with factory() as db:
        assert db.get(Legacy, 2).deletion_requested_at is not None
        profile = db.get(VoiceProfile, profile_id)
        assert profile.status == "deleting" and profile.current_version_id is None
        assert db.get(VoiceProfileVersion, version_id).status == "purge_pending"


def test_profile_delete_vs_synthesis_publication(pg_voice):
    factory = pg_voice
    with factory() as db:
        version = reserve(db, legacy_id=1)
        version = prepare(db, version)
        profile = activate(db, version)
        authoritative_text = "already authoritative"
        authoritative_digest = hashlib.sha256(authoritative_text.encode("utf-8")).hexdigest()
        synth = VoiceJobService().enqueue(db, legacy_id=1, profile_id=profile.id,
            version_id=version.id, kind="synthesize", request_key="synth-race",
            request_digest=canonical_digest({"text": authoritative_text}), priority=50,
            purpose="preview", authoritative_text=authoritative_text,
            authoritative_text_digest=authoritative_digest,
            model_manifest_digest="a" * 64, inference_config_digest="b" * 64,
            requested_by_user_id=1)
        db.commit()
        profile_id, revision, synth_id = profile.id, profile.revision, synth.id
        speech = asyncio.run(FakeClonedSpeechProvider().synthesize(
            authoritative_text=authoritative_text, reference_audio=b"synthetic",
            reference_text=version.reference_transcript, language="mr",
            operation_generation=version.operation_generation))
    with factory() as db:
        claimed = VoiceJobService().claim(db, "synthesize")
        assert claimed.id == synth_id
        token = claimed.lease_token
        db.commit()
    publish_cmd = lambda db: VoiceJobService().publish_synthesis(
        db, synth_id, token, speech, authoritative_text)
    delete_cmd = lambda db: VoiceProfileService().delete(db, 1, 1, revision)
    results = parallel(outcome(factory, publish_cmd), outcome(factory, delete_cmd))
    assert results[1][0] == "ok" and results[0][0] in {"ok", "stale"}
    with factory() as db:
        profile = db.get(VoiceProfile, profile_id)
        synth = db.get(VoiceJob, synth_id)
        assert profile.current_version_id is None and profile.status == "deleting"
        assert synth.state in {"succeeded", "cancelled"}


def test_postgresql_populated_migration_roundtrip_and_composite_constraints(pg_voice):
    factory = pg_voice
    with factory() as db:
        first = reserve(db, legacy_id=1)
        second = reserve(db, legacy_id=2)
        first_profile, second_profile = first.voice_profile_id, second.voice_profile_id
    engine = factory.kw["bind"]
    with engine.begin() as conn:
        metadata = sa.MetaData()
        metadata.reflect(conn, only=["voice_profiles", "voice_profile_versions",
            "voice_consent_receipts", "voice_assets", "voice_jobs"])
        profiles = metadata.tables["voice_profiles"]
        versions = metadata.tables["voice_profile_versions"]
        consents = metadata.tables["voice_consent_receipts"]
        assets = metadata.tables["voice_assets"]
        jobs = metadata.tables["voice_jobs"]

        def reject(statement):
            with pytest.raises(sa.exc.DBAPIError):
                with conn.begin_nested():
                    conn.execute(statement)

        reject(profiles.update().where(profiles.c.id == first_profile).values(desired_version_id=second.id))
        reject(profiles.update().where(profiles.c.id == first_profile).values(
            status="active", current_version_id=first.id))
        reject(assets.insert().values(id="pg-cross-asset", legacy_id=1,
            voice_profile_id=first_profile, version_id=second.id, kind="original",
            storage_backend="local", object_key="legarya/pg-cross-asset"))
        reject(jobs.insert().values(id="pg-cross-job", legacy_id=1,
            voice_profile_id=first_profile, version_id=second.id, kind="prepare",
            operation_generation=1, request_key="cross", request_digest="a" * 64))
        first_consent = conn.scalar(sa.select(versions.c.consent_receipt_id).where(versions.c.id == first.id))
        reject(consents.update().where(consents.c.id == first_consent).values(copy_version="changed"))
        before_users = conn.exec_driver_sql("SELECT count(*) FROM users").scalar_one()
        before_legacies = conn.exec_driver_sql("SELECT count(*) FROM legacies").scalar_one()
        profile_migration = ScriptDirectory(str(ROOT / "alembic")).get_revision("0024_voice_profiles").module
        synthesis_migration = ScriptDirectory(str(ROOT / "alembic")).get_revision("0025_voice_synthesis_jobs").module
        live_synthesis_migration = ScriptDirectory(str(ROOT / "alembic")).get_revision("0026_voice_live_synthesis").module
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            live_synthesis_migration.downgrade()
            synthesis_migration.downgrade()
            profile_migration.downgrade()
        assert not any(name.startswith("voice_") for name in sa.inspect(conn).get_table_names())
        assert conn.exec_driver_sql("SELECT count(*) FROM users").scalar_one() == before_users
        assert conn.exec_driver_sql("SELECT count(*) FROM legacies").scalar_one() == before_legacies
        with Operations.context(context):
            profile_migration.upgrade()
            synthesis_migration.upgrade()
            live_synthesis_migration.upgrade()
        names = set(sa.inspect(conn).get_table_names())
        assert {"voice_profiles", "voice_profile_versions", "voice_consent_receipts",
            "voice_assets", "voice_jobs"} <= names
        expected = {table.name: [column.name for column in table.columns]
            for table in (VoiceProfile.__table__, VoiceProfileVersion.__table__,
                VoiceConsentReceipt.__table__, VoiceAsset.__table__, VoiceJob.__table__)}
        for name, columns in expected.items():
            assert [column["name"] for column in sa.inspect(conn).get_columns(name)] == columns
