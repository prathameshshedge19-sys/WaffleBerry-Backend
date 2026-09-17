"""L21.4 synthesis worker, binding, privacy, fallback, and API acceptance."""

import asyncio
from datetime import timedelta
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import Base, build_engine
from app.models.collaboration import LegacyCollaborator
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.user import User
from app.models.viewer import LegacyViewerAccess
from app.models.voice_profile import VoiceAsset, VoiceJob
from app.services.media_storage import LocalSourceStorage
from app.services.security import create_access_token
from app.services.voice_profiles import (
    AuthorizedSpeechContext, LegacySpeechOrchestrator, VoiceJobService,
    VoiceProfileService, utcnow,
)
from app.services.voice_providers import ClonedSpeechRequest, FakeClonedSpeechProvider, VoiceProviderFailure, validate_synthesis_request
from app.services.voice_storage import VoiceStorage
from app.services.voice_synthesis_manifest import INFERENCE_CONFIG, ManifestError, inference_config_digest, load_and_verify_manifest, test_manifest as fake_manifest
from app.services.voice_synthesis_worker import VoiceSynthesisWorker
from tests.voice_l21_helpers import activate, prepare, reserve, seed


ROOT = Path(__file__).resolve().parents[1]


def auth(user_id):
    return {"Authorization": "Bearer " + create_access_token(user_id)}


def enable(monkeypatch):
    monkeypatch.setenv("VOICE_CLONING_ENABLED", "true")
    monkeypatch.setenv("VOICE_MESSAGE_PLAYBACK_ENABLED", "true")
    monkeypatch.setenv("VOICE_SYNTHESIS_PROVIDER", "fake")
    get_settings.cache_clear()


def test_manifest_is_exact_and_unstaged_default_fails_closed():
    assert INFERENCE_CONFIG == {"nfe_step": 48, "cfg_strength": 1.65,
        "sway_sampling_coef": -1.0, "speed": 0.97,
        "cross_fade_duration": 0.10, "target_rms": 0.1,
        "sample_rate": 24000, "channels": 1}
    with pytest.raises(ManifestError, match="voice_model_artifact_unavailable"):
        load_and_verify_manifest(ROOT / "voice-models" / "indicf5-manifest.json",
            ROOT / "voice-models")


def test_typed_provider_binding_and_malformed_output():
    text, reference = "नमस्कार.", b"synthetic-reference"
    request = ClonedSpeechRequest(legacy_id=1, profile_version_id="v1",
        authoritative_text=text, authoritative_text_digest=hashlib.sha256(text.encode()).hexdigest(),
        reference_audio=reference, reference_audio_digest=hashlib.sha256(reference).hexdigest(),
        reference_transcript=text, reference_transcript_digest=hashlib.sha256(text.encode()).hexdigest(),
        reference_binding_digest="a" * 64, language="mr", model_manifest_digest="b" * 64,
        purpose="message", operation_generation=1)
    validate_synthesis_request(request)
    changed = request.__class__(**{**request.__dict__, "authoritative_text": "changed"})
    with pytest.raises(VoiceProviderFailure, match="voice_provider_input_invalid"):
        validate_synthesis_request(changed)
    malformed = asyncio.run(FakeClonedSpeechProvider("malformed").synthesize(request))
    with pytest.raises(VoiceProviderFailure):
        from app.services.voice_providers import validate_speech
        validate_speech(malformed, text, 1)


