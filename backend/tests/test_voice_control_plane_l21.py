import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database import Base, build_engine
from app.models.collaboration import LegacyCollaborator
from app.models.memory import Memory, MemoryRevision
from app.models.conversation import Conversation, Message
from app.models.personality import LegacyPersonalityProfile
from app.models.progress import BuilderActivity, DailyPrompt
from app.models.story import Story, StoryVersion
from app.models.timeline import LifeEvent
from app.models.viewer import LegacyViewerAccess
from app.models.visitor import LegacyVisitorProfile
from app.models.voice_profile import VoiceAsset, VoiceConsentReceipt, VoiceJob, VoiceProfile, VoiceProfileVersion
from app.services.voice_profiles import (
    AuthorizedSpeechContext, LegacySpeechOrchestrator, StaleVoiceClaim,
    VoiceEnrollmentService, VoiceJobService, VoiceProfileService, canonical_digest, utcnow,
)
from app.services.voice_providers import (
    FakeClonedSpeechProvider, FakeReferencePreparationProvider, VoiceProviderFailure,
    validate_prepared, validate_speech,
)
from tests.voice_l21_helpers import activate, intent, prepare, reserve, seed


@pytest.fixture
def voice_db():
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = __import__("sqlalchemy.orm", fromlist=["sessionmaker"]).sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        seed(db)
        yield db
    engine.dispose()


def canonical_snapshot(db):
    models = (Memory, MemoryRevision, LifeEvent, Story, StoryVersion,
        Conversation, Message, BuilderActivity, DailyPrompt, LegacyPersonalityProfile,
        LegacyCollaborator, LegacyViewerAccess, LegacyVisitorProfile)
    return {model.__tablename__: sorted(repr(tuple(row)) for row in db.execute(select(model.__table__)).all())
        for model in models}


def test_enrollment_replacement_activation_revoke_and_zero_effects(voice_db):
    db = voice_db
    before = canonical_snapshot(db)
    first = reserve(db)
    assert first.status == "uploading"
    assert len(db.scalars(select(VoiceConsentReceipt)).all()) == 1
    prepare(db, first)
    profile = activate(db, first)
    assert (profile.status, profile.current_version_id, profile.desired_version_id) == ("active", first.id, None)

    second = reserve(db, revision=profile.revision)
    db.refresh(profile)
    assert profile.status == "active" and profile.current_version_id == first.id and profile.desired_version_id == second.id
    assert second.consent_receipt_id != first.consent_receipt_id
    prepare(db, second, b"synthetic-second-reference")
    activate(db, second)
    db.refresh(first)
    assert first.status == "purge_pending"
    assert db.scalar(select(VoiceJob).where(VoiceJob.version_id == first.id, VoiceJob.kind == "purge"))

    profile = db.get(VoiceProfile, profile.id)
    VoiceProfileService().revoke(db, 1, 1, profile.revision)
    db.commit()
    db.refresh(profile)
    assert profile.status == "revoked" and profile.current_version_id is None and profile.desired_version_id is None
    assert all(item.revoked_at is not None for item in db.scalars(select(VoiceConsentReceipt)).all())
    assert canonical_snapshot(db) == before


def test_enrollment_and_job_idempotency(voice_db):
    db = voice_db
    key = str(uuid4())
    first = VoiceEnrollmentService().reserve_intent(db, 1, 1, intent(key=key))
    db.commit()
    repeated = VoiceEnrollmentService().reserve_intent(db, 1, 1, intent(key=key))
    assert repeated.id == first.id
    with pytest.raises(HTTPException) as changed:
        VoiceEnrollmentService().reserve_intent(db, 1, 1,
            intent(key=key, authority_basis="authorized_representative"))
    assert changed.value.status_code == 409
    first.status = "queued"
    db.commit()
    digest = canonical_digest({"one": 1})
    service = VoiceJobService()
    job = service.enqueue(db, legacy_id=1, profile_id=first.voice_profile_id,
        version_id=first.id, kind="prepare", request_key="same-key", request_digest=digest)
    db.commit()
    assert service.enqueue(db, legacy_id=1, profile_id=first.voice_profile_id,
        version_id=first.id, kind="prepare", request_key="same-key", request_digest=digest).id == job.id
    with pytest.raises(HTTPException) as changed_job:
        service.enqueue(db, legacy_id=1, profile_id=first.voice_profile_id,
            version_id=first.id, kind="prepare", request_key="same-key", request_digest="b" * 64)
    assert changed_job.value.status_code == 409


def test_owner_only_service_and_revision_fence(voice_db):
    db = voice_db
    with pytest.raises(HTTPException) as forbidden:
        VoiceEnrollmentService().reserve_intent(db, 2, 1, intent())
    assert forbidden.value.status_code == 404
    version = reserve(db)
    with pytest.raises(HTTPException) as stale:
        VoiceProfileService().delete(db, 1, 1, 999)
    assert stale.value.status_code == 409
    profile = db.get(VoiceProfile, version.voice_profile_id)
    VoiceProfileService().delete(db, 1, 1, profile.revision)
    db.commit()
    assert profile.status == "deleting" and profile.current_version_id is None


