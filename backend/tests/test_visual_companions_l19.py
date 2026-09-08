import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database import Base, build_engine, get_db
from app.main import app
from app.api.dependencies import get_current_user
from app.models.user import User
from app.models.viewer import LegacyViewerAccess
from app.models.collaboration import LegacyCollaborator
from app.models.media_source import MediaSource
from app.models.visual_companion import VisualCompanion, VisualCompanionVersion, VisualCompanionAsset, VisualGenerationJob
from app.services.media_sources import MediaSourceService
from app.services.media_storage import LocalSourceStorage
from app.services.visual_companions import VisualCompanionService, utcnow, manifest, digest
from tests.visual_l19_helpers import seed, source, command, admission, approval, factual_snapshot


@pytest.fixture
def visual(tmp_path):
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    storage = LocalSourceStorage(str(tmp_path))
    with factory() as db:
        seed(db)
        source_id = source(db, storage)
    yield factory, storage, source_id
    engine.dispose()


def ready_fixture(db, version_id, storage):
    """Domain-command fixture only; worker tests prove actual bundle publication."""
    version = db.get(VisualCompanionVersion, version_id)
    descriptors = []
    for role in ("poster", "texture_atlas", "rig"):
        asset_id = str(uuid4())
        data = b"synthetic-domain-fixture-" + role.encode()
        key = f"l19-tests/{version_id}/{asset_id}"
        storage.put(key, data, content_type="application/json" if role == "rig" else "image/png")
        db.add(VisualCompanionAsset(id=asset_id, legacy_id=version.legacy_id, companion_id=version.companion_id,
            version_id=version_id, attempt_id=version_id, logical_role=role, state="available",
            storage_backend=storage.backend_name, object_key=key, byte_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(), mime_type="application/json" if role == "rig" else "image/png",
            writer_deadline=utcnow() - timedelta(seconds=1)))
        descriptors.append({"role": role, "sha256": hashlib.sha256(data).hexdigest(),
            "byte_size": len(data), "mime_type": "application/json" if role == "rig" else "image/png"})
    version.state = "ready"
    version.bundle_digest = digest({"recipe": version.recipe_version,
        "assets": sorted(descriptors, key=lambda a: a["role"])})
    db.commit()
    return version


def test_admission_confirmation_private_and_zero_effects(visual):
    factory, storage, source_id = visual
    service = VisualCompanionService()
    with factory() as db:
        before = factual_snapshot(db)
        payload = command(source_id)
        version = service.admit(db, 1, 1, payload)
        db.commit()
        profile = db.scalar(select(VisualCompanion))
        assert not profile.enabled and profile.current_version_id is None
        assert profile.desired_version_id == version.id
        assert version.confirmed_by_user_id == 1 and version.confirmed_at
        assert version.source_generation == version.source_artifact_generation == 1
        assert version.crop_json == payload.crop.model_dump()
        assert service.admit(db, 1, 1, payload).id == version.id
        db.commit()
        assert len(db.scalars(select(VisualGenerationJob)).all()) == 1
        assert factual_snapshot(db) == before


@pytest.mark.parametrize('age,attempts,expected', [(0, 1, 'ok'), (3599, 4, 'ok'),
    (3600, 1, 'error'), (0, 5, 'error')])
def test_purge_health_thresholds_and_content_free_output(visual, age, attempts, expected):
    from app.services.visual_health import health_summary
    factory, _, source_id = visual
    now = utcnow()
    with factory() as db:
        admission(db, source_id)
        profile = db.scalar(select(VisualCompanion))
        VisualCompanionService().delete(db, 1, 1, profile.revision)
        db.commit()
        job = db.scalar(select(VisualGenerationJob).where(VisualGenerationJob.kind == 'purge'))
        job.created_at = now - timedelta(seconds=age)
        job.updated_at = now
        job.attempts = attempts
        job.last_error_code = 'visual_remote_erasure_unproven'
        db.commit()
        before = factual_snapshot(db)
    result = health_summary(factory, now)
    assert result == {'event':'visual_worker_health', 'status':expected,
        'pending_purge_count':1, 'oldest_purge_age_seconds':age,
        'repeated_failure_count':int(attempts>=5), 'remote_reconcile_pending_count':1,
        'last_cleanup_error_code':'visual_remote_erasure_unproven',
        'last_cleanup_error_at':now.isoformat(),
        'thresholds':{'oldest_purge_seconds':3600, 'repeated_failure_attempts':5,
                      'legacy_cleanup_backlog_admission_stop':2}}
    with factory() as db:
        assert factual_snapshot(db) == before
        job = db.scalar(select(VisualGenerationJob).where(VisualGenerationJob.kind == 'purge'))
        job.last_error_code = 'private-diagnostics-not-allowlisted'
        db.commit()
    assert health_summary(factory, now)['last_cleanup_error_code'] is None


