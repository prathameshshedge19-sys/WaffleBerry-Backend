"""Private visual delivery on synthetic SQLite/local/fake-S3 fixtures only."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import visual_companion as routes
from app.config import get_settings
from app.database import Base, get_db
from app.models.collaboration import LegacyCollaborator
from app.models.legacy import Legacy
from app.models.media_source import MediaSource
from app.models.user import User
from app.models.viewer import LegacyViewerAccess
from app.models.visual_companion import VisualCompanion, VisualCompanionAsset, VisualCompanionVersion
from app.services.media_storage import StorageError
from app.services.security import create_access_token
from app.services.visual_companions import VisualCompanionService
from app.services.visual_storage import MAX_ASSET_BYTES, VisualStorage
from tests.test_visual_companions_l19 import ready_fixture, visual  # noqa: F401
from tests.test_visual_storage_l19 import FakeS3
from tests.visual_l19_helpers import admission, approval, command, factual_snapshot, source

BASE = "/api/v1/legacies/1/visual-companion"


def test_pending_owner_can_choose_and_upload_photo_but_cannot_prepare_or_serve_portrait(api):
    with api.factory.begin() as db:
        db.get(Legacy, 1).setup_status = "collecting_identity"
        db.get(Legacy, 1).subject_name = None
    response = api.request("GET", BASE + "/capabilities")
    private(response, 200)
    assert response.json()["enabled"] is True
    assert response.json()["can_manage"] is True
    assert response.json()["can_prepare"] is False
    private(api.request("GET", BASE), 200)
    private(api.request("GET", BASE + "/active-manifest"), 404)


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "location" not in response.headers
    if status >= 400:
        assert response.headers["content-type"] == "application/json"
        assert "detail" in response.json()
        for leaked in ("Traceback", "stacktrace", "site-packages", "File \"", "SSECustomerKey", "password_hash"):
            assert leaked not in response.text


@pytest.fixture
def api(visual, monkeypatch):
    factory, local_storage, source_id = visual
    monkeypatch.setenv("VISUAL_PRESENCE_ENABLED", "true")
    monkeypatch.setenv("VISUAL_PREPARATION_ENABLED", "true")
    get_settings.cache_clear()
    with factory() as db:
        db.add(User(id=4, full_name="Synthetic outsider", email="l19-4@example.invalid", password_hash="unused"))
        db.add(LegacyCollaborator(legacy_id=1, user_id=2, status="active"))
        db.add(LegacyViewerAccess(legacy_id=1, user_id=3, status="active"))
        db.commit()
        version = ready_fixture(db, admission(db, source_id), local_storage)
        profile = db.scalar(select(VisualCompanion))
        VisualCompanionService().activate(db, 1, 1, approval(version, profile.revision))
        db.commit()
        assets = {a.logical_role: a for a in db.scalars(select(VisualCompanionAsset))}
        state = SimpleNamespace(factory=factory, local=local_storage, source_storage=local_storage,
            source_id=source_id, version_id=version.id, profile_id=profile.id,
            assets=assets, calls=[], sessions=[], after_read=None, read_error=None)
    class RecordingStorage(VisualStorage):
        def read(self, key, version=None):
            assert all(not db.in_transaction() for db in state.sessions)
            state.calls.append((key, version))
            if state.read_error:
                raise state.read_error
            data = super().read(key, version)
            if state.after_read:
                state.after_read()
            return data
    monkeypatch.setattr(routes, "VisualStorage", RecordingStorage)
    monkeypatch.setattr(routes, "get_source_storage", lambda: state.source_storage)
    application = FastAPI()
    application.include_router(routes.router, prefix="/api/v1")
    def database():
        with factory() as db:
            state.sessions.append(db)
            try:
                yield db
            finally:
                state.sessions.remove(db)
    application.dependency_overrides[get_db] = database
    application.dependency_overrides[routes.preparation_service] = VisualCompanionService
    with TestClient(application) as client:
        state.client = client
        def request(method, path, actor=1, **kwargs):
            headers = {"Authorization": "Bearer " + create_access_token(actor)} if actor is not None else {}
            headers.update(kwargs.pop("headers", {}))
            return client.request(method, path, headers=headers, **kwargs)
        state.request = request
        yield state
    get_settings.cache_clear()


def preview(api, asset=None, version_id=None):
    return f"{BASE}/versions/{version_id or api.version_id}/assets/{(asset or api.assets['poster']).id}/content"


def active(api, asset=None):
    return f"{BASE}/active/assets/{(asset or api.assets['poster']).id}/content"


@pytest.mark.parametrize("actor,preview_status,active_status", [
    (1, 200, 404), (2, 404, 404), (3, 404, 200), (4, 404, 404), (None, 401, 401)])
def test_content_role_boundaries(api, actor, preview_status, active_status):
    with api.factory() as db:
        before = factual_snapshot(db)
    for path, status in [(preview(api), preview_status), (active(api), active_status)]:
        response = api.request("GET", path, actor)
        private(response, status)
        if status == 200:
            assert response.content == b"synthetic-domain-fixture-poster"
            assert response.headers["content-type"] == "image/png"
            assert response.headers["content-disposition"] == "inline; filename=visual-asset"
        else:
            assert b"synthetic-domain-fixture" not in response.content
    assert len(api.calls) == int(preview_status == 200) + int(active_status == 200)
    with api.factory() as db:
        assert factual_snapshot(db) == before


@pytest.mark.parametrize("role", ["poster", "texture_atlas", "rig"])
def test_all_asset_roles_delivered_with_exact_mime(api, role):
    response = api.request("GET", active(api, api.assets[role]), 3)
    private(response, 200)
    assert response.content == b"synthetic-domain-fixture-" + role.encode()
    assert response.headers["content-type"] == ("application/json" if role == "rig" else "image/png")


@pytest.mark.parametrize("actor", [1, 2])
def test_owner_or_collaborator_with_explicit_viewer_grant(api, actor):
    with api.factory.begin() as db:
        db.add(LegacyViewerAccess(legacy_id=1, user_id=actor, status="active"))
    private(api.request("GET", active(api), actor), 200)
    private(api.request("GET", preview(api), actor), 200 if actor == 1 else 404)


@pytest.mark.parametrize("actor,status", [(1, 404), (2, 404), (3, 200), (4, 404), (None, 401)])
def test_active_manifest_roles_and_no_source_internals(api, actor, status):
    response = api.request("GET", BASE + "/active-manifest", actor)
    private(response, status)
    if status == 200:
        assert response.json()["lease_seconds"] == 15
        assert len(response.json()["assets"]) == 3
        for forbidden in (api.source_id, "object_key", "storage_backend", "crop", "provider", "owner_id", "synthetic-l19.png"):
            assert forbidden not in response.text
    assert not api.calls


@pytest.mark.parametrize("actor,status", [(1, 200), (2, 404), (3, 404), (4, 404), (None, 401)])
def test_owner_metadata_routes_are_private(api, actor, status):
    for path in [BASE, BASE + f"/versions/{api.version_id}", BASE + f"/versions/{api.version_id}/manifest"]:
        private(api.request("GET", path, actor), status)
    assert not api.calls


@pytest.mark.parametrize("actor", [2, 3, 4])
@pytest.mark.parametrize("operation", ["create", "activate", "toggle", "delete"])
def test_nonowner_mutations_denied_without_writes(api, actor, operation):
    with api.factory() as db:
        before = {name: list(db.execute(select(table))) for name, table in Base.metadata.tables.items()}
        profile = db.get(VisualCompanion, api.profile_id)
        version = db.get(VisualCompanionVersion, api.version_id)
        revision = profile.revision
        activation = approval(version, revision).model_dump(mode="json")
    requests = {
        "create": ("POST", BASE + "/versions", {"json": command(api.source_id, revision).model_dump(mode="json")}),
        "activate": ("POST", BASE + "/activate", {"json": activation}),
        "toggle": ("PATCH", BASE, {"json": {"enabled": False, "expected_revision": revision}}),
        "delete": ("DELETE", BASE, {"params": {"expected_revision": revision}}),
    }
    method, path, kwargs = requests[operation]
    private(api.request(method, path, actor, **kwargs), 404)
    with api.factory() as db:
        assert {name: list(db.execute(select(table))) for name, table in Base.metadata.tables.items()} == before


def test_foreign_asset_version_and_original_cannot_be_read(api):
    with api.factory() as db:
        foreign_source = source(db, api.local, legacy_id=2)
        foreign_version = ready_fixture(db, admission(db, foreign_source, legacy_id=2), api.local)
        foreign_asset = db.scalar(select(VisualCompanionAsset).where(VisualCompanionAsset.version_id == foreign_version.id))
        paths = [preview(api, foreign_asset), preview(api, foreign_asset, foreign_version.id),
                 active(api, foreign_asset), active(api).replace("legacies/1", "legacies/2"),
                 active(api).replace(api.assets["poster"].id, api.source_id)]
    for path in paths:
        private(api.request("GET", path, 3 if "/active/" in path else 1), 404)
    assert not api.calls


def test_unapproved_candidate_is_owner_only(api):
    with api.factory() as db:
        profile = db.get(VisualCompanion, api.profile_id)
        candidate = ready_fixture(db, admission(db, api.source_id, profile.revision), api.local)
        asset = db.scalar(select(VisualCompanionAsset).where(VisualCompanionAsset.version_id == candidate.id))
        path = preview(api, asset, candidate.id)
    private(api.request("GET", path, 1), 200)
    private(api.request("GET", path, 3), 404)
    private(api.request("GET", active(api, asset), 3), 404)


@pytest.mark.parametrize('wrong_bucket', [False, True])
def test_s3_delivery_pins_registered_object_version(api, wrong_bucket):
    asset = api.assets["poster"]
    with api.factory.begin() as db:
        row = db.get(VisualCompanionAsset, asset.id)
        row.storage_backend, row.object_version = "s3", "approved-object-version"
        row.encryption_key_id = "test-key-id"
        row.storage_bucket = 'synthetic-bucket'
    client = FakeS3()
    client.objects[asset.object_key, "approved-object-version"] = b"synthetic-domain-fixture-poster"
    client.objects[asset.object_key, "later-unapproved-version"] = b"not the approved bytes"
    api.source_storage = SimpleNamespace(backend_name="s3", encryption_key_id="test-key-id",
        client=client, bucket="wrong-bucket" if wrong_bucket else "synthetic-bucket", _sse={"SSECustomerAlgorithm": "AES256", "SSECustomerKey": b"x" * 32})
    response = api.request("GET", active(api), 3)
    if wrong_bucket:
        private(response, 503)
        assert not api.calls and not client.calls
        return
    private(response, 200)
    assert response.content == b"synthetic-domain-fixture-poster"
    assert api.calls == [(asset.object_key, "approved-object-version")]
    assert client.calls[0][1]["VersionId"] == "approved-object-version"


@pytest.mark.parametrize("failure", [StorageError("storage_timeout"), StorageError("storage_not_found"), PermissionError("secret path")])
def test_storage_errors_are_sanitized_private_503(api, failure):
    api.read_error = failure
    response = api.request("GET", active(api), 3)
    private(response, 503)
    assert "secret" not in response.text and "storage_timeout" not in response.text


@pytest.mark.parametrize("mismatch", ["checksum", "size", "oversize", "mime", "backend", "key_id"])
def test_corrupt_or_misconfigured_asset_is_not_delivered(api, mismatch):
    asset = api.assets["poster"]
    if mismatch == "oversize":
        api.local._path(asset.object_key).write_bytes(b"x" * (MAX_ASSET_BYTES + 1))
    else:
        with api.factory.begin() as db:
            row = db.get(VisualCompanionAsset, asset.id)
            if mismatch == "checksum":
                row.sha256 = "0" * 64
            elif mismatch == "size":
                row.byte_size += 1
            elif mismatch == "mime":
                row.mime_type = "text/html"
            elif mismatch == "backend":
                row.storage_backend = "s3"
            elif mismatch == "key_id":
                row.encryption_key_id = "unavailable-test-key"
    response = api.request("GET", active(api), 3)
    if mismatch in {"checksum", "size", "mime"}:
        # Registered metadata is now fenced against the preview digest before
        # storage is opened, so this is a lifecycle conflict, not an I/O error.
        private(response, 409)
        assert response.json()["detail"]["code"] == "visual_bundle_changed"
    else:
        private(response, 503)
    assert b"synthetic-domain-fixture" not in response.content
    if mismatch in {"checksum", "size", "mime", "backend", "key_id"}:
        assert not api.calls


@pytest.mark.parametrize("corruption", ["same_length", "truncated"])
def test_stored_byte_corruption_is_rejected_with_unchanged_manifest(api, corruption):
    asset = api.assets["poster"]
    original = b"synthetic-domain-fixture-poster"
    corrupt = b"x" * len(original) if corruption == "same_length" else original[:-1]
    # Only synthetic local bytes change; registered digest/size remain the
    # previously approved values so the actual delivery verification is tested.
    api.local._path(asset.object_key).write_bytes(corrupt)
    response = api.request("GET", active(api), 3)
    private(response, 503)
    assert len(api.calls) == 1
    assert response.content != corrupt and original not in response.content


def test_unexpected_internal_failure_is_private_and_sanitized(api, monkeypatch):
    def unavailable():
        raise RuntimeError("private internal adapter diagnostic must never be returned")
    monkeypatch.setattr(routes, "get_source_storage", unavailable)
    response = api.request("GET", active(api), 3)
    private(response, 503)
    assert response.json()["detail"]["code"] == "visual_unavailable"
    assert "private internal" not in response.text


@pytest.mark.parametrize("change", ["viewer_revoked", "disabled", "deleted", "source_deleted", "legacy_inactive",
                                    "ownership", "asset_key", "asset_version", "asset_checksum", "asset_mime", "asset_purging"])
def test_reauthorization_after_storage_discards_loaded_bytes(api, change):
    def mutate():
        with api.factory.begin() as db:
            profile = db.get(VisualCompanion, api.profile_id)
            row = db.get(VisualCompanionAsset, api.assets["poster"].id)
            if change == "viewer_revoked":
                db.scalar(select(LegacyViewerAccess).where(LegacyViewerAccess.user_id == 3)).status = "revoked"
            elif change == "disabled":
                profile.enabled = False
            elif change == "deleted":
                VisualCompanionService().delete(db, 1, 1, profile.revision)
            elif change == "source_deleted":
                db.get(MediaSource, api.source_id).state = "deleted"
            elif change == "legacy_inactive":
                db.get(Legacy, 1).setup_status = "archived"
            elif change == "ownership":
                db.get(Legacy, 1).owner_user_id = 2
            elif change == "asset_key":
                row.object_key += "-changed"
            elif change == "asset_version":
                row.object_version = "changed-version"
            elif change == "asset_checksum":
                row.sha256 = "0" * 64
            elif change == "asset_mime":
                row.mime_type = "image/webp"
            elif change == "asset_purging":
                row.state = "purge_pending"
    api.after_read = mutate
    response = api.request("GET", active(api), 3)
    private(response, 409 if change in {"source_deleted", "ownership", "asset_purging", "asset_checksum", "asset_mime"} else 404)
    if change in {"asset_checksum", "asset_mime"}:
        assert response.json()["detail"]["code"] == "visual_bundle_changed"
    assert len(api.calls) == 1
    assert b"synthetic-domain-fixture" not in response.content


def test_replacement_during_content_load_discards_old_version(api):
    def replace():
        with api.factory() as db:
            profile = db.get(VisualCompanion, api.profile_id)
            candidate = ready_fixture(db, admission(db, api.source_id, profile.revision), api.local)
            VisualCompanionService().activate(db, 1, 1, approval(candidate, profile.revision))
            db.commit()
    api.after_read = replace
    response = api.request("GET", active(api), 3)
    private(response, 404)
    assert b"synthetic-domain-fixture" not in response.content


@pytest.mark.parametrize("case", ["path", "query", "body", "extra", "malformed_json", "nan", "crop_validator"])
def test_validation_422_is_private_and_never_echoes_input(api, case):
    secret = "private-submitted-value"
    method, path, kwargs = "PATCH", BASE, {"json": {"enabled": False, "expected_revision": secret}}
    if case == "path":
        method, path, kwargs = "GET", BASE.replace("legacies/1", "legacies/" + secret), {}
    elif case == "query":
        method, kwargs = "DELETE", {"params": {"expected_revision": secret}}
    elif case == "extra":
        kwargs = {"json": {"enabled": False, "expected_revision": 2, "object_key": secret}}
    elif case == "malformed_json":
        kwargs = {"content": '{"' + secret, "headers": {"Content-Type": "application/json"}}
    elif case in {"nan", "crop_validator"}:
        body = command(api.source_id).model_dump(mode="json")
        body["crop"]["x"] = float("nan") if case == "nan" else 0.9
        method, path = "POST", BASE + "/versions"
        kwargs = {"content": json.dumps(body), "headers": {"Content-Type": "application/json"}}
    response = api.request(method, path, 1, **kwargs)
    private(response, 422)
    assert response.json()["detail"]["code"] == "visual_request_invalid"
    assert secret not in response.text and api.source_id not in response.text
    assert "NaN" not in response.text
    assert not api.calls


def test_missing_invalid_auth_and_feature_disabled_are_private(api, monkeypatch):
    for headers in [{}, {"Authorization": "Bearer invalid-token"}]:
        response = api.client.get(active(api), headers=headers)
        private(response, 401)
        assert response.headers["www-authenticate"] == "Bearer"
    monkeypatch.setenv("VISUAL_PRESENCE_ENABLED", "false")
    get_settings.cache_clear()
    private(api.request("GET", active(api), 3), 404)
    assert not api.calls


def private_requests(api):
    with api.factory() as db:
        profile = db.get(VisualCompanion, api.profile_id)
        version = db.get(VisualCompanionVersion, api.version_id)
        revision = profile.revision
        activation = approval(version, revision).model_dump(mode="json")
    return [
        ("GET", BASE + "/capabilities", {}),
        ("GET", BASE, {}),
        ("POST", BASE + "/versions", {"json": command(api.source_id, revision).model_dump(mode="json")}),
        ("GET", BASE + f"/versions/{api.version_id}", {}),
        ("GET", BASE + f"/versions/{api.version_id}/manifest", {}),
        ("GET", preview(api), {}),
        ("GET", BASE + "/active-manifest", {}),
        ("GET", active(api), {}),
        ("POST", BASE + "/activate", {"json": activation}),
        ("PATCH", BASE, {"json": {"enabled": False, "expected_revision": revision}}),
        ("DELETE", BASE, {"params": {"expected_revision": revision}}),
    ]


def test_every_registered_private_path_requires_auth(api):
    for method, path, kwargs in private_requests(api):
        private(api.request(method, path, actor=None, **kwargs), 401)
    assert not api.calls


def test_disabled_flag_responses_and_erasure_stay_private(api, monkeypatch):
    monkeypatch.setenv("VISUAL_PRESENCE_ENABLED", "false")
    monkeypatch.setenv("VISUAL_PREPARATION_ENABLED", "false")
    get_settings.cache_clear()
    for method, path, kwargs in private_requests(api):
        expected = 200 if path.endswith("/capabilities") else 202 if method == "DELETE" else 404
        response = api.request(method, path, actor=1, **kwargs)
        private(response, expected)
        if path.endswith("/capabilities"):
            assert response.json()["enabled"] is False
    assert not api.calls


def test_preparation_flag_503_is_private_and_sanitized(api, monkeypatch):
    monkeypatch.setenv("VISUAL_PREPARATION_ENABLED", "false")
    get_settings.cache_clear()
    response = api.request("POST", BASE + "/versions", json=command(api.source_id).model_dump(mode="json"))
    private(response, 503)
    assert response.json() == {"detail": "Visual preparation is unavailable."}
    assert not api.calls


def test_unavailable_provider_503_is_private_without_internal_details(api):
    del api.client.app.dependency_overrides[routes.preparation_service]
    for actor in (1, 2, 3, 4):
        response = api.request("POST", BASE + "/versions", actor=actor,
                               json=command(api.source_id).model_dump(mode="json"))
        private(response, 503)
        assert response.json() == {"detail": {"code": "visual_provider_unavailable",
                                              "message": "Visual preparation is not available."}}
    assert not api.calls
