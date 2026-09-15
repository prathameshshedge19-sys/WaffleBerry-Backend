"""L21.3 secure enrollment and exact-reference tests; synthetic audio only."""

import asyncio
from array import array
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models.collaboration import LegacyCollaborator
from app.models.conversation import Conversation, Message
from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryRevision
from app.models.personality import LegacyPersonalityProfile
from app.models.progress import BuilderActivity, DailyPrompt
from app.models.story import Story, StoryVersion
from app.models.timeline import LifeEvent
from app.models.user import User
from app.models.viewer import LegacyViewerAccess
from app.models.visitor import LegacyVisitorProfile
from app.models.voice_profile import VoiceAsset, VoiceConsentReceipt, VoiceJob, VoiceProfileVersion
from app.services.security import create_access_token
from app.services.voice_profiles import (
    CONSENT_TEXT_DIGEST, VoiceEnrollmentService, utcnow,
)
from app.services.voice_providers import validate_prepared
from app.services.voice_providers import FakeReferencePreparationProvider
from app.services.voice_reference import (
    FfmpegPreparation, RealReferencePreparationProvider, VoicePreparationError,
    WhisperLargeV3Turbo, _pcm_wav, normalize_transcript,
    select_reference_segment,
)
from app.services.voice_worker import VoiceWorker
from app.services.voice_storage import VoiceStorage


def _headers(user_id=501):
    return {"Authorization": "Bearer " + create_access_token(user_id)}


def _intent():
    return {"request_key": str(uuid4()), "expected_revision": 0,
        "language": "mr", "consented": True,
        "consent_copy_version": "l21-voice-consent-v1",
        "policy_version": "l21-voice-policy-v1", "authority_basis": "self",
        "source_category": "self_recording",
        "presented_copy_digest": CONSENT_TEXT_DIGEST}


def _synthetic_speech(seconds=10, *, sample_rate=24000):
    values = array("h")
    for index in range(round(seconds * sample_rate)):
        time = index / sample_rate
        envelope = 0.35 + 0.55 * abs(math.sin(2 * math.pi * 1.7 * time))
        value = int(9000 * envelope * math.sin(2 * math.pi * (145 + 20 * math.sin(time)) * time))
        values.append(value)
    return values


def _canonical_counts(db):
    models = (Memory, MemoryRevision, LifeEvent, Story, StoryVersion,
        Conversation, Message, BuilderActivity, DailyPrompt,
        LegacyPersonalityProfile, LegacyCollaborator, LegacyViewerAccess,
        LegacyVisitorProfile)
    return {model.__tablename__: db.scalar(
        select(func.count()).select_from(model)) for model in models}


@pytest.fixture
def enrollment_api(test_context, monkeypatch, tmp_path):
    client, factory, _, _ = test_context
    monkeypatch.setenv("VOICE_CLONING_ENABLED", "true")
    monkeypatch.setenv("VOICE_ENROLLMENT_ENABLED", "true")
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("MEDIA_LOCAL_STORAGE_PATH", str(tmp_path / "private"))
    get_settings.cache_clear()
    with factory.begin() as db:
        db.add(User(id=501, full_name="L21.3 owner",
            email="l21-3-owner@example.invalid", password_hash="unused"))
        db.flush()
        db.add(Legacy(id=501, owner_user_id=501,
            subject_name="Synthetic enrollment", setup_status="active"))
    yield client, factory
    get_settings.cache_clear()


def test_private_upload_reservation_content_and_no_key_exposure(enrollment_api):
    client, factory = enrollment_api
    base = "/api/v1/legacies/501/voice-profile"
    intent = client.post(base + "/enrollments", headers=_headers(), json=_intent())
    assert intent.status_code == 202
    version_id = intent.json()["version_id"]
    source = _pcm_wav(_synthetic_speech(4))
    uploaded = client.put(base + f"/enrollments/{version_id}/content",
        headers={**_headers(), "Content-Type": "audio/wav"}, content=source)
    assert uploaded.status_code == 202, uploaded.text
    assert uploaded.json()["lifecycle"] == "queued"
    assert "object_key" not in uploaded.text and "reference_transcript" not in uploaded.text
    with factory() as db:
        version = db.get(VoiceProfileVersion, version_id)
        asset = db.scalar(select(VoiceAsset).where(VoiceAsset.version_id == version_id))
        job = db.scalar(select(VoiceJob).where(VoiceJob.version_id == version_id))
        assert version.status == "queued" and job.kind == "prepare"
        assert asset.kind == "original" and asset.state == "available"
        assert asset.object_key.startswith("legarya/legacies/501/voice/")
        assert asset.sha256 == hashlib.sha256(source).hexdigest()