def test_fake_warm_worker_publishes_private_wav_and_reuses_job(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    source = b"synthetic-reference-fixture"
    with sessions() as db:
        seed(db)
        version = prepare(db, reserve(db), source)
        activate(db, version)
        db.add(LegacyViewerAccess(legacy_id=1, user_id=3, status="active"))
        db.add(Conversation(id=42, user_id=3, legacy_id=1, title="Worker", mode="legacy"))
        db.flush()
        db.add(Message(id=42, conversation_id=42, role=MessageRole.ASSISTANT,
            content="Persisted assistant answer."))
        db.flush()
        manifest = fake_manifest()
        context = AuthorizedSpeechContext(1, "legacy", 3, 42, 1)
        job = LegacySpeechOrchestrator().admit_synthesis(db, context,
            authoritative_text="Persisted assistant answer.", purpose="message",
            request_key="message:42", model_manifest_digest=manifest.digest,
            inference_config_digest=inference_config_digest(), conversation_id=42,
            message_id=42)
        db.commit()
        reference = db.get(VoiceAsset, version.reference_asset_id)
    raw = LocalSourceStorage(str(tmp_path / "storage"))
    raw.put(reference.object_key, source, content_type="audio/wav")
    settings = get_settings().model_copy(update={"voice_synthesis_provider": "fake",
        "voice_synthesis_artifact_path": str(tmp_path),
        "media_local_storage_path": str(tmp_path / "storage")})
    worker = VoiceSynthesisWorker(sessions, VoiceStorage(raw), settings=settings,
        manifest=manifest, provider=FakeClonedSpeechProvider())
    try:
        assert worker.warmup().provider == "fake-cloned-speech-test-only"
        assert worker.run_once() == "ready"
    finally:
        worker.close()
    with sessions() as db:
        complete = db.get(VoiceJob, job.id)
        asset = db.scalar(select(VoiceAsset).where(VoiceAsset.job_id == job.id,
            VoiceAsset.kind == "generated"))
        assert complete.state == "succeeded"
        assert asset.state == "available" and asset.mime_type == "audio/wav"
        assert asset.expires_at <= asset.created_at + timedelta(hours=24, seconds=1)
        audio = VoiceStorage(raw).read_private(asset)
        assert audio[:4] == b"RIFF" and hashlib.sha256(audio).hexdigest() == asset.sha256
        repeated = LegacySpeechOrchestrator().admit_synthesis(db, context,
            authoritative_text="Persisted assistant answer.", purpose="message",
            request_key="message:42", model_manifest_digest=manifest.digest,
            inference_config_digest=inference_config_digest(), conversation_id=42,
            message_id=42)
        assert repeated.id == job.id
        asset.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        replacement = LegacySpeechOrchestrator().admit_synthesis(db, context,
            authoritative_text="Persisted assistant answer.", purpose="message",
            request_key="message:42", model_manifest_digest=manifest.digest,
            inference_config_digest=inference_config_digest(), conversation_id=42,
            message_id=42)
        assert replacement.id != job.id
        assert db.get(VoiceAsset, asset.id).state == "purge_pending"
    engine.dispose()


def test_worker_late_result_after_revoke_is_fenced(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'revoke-worker.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    source = b"synthetic-reference-fixture"
    with sessions() as db:
        seed(db)
        version = prepare(db, reserve(db), source)
        profile = activate(db, version)
        job = LegacySpeechOrchestrator().admit_synthesis(db,
            AuthorizedSpeechContext(1, "legacy", 1, None, 1),
            authoritative_text="Fixed preview.", purpose="preview",
            request_key="preview-v1", model_manifest_digest=fake_manifest().digest,
            inference_config_digest=inference_config_digest())
        db.commit()
        reference = db.get(VoiceAsset, version.reference_asset_id)
        revision = profile.revision
    raw = LocalSourceStorage(str(tmp_path / "revoke-storage"))
    raw.put(reference.object_key, source, content_type="audio/wav")

    class RevokeDuringInference(FakeClonedSpeechProvider):
        async def synthesize(self, request=None, **legacy):
            with sessions.begin() as db:
                VoiceProfileService().revoke(db, 1, 1, revision)
            return await super().synthesize(request, **legacy)

    settings = get_settings().model_copy(update={"voice_synthesis_provider": "fake",
        "media_local_storage_path": str(tmp_path / "revoke-storage")})
    worker = VoiceSynthesisWorker(sessions, VoiceStorage(raw), settings=settings,
        manifest=fake_manifest(), provider=FakeClonedSpeechProvider())
    worker.warmup()
    worker.provider = RevokeDuringInference()
    try:
        assert worker.run_once() == "stale"
    finally:
        worker.close()
    with sessions() as db:
        assert db.get(VoiceJob, job.id).state == "cancelled"
        assert db.scalar(select(VoiceAsset).where(
            VoiceAsset.job_id == job.id, VoiceAsset.kind == "generated")) is None
    engine.dispose()


def test_preview_and_message_authorization_and_same_text_fallback(test_context, monkeypatch):
    client, sessions, _, providers = test_context
    enable(monkeypatch)
    with sessions() as db:
        users = [User(id=i, full_name=f"Synthetic {i}", email=f"voice-{i}@example.invalid",
            password_hash="unused") for i in (201, 202, 203, 204)]
        db.add_all(users); db.flush()
        db.add_all([Legacy(id=301, owner_user_id=201, subject_name="One", setup_status="active"),
            Legacy(id=302, owner_user_id=201, subject_name="Two", setup_status="active")])
        db.flush()
        db.add(LegacyCollaborator(legacy_id=301, user_id=203, status="active"))
        db.add_all([LegacyViewerAccess(legacy_id=301, user_id=202, status="active"),
            LegacyViewerAccess(legacy_id=302, user_id=202, status="active")])
        db.commit()
        version = prepare(db, reserve(db, legacy_id=301, owner_id=201))
        activate(db, version, owner_id=201)
        c1 = Conversation(user_id=202, legacy_id=301, title="Legacy", mode="legacy")
        c2 = Conversation(user_id=202, legacy_id=302, title="Fallback", mode="legacy")
        rya = Conversation(user_id=202, legacy_id=301, title="Rya", mode="rya")
        db.add_all([c1, c2, rya]); db.flush()
        exact = "Rya remains text; do not answer again."
        m1 = Message(conversation_id=c1.id, role=MessageRole.ASSISTANT, content=exact)
        m2 = Message(conversation_id=c2.id, role=MessageRole.ASSISTANT, content=exact)
        mr = Message(conversation_id=rya.id, role=MessageRole.ASSISTANT, content=exact)
        db.add_all([m1, m2, mr]); db.commit()
        ids = c1.id, c2.id, rya.id, m1.id, m2.id, mr.id
    preview = client.post("/api/v1/legacies/301/voice-profile/preview", headers=auth(201))
    assert preview.status_code == 202 and preview.json()["voice_delivery"] == "preserved"
    assert client.post("/api/v1/legacies/301/voice-profile/preview", headers=auth(201),
        json={"voice_profile_id": "caller-controlled"}).status_code == 422
    for denied in (202, 203, 204):
        assert client.post("/api/v1/legacies/301/voice-profile/preview", headers=auth(denied)).status_code == 404
    c1, c2, rya, m1, m2, mr = ids
    admitted = client.post(f"/api/v1/legacy-conversations/{c1}/messages/{m1}/speech", headers=auth(202))
    assert admitted.status_code == 202 and admitted.json()["voice_delivery"] == "preserved"
    assert client.post(f"/api/v1/legacy-conversations/{c1}/messages/{m1}/speech", headers=auth(202),
        json={"version_id": "caller-controlled"}).status_code == 422
    job_url = f"/api/v1/voice-synthesis/jobs/{admitted.json()['job_id']}"
    assert client.get(job_url, headers=auth(202)).status_code == 200
    assert client.get(job_url, headers=auth(201)).status_code == 404
    assert client.get(f"{job_url}/content", headers=auth(202)).status_code == 404
    assert client.get(f"{job_url}/content", headers=auth(201)).status_code == 404
    assert client.post(f"/api/v1/legacy-conversations/{c1}/messages/{m1}/speech", headers=auth(204)).status_code == 404
    assert client.post(f"/api/v1/legacy-conversations/{rya}/messages/{mr}/speech", headers=auth(202)).status_code == 404
    fallback = client.post(f"/api/v1/legacy-conversations/{c2}/messages/{m2}/speech", headers=auth(202))
    assert fallback.status_code == 200 and fallback.headers["x-voice-delivery"] == "standard_fallback"
    assert providers.voice_provider.synthesis_calls[-1][0] == "Rya remains text; do not answer again."
    with sessions() as db:
        job = db.get(VoiceJob, admitted.json()["job_id"])
        assert job.authoritative_text == "Rya remains text; do not answer again."
        assert job.authoritative_text_digest == hashlib.sha256(job.authoritative_text.encode()).hexdigest()
    monkeypatch.setenv("VOICE_CLONING_ENABLED", "false")
    monkeypatch.setenv("VOICE_MESSAGE_PLAYBACK_ENABLED", "false")
    get_settings.cache_clear()
    disabled = client.post(f"/api/v1/legacy-conversations/{c1}/messages/{m1}/speech", headers=auth(202))
    assert disabled.status_code == 200
    assert disabled.headers["x-voice-delivery"] == "standard_fallback"
    assert providers.voice_provider.synthesis_calls[-1][0] == "Rya remains text; do not answer again."


def test_expired_generated_asset_is_fenced_and_queued_for_exact_purge(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'expiry.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        seed(db); version = prepare(db, reserve(db)); activate(db, version)
        asset = VoiceAsset(id="generated-expired", legacy_id=1,
            voice_profile_id=version.voice_profile_id, version_id=version.id,
            kind="generated", state="available", storage_backend="local",
            object_key=f"legarya/legacies/1/voice/{version.id}/generated/expired.wav",
            sha256="a" * 64, byte_count=44, mime_type="audio/wav", sample_rate=24000,
            channels=1, duration_ms=0, created_at=utcnow(), expires_at=utcnow()-timedelta(seconds=1))
        db.add(asset); db.commit()
        assert VoiceJobService().expire_generated(db) == 1
        db.commit(); db.refresh(asset)
        assert asset.state == "purge_pending"
        assert db.scalar(select(VoiceJob).where(VoiceJob.request_key.like("asset-purge:generated-expired:%")))
    engine.dispose()
