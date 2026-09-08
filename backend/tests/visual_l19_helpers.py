"""Synthetic, non-identity-bearing L19 fixtures shared by acceptance suites."""

import hashlib
import io
from datetime import timedelta
from uuid import uuid4

from PIL import Image
from sqlalchemy import select

from app.database import Base
from app.models.legacy import Legacy
from app.models.user import User
from app.models.media_source import MediaSource, MediaArtifact
from app.models.memory import Memory
from app.schemas.visual_companion import VersionCreate, Activation
from app.services.visual_companions import utcnow, VisualCompanionService


def seed(db):
    db.add_all([User(id=i, full_name=f"L19 synthetic user {i}", email=f"l19-{i}@example.invalid", password_hash="not-a-password") for i in (1, 2, 3)])
    db.flush()
    db.add_all([Legacy(id=i, owner_user_id=1, subject_name=f"Synthetic L19 {i}", setup_status="active") for i in (1, 2)])
    db.flush()
    db.add(Memory(legacy_id=1, canonical_text="Synthetic QA subject learned gardening in 1998.",
        category="life_event", source_language="english", source_excerpt="Synthetic fixture",
        confidence=1, status="active", operation_type="explicit_save", explicit_save=True,
        normalized_fingerprint="l19-existing-canonical-memory"))
    db.commit()


def source(db, storage, legacy_id=1):
    data = io.BytesIO()
    Image.new("RGB", (256, 256), (90, 110, 130)).save(data, "PNG")
    body = data.getvalue()
    sha = hashlib.sha256(body).hexdigest()
    source_id, artifact_id = str(uuid4()), str(uuid4())
    key = f"synthetic-sources/{source_id}/{artifact_id}"
    storage.put(key, body, content_type="image/png")
    db.add(MediaSource(id=source_id, legacy_id=legacy_id, uploader_user_id=1,
        kind="image", processing_purpose="visual_reference", original_filename="synthetic-l19.png",
        declared_mime_type="image/png", detected_mime_type="image/png", declared_size_bytes=len(body),
        size_bytes=len(body), sha256=sha, state="ready", safety_state="clean", generation=1,
        upload_request_key=str(uuid4()), upload_request_digest="0" * 64,
        upload_expires_at=utcnow() + timedelta(hours=1)))
    db.flush()
    db.add(MediaArtifact(id=artifact_id, legacy_id=legacy_id, source_id=source_id, generation=1,
        kind="original", logical_key="original", storage_backend=storage.backend_name,
        object_key=key, encryption_key_id=storage.encryption_key_id, state="available", byte_size=len(body), sha256=sha, mime_type="image/png"))
    db.commit()
    return source_id


def command(source_id, revision=0, key=None, **changes):
    return VersionCreate.model_validate({"source_id": source_id,
        "crop": {"x": 0, "y": 0, "width": 1, "height": 1, "rotation": 0},
        "confirmed": True, "confirmation_copy_version": "l19-likeness-v1",
        "request_key": key or str(uuid4()), "expected_revision": revision, **changes})


def admission(db, source_id, revision=0, legacy_id=1, key=None):
    version = VisualCompanionService().admit(db, 1, legacy_id, command(source_id, revision, key))
    db.commit()
    return version.id


def approval(version, revision):
    return Activation(version_id=version.id, expected_revision=revision,
        bundle_digest=version.bundle_digest, approved=True)


def factual_snapshot(db):
    # Snapshot values, not only counts: updates/replacements must also be caught.
    excluded = {"media_sources", "media_artifacts", "media_processing_jobs",
        "visual_companions", "visual_companion_versions", "visual_companion_assets", "visual_generation_jobs"}
    return {name: sorted([repr(tuple(row)) for row in db.execute(select(table)).all()])
        for name, table in Base.metadata.tables.items() if name not in excluded}