def test_upload_rejects_type_size_and_changed_consent(enrollment_api, monkeypatch):
    client, factory = enrollment_api
    base = "/api/v1/legacies/501/voice-profile"
    bad = _intent()
    bad["presented_copy_digest"] = "a" * 64
    response = client.post(base + "/enrollments", headers=_headers(), json=bad)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "voice_consent_invalid"
    intent = client.post(base + "/enrollments", headers=_headers(), json=_intent())
    version_id = intent.json()["version_id"]
    unsupported = client.put(base + f"/enrollments/{version_id}/content",
        headers={**_headers(), "Content-Type": "application/octet-stream"}, content=b"x")
    assert unsupported.status_code == 415
    monkeypatch.setenv("VOICE_ENROLLMENT_MAX_BYTES", "1024")
    get_settings.cache_clear()
    too_large = client.put(base + f"/enrollments/{version_id}/content",
        headers={**_headers(), "Content-Type": "audio/wav"}, content=b"x" * 1025)
    assert too_large.status_code == 413
    with factory() as db:
        assert not db.scalars(select(VoiceAsset)).all()
        assert len(db.scalars(select(VoiceConsentReceipt)).all()) == 1


def test_probe_establishes_actual_type_and_rejects_mime_spoof(monkeypatch):
    settings = get_settings()
    media = FfmpegPreparation(settings)
    captured = []
    value = {"format": {"format_name": "mp3", "duration": "8.0"},
        "streams": [{"index": 0, "codec_type": "audio", "codec_name": "mp3",
            "channels": 1, "sample_rate": "44100", "duration": "8.0"}]}
    def probe(arguments, **_kwargs):
        captured.extend(arguments)
        return json.dumps(value).encode()
    monkeypatch.setattr(media, "_run", probe)
    with pytest.raises(VoicePreparationError) as failure:
        media.probe(__import__("pathlib").Path("fixed.media"), "audio/wav",
            __import__("pathlib").Path("."))
    assert failure.value.code == "voice_media_type_mismatch"
    assert "-nostdin" not in captured
    assert captured[captured.index("-protocol_whitelist") + 1] == "file,pipe"


def test_corrupt_media_duration_limit_and_tool_timeout_fail_safely(monkeypatch, tmp_path):
    media = FfmpegPreparation(get_settings())
    monkeypatch.setattr(media, "_run", lambda *_args, **_kwargs: b"not-json")
    with pytest.raises(VoicePreparationError) as corrupt:
        media.probe(Path("fixed.media"), "audio/wav", tmp_path)
    assert corrupt.value.code == "voice_media_invalid"

    too_long = {"format": {"format_name": "wav", "duration": "601.0"},
        "streams": [{"index": 0, "codec_type": "audio",
            "codec_name": "pcm_s16le", "channels": 1,
            "sample_rate": "24000", "duration": "601.0"}]}
    monkeypatch.setattr(media, "_run",
        lambda *_args, **_kwargs: json.dumps(too_long).encode())
    with pytest.raises(VoicePreparationError) as duration:
        media.probe(Path("fixed.media"), "audio/wav", tmp_path)
    assert duration.value.code == "voice_media_duration_exceeded"

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=1)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(VoicePreparationError) as timed_out:
        FfmpegPreparation(get_settings())._run(["ffprobe"], cwd=tmp_path)
    assert timed_out.value.code == "voice_media_timeout"


