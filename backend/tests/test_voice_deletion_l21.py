from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, func, select, update

from app.models.legacy import Legacy
from app.models.user import User
from app.models.voice_profile import VoiceAsset, VoiceConsentReceipt, VoiceJob, VoiceProfile, VoiceProfileVersion
from app.services.legacy_deletion import finalize_one, request_deletion
from app.services.media_sources import MediaSourceService
from app.services.voice_profiles import (
    AuthorizedSpeechContext, LegacySpeechOrchestrator, VoiceEnrollmentService,
    VoiceJobService, request_account_voice_purge,
)
from app.services.voice_storage import VoiceStorage
from tests.test_media_sources_l16 import media_db  # noqa: F401
from tests.voice_l21_helpers import intent


@pytest.fixture
def voice_media_db(media_db):
    factory, storage, settings = media_db
    yield factory, storage, settings
    # Several older SQLite fixtures call metadata.drop_all with FK enforcement
    # still on. Break this new aggregate's deliberate circular pointers first.
    with factory.begin() as db:
        db.execute(update(VoiceProfile).values(status="processing",
            current_version_id=None, desired_version_id=None))
        db.execute(update(VoiceProfileVersion).values(status="failed", reference_asset_id=None))
        for model in (VoiceAsset, VoiceJob, VoiceProfileVersion, VoiceConsentReceipt, VoiceProfile):
            db.execute(delete(model))


def test_legacy_deletion_fences_selection_waits_for_purge_then_removes_registry(voice_media_db):
    factory, storage, _ = voice_media_db
    with factory() as db:
        version = VoiceEnrollmentService().reserve_intent(db, 1, 1, intent())
        version.status = "queued"
        VoiceJobService().enqueue(db, legacy_id=1, profile_id=version.voice_profile_id,
            version_id=version.id, kind="prepare", request_key="prepare-before-delete",
            request_digest="a" * 64)
        db.commit()
        version_id = version.id
    with factory() as db:
        result = request_deletion(db, db.get(User, 1), 1, "DELETE LEGACY 1",
            source_service=MediaSourceService(storage=storage))
        assert result["status"] == "deleting"
    with factory() as db:
        profile = db.scalar(select(VoiceProfile).where(VoiceProfile.legacy_id == 1))
        version = db.get(VoiceProfileVersion, version_id)
        prepare = db.scalar(select(VoiceJob).where(VoiceJob.kind == "prepare"))
        assert profile.status == "deleting" and profile.current_version_id is None
        assert version.status == "purge_pending" and prepare.state == "cancelled"
        context = AuthorizedSpeechContext(legacy_id=1, mode="legacy", actor_user_id=3,
            turn_id=1, authorization_generation=1)
        assert LegacySpeechOrchestrator().resolve_version(db, context) is None
    assert finalize_one(factory, storage) == "legacy_cleanup_pending"
    with factory() as db:
        job = VoiceJobService().claim(db, "purge")
        token = job.lease_token
        db.commit()
        VoiceJobService().publish_purge(db, job.id, token)
        db.commit()
    assert finalize_one(factory, storage) == "legacy_erased"
    with factory() as db:
        assert db.get(Legacy, 1) is None
        for model in (VoiceAsset, VoiceJob, VoiceProfileVersion, VoiceConsentReceipt, VoiceProfile):
            assert db.scalar(select(func.count()).select_from(model).where(model.legacy_id == 1)) == 0


def test_account_deletion_internal_hook_is_transactional_and_parentless(voice_media_db):
    factory, _, _ = voice_media_db
    with factory() as db:
        version = VoiceEnrollmentService().reserve_intent(db, 1, 1, intent())
        db.commit()
        version_id = version.id
        profiles = request_account_voice_purge(db, 1)
        assert len(profiles) == 1 and profiles[0].status == "deleting"
        # The hook owns no commit, so a future account-deletion parent can make
        # its own marker/fence transaction atomic.
        db.rollback()
    with factory() as db:
        assert db.get(VoiceProfileVersion, version_id).status == "uploading"


def test_voice_storage_erases_only_registered_exact_key_and_records_absence(voice_media_db):
    factory, storage, _ = voice_media_db
    key = "legarya/legacies/1/voice/synthetic/reference.wav"
    storage.put(key, b"synthetic", content_type="audio/wav")
    with factory() as db:
        version = VoiceEnrollmentService().reserve_intent(db, 1, 1, intent())
        asset = VoiceAsset(id="voice-storage-asset", legacy_id=1,
            voice_profile_id=version.voice_profile_id, version_id=version.id,
            kind="reference", state="purge_pending", storage_backend=storage.backend_name,
            object_key=key, encryption_key_id=storage.encryption_key_id,
            purge_requested_at=datetime.now(timezone.utc))
        db.add(asset)
        db.commit()
    assert VoiceStorage(storage).erase_registered(factory, "voice-storage-asset") is True
    assert not storage.exists(key)
    with factory() as db:
        asset = db.get(VoiceAsset, "voice-storage-asset")
        assert asset.state == "purged" and asset.absence_checks == 1 and asset.absent_since is not None
