"""L19 presentation aggregate. Commands participate in the caller's transaction.

No storage/provider calls or canonical writes are permitted inside these locks.
Every write follows Legacy, sources, profile,
versions, jobs, assets. Legacy serialization also fences source deletion.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.models.legacy import Legacy
from app.models.media_source import MediaArtifact, MediaSource
from app.models.visual_companion import (
    VisualCompanion, VisualCompanionVersion, VisualCompanionAsset, VisualGenerationJob,
)
from app.services.authorization import require_persona_legacy
from app.schemas.visual_companion import Crop

RECIPE = "portrait_2d_v1"
CONFIRMATION = "l19-likeness-v1"
ROLES = {"poster", "texture_atlas", "rig"}


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def conflict(code="visual_revision_conflict"):
    raise HTTPException(409, detail={"code": code, "message": "Visual Presence changed. Refresh and try again."})


def locked(db, model, *conditions):
    # Explicit transaction ownership must not turn populate_existing into a
    # rollback of an earlier command's unflushed changes in the same Session.
    db.flush()
    return db.scalars(select(model).where(*conditions).order_by(model.id)
                      .execution_options(populate_existing=True).with_for_update()).all()


def lock_scope(db, legacy_id, owner_id=None, *, require_active=True, allow_pending=False):
    rows = locked(db, Legacy, Legacy.id == legacy_id)
    legacy = rows[0] if rows else None
    if legacy is None or (owner_id is not None and (legacy.owner_user_id != owner_id or legacy.deletion_requested_at is not None)):
        raise HTTPException(404, detail="Legacy not found.")
    allowed = {"active", "collecting_identity"} if allow_pending else {"active"}
    if require_active and owner_id is not None and legacy.setup_status not in allowed:
        conflict("legacy_not_active")
    # Deliberately lock all this Legacy's sources in ID order before the profile.
    # This avoids a stale pointer discovery window when current A/candidate B change.
    sources = {s.id: s for s in locked(db, MediaSource, MediaSource.legacy_id == legacy_id)}
    profiles = locked(db, VisualCompanion, VisualCompanion.legacy_id == legacy_id)
    profile = profiles[0] if profiles else None
    versions = {v.id: v for v in locked(db, VisualCompanionVersion, VisualCompanionVersion.legacy_id == legacy_id)}
    return legacy, profile, versions, sources


def require_source(version, sources, db):
    source = sources.get(version.source_id)
    if (source is None or source.kind != "image" or source.safety_state != "clean"
            or source.state in {"uploading", "deleting", "deleted"}
            or source.generation != version.source_generation or source.sha256 != version.source_sha256):
        conflict("visual_source_unavailable")
    artifact = db.scalar(select(MediaArtifact).where(
        MediaArtifact.legacy_id == version.legacy_id, MediaArtifact.source_id == source.id,
        MediaArtifact.id == version.source_artifact_id,
        MediaArtifact.generation == version.source_artifact_generation,
        MediaArtifact.kind == "original", MediaArtifact.state == "available",
        MediaArtifact.sha256 == version.source_sha256).execution_options(populate_existing=True))
    if artifact is None:
        conflict("visual_source_unavailable")
    return artifact


def enqueue_purge(db, profile, version, now, delay_seconds=0):
    if version.state == "purged":
        return
    version.state = "purge_pending"
    version.updated_at = now
    # Drop private crop coordinates; retain only the audit digest/confirmation.
    version.crop_json = {}
    jobs = locked(db, VisualGenerationJob, VisualGenerationJob.version_id == version.id)
    purge = None
    for job in jobs:
        if job.kind == "prepare":
            job.state = "cancelled"
            job.lease_token = job.lease_expires_at = None
        else:
            purge = job
    if purge is None:
        purge = VisualGenerationJob(id=str(uuid4()), legacy_id=version.legacy_id,
            companion_id=profile.id, version_id=version.id, kind="purge", state="queued",
            attempts=0, next_attempt_at=now + timedelta(seconds=delay_seconds))
        db.add(purge)
    elif purge.state not in {"running", "queued", "retry_wait"}:
        purge.state = "queued"
        purge.next_attempt_at = now + timedelta(seconds=delay_seconds)
        purge.lease_token = purge.lease_expires_at = None
    for asset in locked(db, VisualCompanionAsset, VisualCompanionAsset.version_id == version.id):
        if asset.state != "purged":
            asset.state = "purge_pending"


def source_deleted_in_transaction(db, source):
    """Called only under L16's Legacy -> source lock, before its commit."""
    profiles = locked(db, VisualCompanion, VisualCompanion.legacy_id == source.legacy_id)
    if not profiles:
        return
    profile = profiles[0]
    versions = locked(db, VisualCompanionVersion,
        VisualCompanionVersion.legacy_id == source.legacy_id, VisualCompanionVersion.source_id == source.id)
    affected = {v.id for v in versions}
    if not affected:
        return
    now = utcnow()
    if profile.current_version_id in affected:
        profile.current_version_id = None
        profile.enabled = False
    if profile.desired_version_id in affected:
        profile.desired_version_id = None
    profile.revision += 1
    profile.updated_at = now
    for version in versions:
        enqueue_purge(db, profile, version, now)