def test_rya_never_resolves_or_enqueues_voice():
    class NoDatabaseAccess:
        def expire_all(self):
            raise AssertionError("Rya attempted voice database resolution")
    context = AuthorizedSpeechContext(legacy_id=1, mode="rya", actor_user_id=1,
        turn_id=None, authorization_generation=1)
    service = LegacySpeechOrchestrator()
    assert service.resolve_version(NoDatabaseAccess(), context) is None
    assert service.admit_synthesis(NoDatabaseAccess(), context,
        authoritative_text="Already decided answer.", purpose="live", request_key="one") is None


def test_orchestrator_uses_frozen_text_and_job_digest(voice_db):
    db = voice_db
    version = prepare(db, reserve(db))
    activate(db, version)
    context = AuthorizedSpeechContext(legacy_id=1, mode="legacy", actor_user_id=3,
        turn_id=42, authorization_generation=7)
    service = LegacySpeechOrchestrator()
    job = service.admit_synthesis(db, context, authoritative_text="The authoritative answer.",
        purpose="message", request_key="message-42")
    db.commit()
    assert job.kind == "synthesize" and job.priority == 50 and job.version_id == version.id
    again = service.admit_synthesis(db, context, authoritative_text="The authoritative answer.",
        purpose="message", request_key="message-42")
    assert again.id == job.id
    with pytest.raises(HTTPException):
        service.admit_synthesis(db, context, authoritative_text="Changed answer.",
            purpose="message", request_key="message-42")


def test_lease_expiry_and_stale_publication(voice_db):
    db = voice_db
    version = reserve(db)
    version.status = "queued"
    service = VoiceJobService()
    job = service.enqueue(db, legacy_id=1, profile_id=version.voice_profile_id,
        version_id=version.id, kind="prepare", request_key="lease-test",
        request_digest="c" * 64)
    db.commit()
    now = utcnow()
    first = service.claim(db, "prepare", now=now, lease_seconds=1)
    first_token = first.lease_token
    db.commit()
    second = service.claim(db, "prepare", now=now + timedelta(seconds=2), lease_seconds=30)
    assert second.id == job.id and second.lease_token != first_token and second.attempts == 2
    db.commit()
    fake = asyncio.run(FakeReferencePreparationProvider().prepare(
        source=b"fixture", language="mr", operation_generation=version.operation_generation))
    with pytest.raises(StaleVoiceClaim):
        service.publish_prepared(db, job.id, first_token, fake,
            reference_asset_id="missing", model_manifest={}, asr_manifest={}, inference_config={})


def test_heartbeat_retry_and_safe_terminal_failure(voice_db):
    db = voice_db
    version = reserve(db)
    version.status = "queued"
    service = VoiceJobService()
    job = service.enqueue(db, legacy_id=1, profile_id=version.voice_profile_id,
        version_id=version.id, kind="prepare", request_key="failure-test",
        request_digest="e" * 64)
    db.commit()
    claimed = service.claim(db, "prepare")
    original_expiry = claimed.lease_expires_at
    service.heartbeat(db, job.id, claimed.lease_token, now=utcnow() + timedelta(seconds=1))
    assert claimed.lease_expires_at > original_expiry
    service.fail(db, job.id, claimed.lease_token, "voice_worker_busy", retryable=True)
    assert claimed.state == "retry_wait" and claimed.safe_error_code == "voice_worker_busy"
    claimed.next_attempt_at = utcnow()
    db.commit()
    claimed = service.claim(db, "prepare")
    service.fail(db, job.id, claimed.lease_token, "voice_media_invalid", retryable=False)
    db.commit()
    assert claimed.state == "failed" and version.status == "failed"
    with pytest.raises(ValueError):
        service.enqueue(db, legacy_id=1, profile_id=version.voice_profile_id,
            version_id=version.id, kind="prepare", request_key="x" * 129,
            request_digest="e" * 64)


def test_fake_provider_outcomes_are_deterministic_and_validated():
    prepare_one = asyncio.run(FakeReferencePreparationProvider().prepare(
        source=b"fixture", language="mr", operation_generation=3))
    prepare_two = asyncio.run(FakeReferencePreparationProvider().prepare(
        source=b"fixture", language="mr", operation_generation=3))
    assert prepare_one == prepare_two
    validate_prepared(prepare_one, 3)
    speech = asyncio.run(FakeClonedSpeechProvider().synthesize(authoritative_text="नमस्कार",
        reference_audio=b"fixture", reference_text=prepare_one.transcript,
        language="mr", operation_generation=3))
    validate_speech(speech, "नमस्कार", 3)
    for provider in (FakeReferencePreparationProvider("failure"), FakeClonedSpeechProvider("failure")):
        with pytest.raises(VoiceProviderFailure):
            if isinstance(provider, FakeReferencePreparationProvider):
                asyncio.run(provider.prepare(source=b"x", language="mr", operation_generation=1))
            else:
                asyncio.run(provider.synthesize(authoritative_text="x", reference_audio=b"x",
                    reference_text="x", language="mr", operation_generation=1))
    with pytest.raises(TimeoutError):
        asyncio.run(FakeReferencePreparationProvider("timeout").prepare(source=b"x", language="mr", operation_generation=1))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(FakeClonedSpeechProvider("cancellation").synthesize(authoritative_text="x",
            reference_audio=b"x", reference_text="x", language="mr", operation_generation=1))
    for outcome in ("malformed", "stale_completion"):
        value = asyncio.run(FakeReferencePreparationProvider(outcome).prepare(source=b"x", language="mr", operation_generation=2))
        with pytest.raises(VoiceProviderFailure):
            validate_prepared(value, 2)
