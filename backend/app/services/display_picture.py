"""Static, owner-selected source image. No facial model or factual writes.

The selection lives in existing presentation-source metadata. Legacy locking
serializes selections and source/Legacy deletion; explicit selection history
prevents an older animated portrait from reappearing after source deletion.
"""
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.models.legacy import Legacy
from app.models.media_source import MediaArtifact, MediaSource
from app.models.visual_companion import VisualCompanion, VisualCompanionVersion
from app.services.authorization import is_owner, require_legacy, require_persona_legacy

MARKER = "display_picture"


def authorize(db, user_id, legacy_id):
    db.expire_all()
    legacy = db.get(Legacy, legacy_id)
    if legacy is None or not is_owner(user_id, legacy):
        legacy = require_persona_legacy(db, user_id, legacy_id)
    if legacy.setup_status not in {"active", "collecting_identity"}:
        raise HTTPException(404, detail="Legacy unavailable.")
    return legacy


def eligible(source):
    return (source is not None and source.kind == "image"
            and source.processing_purpose == "visual_reference"
            and source.state == "ready" and source.safety_state == "clean")


def selection(db, legacy_id):
    rows = db.scalars(select(MediaSource).where(MediaSource.legacy_id == legacy_id,
        MediaSource.metadata_json[MARKER].as_string().is_not(None))).all()
    if rows:
        chosen = [s for s in rows if (s.metadata_json.get(MARKER) or {}).get("selected")]
        if len(chosen) != 1 or not eligible(chosen[0]):
            return None
        return chosen[0], chosen[0].metadata_json[MARKER]["revision"]
    # Preserve existing owner-approved pictures without regenerating or changing
    # their stored assets. Display the full original, not the previous face crop.
    profile = db.scalar(select(VisualCompanion).where(VisualCompanion.legacy_id == legacy_id))
    if not profile or not profile.enabled or profile.deleted_at or not profile.current_version_id:
        return None
    version = db.get(VisualCompanionVersion, profile.current_version_id)
    if not version or version.legacy_id != legacy_id or version.state != "ready" or not version.approved_at or version.removed_at:
        return None
    source = db.get(MediaSource, version.source_id)
    if not eligible(source) or source.legacy_id != legacy_id or source.generation != version.source_generation or source.sha256 != version.source_sha256:
        return None
    return source, "approved-" + version.id


def scope(db, user_id, legacy_id):
    authorize(db, user_id, legacy_id)
    selected = selection(db, legacy_id)
    if not selected:
        return None
    source, revision = selected
    artifact = db.scalar(select(MediaArtifact).where(MediaArtifact.legacy_id == legacy_id,
        MediaArtifact.source_id == source.id, MediaArtifact.generation == source.generation,
        MediaArtifact.kind == "original", MediaArtifact.state == "available"))
    if not artifact or artifact.sha256 != source.sha256 or artifact.byte_size != source.size_bytes:
        return None
    return source, revision, artifact


def select_picture(db, user_id, legacy_id, source_id):
    db.scalar(select(Legacy).where(Legacy.id == legacy_id).with_for_update())
    require_legacy(db, user_id, legacy_id, owner_only=True)
    source = db.scalar(select(MediaSource).where(MediaSource.legacy_id == legacy_id,
        MediaSource.id == source_id).execution_options(populate_existing=True).with_for_update())
    if not eligible(source):
        raise HTTPException(409, detail="The uploaded picture is not ready. Please retry.")
    artifact = db.scalar(select(MediaArtifact).where(MediaArtifact.legacy_id == legacy_id,
        MediaArtifact.source_id == source.id, MediaArtifact.generation == source.generation,
        MediaArtifact.kind == "original", MediaArtifact.state == "available"))
    if not artifact or artifact.sha256 != source.sha256 or artifact.byte_size != source.size_bytes:
        raise HTTPException(409, detail="The uploaded picture is unavailable. Please retry.")
    existing = selection(db, legacy_id)
    if existing and existing[0].id == source.id and source.metadata_json.get(MARKER, {}).get("selected"):
        return
    rows = db.scalars(select(MediaSource).where(MediaSource.legacy_id == legacy_id,
        MediaSource.metadata_json[MARKER].as_string().is_not(None)).order_by(MediaSource.id).with_for_update()).all()
    for row in rows:
        row.metadata_json = {**row.metadata_json, MARKER: {**row.metadata_json[MARKER], "selected": False}}
    source.metadata_json = {**source.metadata_json, MARKER: {"selected": True, "revision": str(uuid4())}}
    # Explicit static selection supersedes old animation publication. This also
    # prevents fallback resurrection after source erasure clears its metadata.
    profile = db.scalar(select(VisualCompanion).where(VisualCompanion.legacy_id == legacy_id).with_for_update())
    if profile and profile.enabled:
        profile.enabled = False
        profile.revision += 1
    db.flush()