def test_deterministic_selector_freezes_before_exact_marathi_asr():
    settings = get_settings()
    samples = array("h", [0] * (2 * 24000))
    samples.extend(_synthetic_speech(9))
    samples.extend(array("h", [0] * (2 * 24000)))
    normalized = _pcm_wav(samples)
    selection = select_reference_segment(normalized, settings)
    assert 5000 <= selection.duration_ms <= 8000
    assert selection.start_ms >= 1800

    class Transcriber:
        def __init__(self): self.digest = None
        def transcribe(self, data, language):
            self.digest = hashlib.sha256(data).hexdigest()
            return "  नमस्कार,   माझे नाव सई आहे.  ", "hi"
    class Media:
        def probe(self, *_args): return None
        def decode(self, *_args): return normalized
    transcriber = Transcriber()
    provider = RealReferencePreparationProvider(settings, transcriber=transcriber)
    provider.media = Media()
    result = asyncio.run(provider.prepare(source=b"synthetic-consented-fixture",
        language="mr", operation_generation=7, declared_mime="audio/wav"))
    validate_prepared(result, 7)
    assert result.transcript == "नमस्कार, माझे नाव सई आहे."
    assert result.detected_language == "hi"
    assert result.audio_digest == transcriber.digest
    assert result.audio_digest == hashlib.sha256(result.reference_audio).hexdigest()
    assert result.binding_digest not in {result.audio_digest, result.transcript_digest}


def test_whisper_requests_marathi_transcription_with_attention_mask():
    calls = {}

    class Processor:
        def __call__(self, audio, **kwargs):
            calls["processor"] = (len(audio), kwargs)
            return SimpleNamespace(input_features="features", attention_mask="mask")

        def batch_decode(self, generated, **kwargs):
            calls["decode"] = (generated, kwargs)
            return ["नमस्कार जग"]

    class Model:
        generation_config = SimpleNamespace(lang_to_id={"<|mr|>": 50320})

        def detect_language(self, features):
            calls["detect"] = features
            return [50320]

        def generate(self, features, **kwargs):
            calls["generate"] = (features, kwargs)
            return "tokens"

    class Torch:
        class inference_mode:
            def __enter__(self): return self
            def __exit__(self, *_args): return False

    transcriber = WhisperLargeV3Turbo.__new__(WhisperLargeV3Turbo)
    transcriber.processor = Processor()
    transcriber.model = Model()
    transcriber.torch = Torch()
    text, language = transcriber.transcribe(_pcm_wav(_synthetic_speech(5)), "mr")
    assert (text, language) == ("नमस्कार जग", "mr")
    assert calls["processor"][1]["return_attention_mask"] is True
    assert calls["detect"] == "features"
    assert calls["generate"] == ("features", {
        "attention_mask": "mask", "language": "marathi",
        "task": "transcribe", "max_new_tokens": 192})
    assert calls["decode"] == ("tokens", {"skip_special_tokens": True})


def test_silence_and_non_speech_fail_safely():
    settings = get_settings()
    with pytest.raises(VoicePreparationError) as silence:
        select_reference_segment(_pcm_wav(array("h", [0] * (8 * 24000))), settings)
    assert silence.value.code == "voice_speech_insufficient"
    with pytest.raises(VoicePreparationError) as tone:
        select_reference_segment(_pcm_wav(array("h", [int(5000 * math.sin(
            2 * math.pi * 220 * index / 24000)) for index in range(8 * 24000)])), settings)
    assert tone.value.code == "voice_non_speech"
    assert normalize_transcript("  नमस्कार\n  जग  ") == "नमस्कार जग"

    discontinuous = _synthetic_speech(3)
    discontinuous.extend(array("h", [0] * 24000))
    discontinuous.extend(_synthetic_speech(3))
    with pytest.raises(VoicePreparationError) as uncertain:
        select_reference_segment(_pcm_wav(discontinuous), settings)
    assert uncertain.value.code == "voice_speaker_uncertain"