def test_purge_retry_emits_only_fixed_code_and_opaque_id(visual, caplog):
    from app.services.visual_storage import VisualStorage
    from app.services.visual_worker import VisualWorker
    factory, storage, source_id = visual
    with factory() as db:
        admission(db, source_id)
        profile = db.scalar(select(VisualCompanion))
        VisualCompanionService().delete(db, 1, 1, profile.revision)
        db.commit()
    worker = VisualWorker(factory, VisualStorage(storage), source_storage=storage)
    claim = worker.claim()
    assert claim
    with caplog.at_level('ERROR', logger='visual_purge'):
        assert worker._purge_retry(*claim, remote_unproven=True) == 'retry_wait'
    records = [r for r in caplog.records if r.name == 'visual_purge']
    assert len(records) == 1
    assert json.loads(records[0].message) == {'event':'visual_purge_pending',
        'job_id':claim[0], 'code':'visual_remote_erasure_unproven', 'attempts':1}


@pytest.mark.parametrize("change", [{"confirmed": False}, {"confirmation_copy_version": "wrong"},
    {"crop": {"x": .9, "y": 0, "width": .5, "height": 1}},
    {"crop": {"x": float("nan"), "y": 0, "width": 1, "height": 1}}])
def test_invalid_confirmation_crop_rejected(visual, change):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        command(visual[2], **change)


def test_activation_disable_reenable_delete_zero_effects(visual):
    factory, storage, source_id = visual
    service = VisualCompanionService()
    with factory() as db:
        before = factual_snapshot(db)
        version = ready_fixture(db, admission(db, source_id), storage)
        profile = db.scalar(select(VisualCompanion))
        service.activate(db, 1, 1, approval(version, profile.revision)); db.commit()
        assert profile.current_version_id == version.id and profile.enabled
        service.toggle(db, 1, 1, False, profile.revision); db.commit()
        assert not profile.enabled
        assert len(db.scalars(select(VisualCompanionAsset).where(VisualCompanionAsset.state == "available")).all()) == 3
        service.toggle(db, 1, 1, True, profile.revision); db.commit()
        service.delete(db, 1, 1, profile.revision); db.commit()
        assert profile.deleted_at and not profile.enabled and profile.current_version_id is None
        assert db.get(MediaSource, source_id).state == "ready"
        assert db.scalar(select(VisualGenerationJob).where(VisualGenerationJob.kind == "purge"))
        assert factual_snapshot(db) == before


def test_old_request_replay_never_reopens_or_rolls_back(visual):
    factory, storage, source_id = visual
    service = VisualCompanionService()
    with factory() as db:
        first_request = command(source_id)
        first = service.admit(db, 1, 1, first_request); db.commit()
        ready_fixture(db, first.id, storage)
        profile = db.scalar(select(VisualCompanion))
        service.activate(db, 1, 1, approval(first, profile.revision)); db.commit()
        second = service.admit(db, 1, 1, command(source_id, profile.revision)); db.commit()
        ready_fixture(db, second.id, storage)
        service.activate(db, 1, 1, approval(second, profile.revision)); db.commit()
        state = (profile.current_version_id, profile.desired_version_id, profile.enabled, profile.revision)
        assert service.admit(db, 1, 1, first_request).id == first.id
        db.commit()
        assert state == (profile.current_version_id, profile.desired_version_id, profile.enabled, profile.revision)
        service.delete(db, 1, 1, profile.revision); db.commit()
        service.admit(db, 1, 1, first_request); db.commit()
        assert profile.deleted_at and not profile.enabled and profile.desired_version_id is None


