"""Synthetic L21 fixtures; no human audio or identity-bearing content."""

import asyncio
import hashlib
from uuid import uuid4

from sqlalchemy import select

from app.models.legacy import Legacy
from app.models.user import User
from app.models.voice_profile import VoiceAsset, VoiceProfile
from app.schemas.voice_profile import VoiceActivation, VoiceEnrollmentIntent
from app.services.voice_profiles import VoiceEnrollmentService, VoiceJobService, VoiceProfileService, canonical_digest, utcnow
from app.services.voice_providers import FakeReferencePreparationProvider


def seed(db):
    db.add_all([User(id=i, full_name=f"L21 synthetic user {i}",
        email=f"l21-{i}@example.invalid", password_hash="unused") for i in (1, 2, 3, 4)])
    db.flush()
    db.add_all([Legacy(id=i, owner_user_id=1, subject_name=f"Synthetic L21 {i}",
        setup_status="active") for i in (1, 2)])
    db.commit()


def intent(revision=0, key=None, **changes):
    values = {"request_key": key or str(uuid4()), "expected_revision": revision,
        "language": "mr", "consented": True,
        "consent_copy_version": "l21-voice-consent-v1",
        "policy_version": "l21-voice-policy-v1", "authority_basis": "self",
        "source_category": "self_recording", "presented_copy_digest": "a" * 64}
    values.update(changes)
    return VoiceEnrollmentIntent.model_validate(values)


def reserve(db, revision=0, legacy_id=1, key=None):
    version = VoiceEnrollmentService().reserve_intent(db, 1, legacy_id, intent(revision, key))
    db.commit()
    return version


def prepare(db, version, source=b"synthetic-reference-fixture"):
    output = asyncio.run(FakeReferencePreparationProvider().prepare(
        source=source, language="mr", operation_generation=version.operation_generation))
    asset = VoiceAsset(id=str(uuid4()), legacy_id=version.legacy_id,
        voice_profile_id=version.voice_profile_id, version_id=version.id,
        kind="reference", state="available", storage_backend="local",
        object_key=f"legarya/legacies/{version.legacy_id}/voice/{version.id}/reference.wav",
        sha256=hashlib.sha256(source).hexdigest(), byte_count=len(source),
        mime_type="audio/wav", sample_rate=24000, channels=1,
        duration_ms=1000, created_at=utcnow())
    version.status = "queued"
    db.add(asset)
    db.flush()
    jobs = VoiceJobService()
    job = jobs.enqueue(db, legacy_id=version.legacy_id, profile_id=version.voice_profile_id,
        version_id=version.id, kind="prepare", request_key=f"prepare:{version.id}",
        request_digest=canonical_digest({"version": version.id, "kind": "prepare"}), priority=20)
    db.commit()
    claimed = jobs.claim(db, "prepare")
    assert claimed.id == job.id
    token = claimed.lease_token
    db.commit()
    result = jobs.publish_prepared(db, job.id, token, output, reference_asset_id=asset.id,
        model_manifest={"provider": "fake", "revision": "test-only"},
        asr_manifest={"provider": "fake", "revision": "test-only"},
        inference_config={"sample_rate": 24000})
    db.commit()
    return result

def activate(db, version):
    profile = db.scalar(select(VoiceProfile).where(VoiceProfile.id == version.voice_profile_id))
    result = VoiceProfileService().activate(db, 1, version.legacy_id,
        VoiceActivation(version_id=version.id, expected_revision=profile.revision,
            binding_digest=version.binding_digest, approved=True))
    db.commit()
    return result