class VisualCompanionService:
    def __init__(self, *, provider_name="fake-test-only", model_digest=None):
        # Real preparation is not selected until the separate license gate passes.
        self.provider_name = provider_name
        self.model_digest = model_digest or hashlib.sha256(b"l19-fake-test-only").hexdigest()

    def admit(self, db, owner_id, legacy_id, payload):
        db.flush()
        legacy, profile, versions, sources = lock_scope(db, legacy_id, owner_id, allow_pending=True)
        request = payload.model_dump(mode="json")
        # Expected revision is a concurrency precondition, not immutable intent.
        request.pop("expected_revision")
        request_digest = digest(request)
        key = str(payload.request_key)
        if profile:
            existing = next((v for v in versions.values() if v.request_key == key), None)
            if existing:
                if existing.request_digest != request_digest:
                    conflict("visual_request_conflict")
                return existing  # Historical receipt: NEVER restore pointers/enablement.
        if payload.expected_revision != (profile.revision if profile else 0):
            conflict()
        source = sources.get(str(payload.source_id))
        if source is None:
            raise HTTPException(404, detail="Source not found.")
        artifact = db.scalar(select(MediaArtifact).where(MediaArtifact.legacy_id == legacy_id,
            MediaArtifact.source_id == source.id, MediaArtifact.kind == "original",
            MediaArtifact.state == "available").execution_options(populate_existing=True))
        if artifact is None:
            conflict("visual_source_unavailable")
        now = utcnow()
        # No daily/lifetime preparation quota. Serial execution and physical
        # cleanup backpressure remain independent resource/privacy safeguards.
        # Physical cleanup backlog cannot grow unbounded under repeated replacements.
        backlog = sum(v.state == "purge_pending" for v in versions.values())
        if backlog >= 2:
            conflict("visual_cleanup_pending")
        if profile is None:
            profile = VisualCompanion(id=str(uuid4()), legacy_id=legacy_id, revision=1, enabled=False)
            db.add(profile)
            db.flush()
        crop = payload.crop.model_dump()
        version = VisualCompanionVersion(id=str(uuid4()), legacy_id=legacy_id, companion_id=profile.id,
            version_number=max((v.version_number for v in versions.values()), default=0) + 1,
            source_id=source.id, source_generation=source.generation,
            source_artifact_id=artifact.id, source_artifact_generation=artifact.generation,
            source_sha256=source.sha256, crop_json=crop, crop_digest=digest(crop),
            confirmed_by_user_id=owner_id, confirmed_at=now, confirmation_copy_version=CONFIRMATION,
            request_key=key, request_digest=request_digest, recipe_version=RECIPE,
            provider_name=self.provider_name, model_digest=self.model_digest,
            recipe_digest=digest({"recipe": RECIPE, "provider": self.provider_name, "model": self.model_digest}),
            state="queued", created_at=now, updated_at=now, expires_at=now + timedelta(days=7))
        require_source(version, sources, db)
        old_desired = versions.get(profile.desired_version_id)
        if old_desired and old_desired.id != profile.current_version_id:
            enqueue_purge(db, profile, old_desired, now)
        db.add(version)
        db.flush()  # Circular scoped pointer FK requires the version first.
        db.add(VisualGenerationJob(id=str(uuid4()), legacy_id=legacy_id, companion_id=profile.id,
            version_id=version.id, kind="prepare", state="queued", attempts=0, next_attempt_at=now))
        profile.desired_version_id = version.id
        profile.deleted_at = None  # Only a fresh confirmed request can reopen a tombstone.
        profile.revision += 1
        profile.updated_at = now
        return version

    def activate(self, db, owner_id, legacy_id, payload):
        legacy, profile, versions, sources = lock_scope(db, legacy_id, owner_id, allow_pending=True)
        if profile is None or profile.deleted_at or profile.revision != payload.expected_revision:
            conflict()
        version = versions.get(str(payload.version_id))
        if (version is None or profile.desired_version_id != version.id or version.state != "ready"
                or version.bundle_digest != payload.bundle_digest
                or version.confirmed_by_user_id != legacy.owner_user_id
                or (version.expires_at and aware(version.expires_at) <= utcnow())):
            conflict("visual_preview_changed")
        require_source(version, sources, db)
        _confirmed(version, legacy)
        _assets(db, version)
        now = utcnow()
        old = versions.get(profile.current_version_id)
        profile.current_version_id = version.id
        profile.enabled = True
        profile.revision += 1
        profile.updated_at = now
        version.approved_by_user_id = owner_id
        version.approved_at = now
        version.expires_at = None
        if old and old.id != version.id:
            enqueue_purge(db, profile, old, now, delay_seconds=60)
        return profile

    def toggle(self, db, owner_id, legacy_id, enabled, expected_revision):
        legacy, profile, versions, sources = lock_scope(db, legacy_id, owner_id, allow_pending=True)
        if profile is None or profile.deleted_at or profile.revision != expected_revision:
            conflict()
        if enabled:
            version = versions.get(profile.current_version_id)
            _approved(version, legacy)
            require_source(version, sources, db)
            _assets(db, version)
        profile.enabled = enabled
        profile.revision += 1
        profile.updated_at = utcnow()
        return profile

    def delete(self, db, owner_id, legacy_id, expected_revision):
        _, profile, versions, _ = lock_scope(db, legacy_id, owner_id, require_active=False)
        if profile is None:
            raise HTTPException(404, detail="Visual Presence not found.")
        if profile.deleted_at:
            return profile
        if profile.revision != expected_revision:
            conflict()
        now = utcnow()
        profile.enabled = False
        profile.current_version_id = profile.desired_version_id = None
        profile.deleted_at = profile.updated_at = now
        profile.revision += 1
        for version in versions.values():
            enqueue_purge(db, profile, version, now)
        return profile