def test_quota_and_conflicting_key(visual):
    factory, _, source_id = visual
    with factory() as db:
        request = command(source_id)
        VisualCompanionService().admit(db, 1, 1, request); db.commit()
        with pytest.raises(HTTPException) as exc:
            VisualCompanionService().admit(db, 1, 1, command(source_id, key=str(request.request_key), crop={"x": 0, "y": 0, "width": .5, "height": 1}))
        assert exc.value.status_code == 409
        db.rollback()
        for _ in range(2):
            profile = db.scalar(select(VisualCompanion))
            VisualCompanionService().admit(db, 1, 1, command(source_id, profile.revision)); db.commit()
        with pytest.raises(HTTPException) as exc:
            VisualCompanionService().admit(db, 1, 1, command(source_id, profile.revision))
        assert exc.value.status_code == 429


@pytest.mark.parametrize("actor", [2, 3])
def test_nonowner_mutations_denied(visual, actor):
    factory, _, source_id = visual
    with factory() as db:
        if actor == 2:
            db.add(LegacyCollaborator(legacy_id=1, user_id=2, status="active")); db.commit()
        else:
            db.add(LegacyViewerAccess(legacy_id=1, user_id=3, status="active")); db.commit()
        before = factual_snapshot(db)
        with pytest.raises(HTTPException) as exc:
            VisualCompanionService().admit(db, actor, 1, command(source_id))
        assert exc.value.status_code == 404
        assert factual_snapshot(db) == before


def test_visitor_only_approved_current_and_no_source_metadata(visual):
    factory, storage, source_id = visual
    with factory() as db:
        db.add(LegacyViewerAccess(legacy_id=1, user_id=3, status="active")); db.commit()
        version = ready_fixture(db, admission(db, source_id), storage)
        profile = db.scalar(select(VisualCompanion))
        for actor in (1, 2, 3):
            with pytest.raises(HTTPException):
                manifest(db, actor, 1, viewer=True)
        VisualCompanionService().activate(db, 1, 1, approval(version, profile.revision)); db.commit()
        result = manifest(db, 3, 1, viewer=True)
        assert result["lease_seconds"] == 15 and len(result["assets"]) == 3
        text = repr(result)
        assert source_id not in text and "object_key" not in text and "crop" not in text and "provider" not in text
        with pytest.raises(HTTPException):
            manifest(db, 1, 1, viewer=True)  # owner is not a viewer grant
        access = db.scalar(select(LegacyViewerAccess)); access.status = "revoked"; db.commit()
        with pytest.raises(HTTPException):
            manifest(db, 3, 1, viewer=True)


@pytest.mark.parametrize("deleted_source", ["current", "candidate"])
def test_source_deletion_respects_other_source_candidate(visual, deleted_source):
    factory, storage, source_a = visual
    with factory() as db:
        source_b = source(db, storage)
        before = factual_snapshot(db)
        first = ready_fixture(db, admission(db, source_a), storage)
        profile = db.scalar(select(VisualCompanion))
        service = VisualCompanionService()
        service.activate(db, 1, 1, approval(first, profile.revision)); db.commit()
        second = admission(db, source_b, profile.revision)
        MediaSourceService(storage=storage).delete(db, db.get(User, 1), 1, source_a if deleted_source == "current" else source_b)
        db.expire_all()
        assert profile.current_version_id == (None if deleted_source == "current" else first.id)
        assert profile.desired_version_id == (second if deleted_source == "current" else None)
        assert profile.enabled == (deleted_source == "candidate")
        assert factual_snapshot(db) == before


def test_rollback_admission_and_activation_leaves_no_partial_state(visual):
    factory, storage, source_id = visual
    with factory() as db:
        VisualCompanionService().admit(db, 1, 1, command(source_id)); db.flush(); db.rollback()
        assert not db.scalar(select(VisualCompanion))
        assert not db.scalar(select(VisualCompanionVersion))
        version = ready_fixture(db, admission(db, source_id), storage)
        profile = db.scalar(select(VisualCompanion))
        VisualCompanionService().activate(db, 1, 1, approval(version, profile.revision)); db.flush(); db.rollback()
        assert not profile.enabled and profile.current_version_id is None and version.approved_at is None