@pytest.mark.parametrize(("raw", "detected", "expected"), [
    ("   ", "mr", "voice_transcript_empty"),
    ("This is not Marathi.", "en", "voice_language_mismatch"),
    ("यह केवल हिंदी भाषा है", "hi", "voice_language_mismatch"),
])
def test_empty_transcript_and_language_mismatch_fail_safely(
        monkeypatch, tmp_path, raw, detected, expected):
    monkeypatch.setenv("VOICE_TEMP_PATH", str(tmp_path / "worker"))
    get_settings.cache_clear()
    settings = get_settings()
    normalized = _pcm_wav(_synthetic_speech(8))

    class Transcriber:
        def transcribe(self, _data, _language):
            return raw, detected

    class Media:
        def probe(self, *_args): return None
        def decode(self, *_args): return normalized

    provider = RealReferencePreparationProvider(settings, transcriber=Transcriber())
    provider.media = Media()
    try:
        with pytest.raises(VoicePreparationError) as failure:
            asyncio.run(provider.prepare(source=b"consented-synthetic-fixture",
                language="mr", operation_generation=1,
                declared_mime="audio/wav"))
        assert failure.value.code == expected
        assert not list((tmp_path / "worker").glob("prepare-*"))
    finally:
        get_settings.cache_clear()


def test_worker_publication_verifies_reference_then_purges_original(enrollment_api):
    client, factory = enrollment_api
    base = "/api/v1/legacies/501/voice-profile"
    with factory() as db:
        canonical_before = _canonical_counts(db)
    intent = client.post(base + "/enrollments", headers=_headers(), json=_intent())
    version_id = intent.json()["version_id"]
    source = _pcm_wav(_synthetic_speech(1))
    uploaded = client.put(base + f"/enrollments/{version_id}/content",
        headers={**_headers(), "Content-Type": "audio/wav"}, content=source)
    assert uploaded.status_code == 202
    worker = VoiceWorker(sessions=factory,
        provider=FakeReferencePreparationProvider(), settings=get_settings())
    try:
        assert worker.run_once() == "ready"
        with factory() as db:
            assets = list(db.scalars(select(VoiceAsset).where(
                VoiceAsset.version_id == version_id)))
            assert sorted((item.kind, item.state) for item in assets) == [
                ("original", "purge_pending"), ("reference", "available")]
            original = next(item for item in assets if item.kind == "original")
            reference = next(item for item in assets if item.kind == "reference")
            assert reference.sha256 == hashlib.sha256(source).hexdigest()
            assert original.object_key != reference.object_key
        assert worker.run_once() == "purged"
        with factory() as db:
            original = db.scalar(select(VoiceAsset).where(
                VoiceAsset.version_id == version_id, VoiceAsset.kind == "original"))
            version = db.get(VoiceProfileVersion, version_id)
            assert original.state == "purged" and original.absence_checks == 1
            assert version.status == "ready" and version.reference_asset_id is not None
            assert version.model_manifest_json == {
                "provider": "none", "artifact_state": "not_created_l21_3"}
            assert _canonical_counts(db) == canonical_before
    finally:
        worker.close()


def test_worker_reconciles_expired_interrupted_upload(enrollment_api):
    client, factory = enrollment_api
    base = "/api/v1/legacies/501/voice-profile"
    version_id = client.post(base + "/enrollments", headers=_headers(),
        json=_intent()).json()["version_id"]
    storage = VoiceStorage()
    with factory.begin() as db:
        asset, created = VoiceEnrollmentService().reserve_upload(db, 501, 501,
            version_id, sha256=hashlib.sha256(b"uncertain-write").hexdigest(),
            byte_count=len(b"uncertain-write"), mime_type="audio/wav",
            storage=storage, retention_seconds=86400)
        assert created
        asset_id = asset.id
        asset.writer_deadline = utcnow() - timedelta(seconds=1)
    worker = VoiceWorker(sessions=factory,
        provider=FakeReferencePreparationProvider(), settings=get_settings())
    try:
        assert worker.run_once() == "purged"
    finally:
        worker.close()
    with factory() as db:
        version = db.get(VoiceProfileVersion, version_id)
        asset = db.get(VoiceAsset, asset_id)
        assert (version.status, version.safe_failure_code) == (
            "failed", "voice_upload_interrupted")
        assert asset.state == "purged" and asset.absence_checks == 1