def _confirmed(version, legacy):
    if (version is None or version.confirmed_by_user_id != legacy.owner_user_id
            or version.confirmation_copy_version != CONFIRMATION or not version.confirmed_at
            or version.recipe_version != RECIPE or not version.crop_json):
        conflict("visual_confirmation_changed")
    try:
        Crop.model_validate(version.crop_json)
        valid = (digest(version.crop_json) == version.crop_digest and
            digest({"recipe": version.recipe_version, "provider": version.provider_name,
                    "model": version.model_digest}) == version.recipe_digest)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        conflict("visual_confirmation_changed")


def _approved(version, legacy):
    if (version is None or version.state != "ready" or not version.approved_at
            or version.approved_by_user_id != legacy.owner_user_id
            or version.confirmed_by_user_id != legacy.owner_user_id):
        conflict("visual_unavailable")
    _confirmed(version, legacy)


def _assets(db, version):
    assets = db.scalars(select(VisualCompanionAsset).where(
        VisualCompanionAsset.legacy_id == version.legacy_id,
        VisualCompanionAsset.companion_id == version.companion_id,
        VisualCompanionAsset.version_id == version.id, VisualCompanionAsset.state == "available")
        .execution_options(populate_existing=True)).all()
    if len(assets) != 3 or {a.logical_role for a in assets} != ROLES or not version.bundle_digest:
        conflict("visual_bundle_incomplete")
    # Bind approval/read to the same exact manifest that publication committed.
    # A stale digest must not authorize changed registered content metadata.
    if any(a.byte_size is None or a.byte_size <= 0 or not a.sha256 for a in assets):
        conflict("visual_bundle_incomplete")
    if sum(a.byte_size for a in assets) > 2 * 1024 * 1024:
        conflict("visual_bundle_incomplete")
    expected = digest({"recipe": version.recipe_version, "assets": [
        {"role": a.logical_role, "sha256": a.sha256, "byte_size": a.byte_size, "mime_type": a.mime_type}
        for a in sorted(assets, key=lambda a: a.logical_role)]})
    if expected != version.bundle_digest:
        conflict("visual_bundle_changed")
    return assets