def test_get_is_read_only_and_private_errors(visual, monkeypatch):
    factory, _, _ = visual
    monkeypatch.setenv("VISUAL_PRESENCE_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()
    def db_override():
        with factory() as db:
            yield db
    def user_override():
        with factory() as db:
            return db.get(User, 1)
    old = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    try:
        with TestClient(app) as client:
            result = client.get("/api/v1/legacies/1/visual-companion")
            assert result.status_code == 200 and result.json()["revision"] == 0
            denied = client.get("/api/v1/legacies/1/visual-companion/active-manifest")
            assert denied.status_code == 404
            assert denied.headers["cache-control"] == "private, no-store"
            assert denied.headers["x-content-type-options"] == "nosniff"
        with factory() as db:
            assert not db.scalar(select(VisualCompanion)) and not db.scalar(select(VisualGenerationJob))
    finally:
        app.dependency_overrides.clear(); app.dependency_overrides.update(old)
        get_settings.cache_clear()


@pytest.mark.parametrize("mutation", ["disable", "delete"])
def test_read_does_not_discard_pending_revocation_in_same_transaction(visual, mutation):
    factory, storage, source_id = visual
    with factory() as db:
        db.add(LegacyViewerAccess(legacy_id=1, user_id=3, status="active")); db.commit()
        version = ready_fixture(db, admission(db, source_id), storage)
        profile = db.scalar(select(VisualCompanion))
        service = VisualCompanionService()
        service.activate(db, 1, 1, approval(version, profile.revision)); db.commit()
        if mutation == "disable":
            service.toggle(db, 1, 1, False, profile.revision)
        else:
            service.delete(db, 1, 1, profile.revision)
        with pytest.raises(HTTPException):
            manifest(db, 3, 1, viewer=True)
        db.commit()
        assert not profile.enabled
        if mutation == "delete":
            assert profile.deleted_at


def test_changed_asset_metadata_cannot_activate_old_preview_digest(visual):
    factory, storage, source_id = visual
    with factory() as db:
        version = ready_fixture(db, admission(db, source_id), storage)
        profile = db.scalar(select(VisualCompanion))
        preview = approval(version, profile.revision)
        db.scalar(select(VisualCompanionAsset)).sha256 = "f" * 64
        db.commit()
        with pytest.raises(HTTPException) as exc:
            VisualCompanionService().activate(db, 1, 1, preview)
        assert exc.value.detail["code"] == "visual_bundle_changed"
        assert not profile.enabled and version.approved_at is None


def test_owner_can_erase_companion_after_legacy_archival(visual):
    from app.models.legacy import Legacy
    factory, storage, source_id = visual
    with factory() as db:
        version = ready_fixture(db, admission(db, source_id), storage)
        profile = db.scalar(select(VisualCompanion))
        db.get(Legacy, 1).setup_status = "archived"; db.commit()
        VisualCompanionService().delete(db, 1, 1, profile.revision); db.commit()
        assert profile.deleted_at and not profile.enabled
        assert db.get(MediaSource, source_id).state == "ready"


@pytest.mark.parametrize("field,value", [("confirmation_copy_version", "invalid"), ("crop_digest", "f" * 64), ("recipe_digest", "f" * 64)])
def test_reenable_revalidates_immutable_confirmation(visual, field, value):
    factory, storage, source_id = visual
    with factory() as db:
        version = ready_fixture(db, admission(db, source_id), storage)
        profile = db.scalar(select(VisualCompanion))
        service = VisualCompanionService()
        service.activate(db, 1, 1, approval(version, profile.revision)); db.commit()
        service.toggle(db, 1, 1, False, profile.revision); db.commit()
        setattr(version, field, value); db.commit()
        with pytest.raises(HTTPException):
            service.toggle(db, 1, 1, True, profile.revision)
        assert not profile.enabled