def read_scope(db, user_id, legacy_id, *, viewer=False, version_id=None):
    # Freshly evaluate current role, pointer and source, including long-lived tests
    # or internal callers reusing a Session with expire_on_commit=False.
    db.flush()
    db.expire_all()
    legacy = db.get(Legacy, legacy_id)
    if viewer:
        require_persona_legacy(db, user_id, legacy_id)
    elif legacy is None or legacy.owner_user_id != user_id:
        raise HTTPException(404, detail="Legacy not found.")
    # Private owner preparation/approval is independent of identity setup.
    # Visitor call availability still follows the existing active-Legacy rules.
    allowed = {"active"} if viewer else {"active", "collecting_identity"}
    if legacy.deletion_requested_at is not None or legacy.setup_status not in allowed:
        raise HTTPException(404, detail="Visual Presence unavailable.")
    profile = db.scalar(select(VisualCompanion).where(VisualCompanion.legacy_id == legacy_id))
    if profile is None or profile.deleted_at or (viewer and not profile.enabled):
        raise HTTPException(404, detail="Visual Presence unavailable.")
    selected = profile.current_version_id if viewer else version_id
    version = db.scalar(select(VisualCompanionVersion).where(
        VisualCompanionVersion.legacy_id == legacy_id, VisualCompanionVersion.companion_id == profile.id,
        VisualCompanionVersion.id == selected))
    if (version is None or version.state != "ready"
            or (version.expires_at and aware(version.expires_at) <= utcnow())
            or (not viewer and selected not in {profile.current_version_id, profile.desired_version_id})):
        raise HTTPException(404, detail="Visual Presence unavailable.")
    if viewer:
        _approved(version, legacy)
    else:
        _confirmed(version, legacy)
    source = db.get(MediaSource, version.source_id)
    require_source(version, {source.id: source} if source else {}, db)
    return profile, version, _assets(db, version)


def manifest(db, user_id, legacy_id, *, viewer=False, version_id=None):
    profile, version, assets = read_scope(db, user_id, legacy_id, viewer=viewer, version_id=version_id)
    base = f"/api/v1/legacies/{legacy_id}/visual-companion"
    content_base = base + ("/active" if viewer else f"/versions/{version.id}")
    return {"version_id": version.id, "revision": profile.revision, "recipe": version.recipe_version,
        "bundle_digest": version.bundle_digest, "lease_seconds": 15, "valid_until": utcnow() + timedelta(seconds=15),
        "assets": [{"id": a.id, "role": a.logical_role, "mime_type": a.mime_type,
            "byte_size": a.byte_size, "sha256": a.sha256,
            "content_path": f"{content_base}/assets/{a.id}/content"} for a in sorted(assets, key=lambda a: a.logical_role)]}
