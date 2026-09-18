"""Final L21 overload, supervision, crash and privacy acceptance (no benchmarks)."""
import asyncio
from datetime import timedelta
import json
import logging
import threading

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.voice_profile import VoiceAsset, VoiceJob, VoiceProfile
from app.services import voice_limits
from app.services.media_storage import StorageError
from app.services.voice_observability import emit
from app.services.voice_profiles import (
    StaleVoiceClaim, VoiceJobService, request_account_voice_purge, utcnow,
)
from app.services.voice_providers import FakeClonedSpeechProvider, VoiceProviderFailure
from app.services.voice_synthesis_worker import VoiceSynthesisWorker, pcm_wav
from app.services.voice_worker import VoiceWorker
from tests.test_realtime_l15 import live
from tests.test_realtime_speech_audit_l21 import speech_world
from tests.voice_l21_helpers import reserve


def admit(w):
    return w.renderer._admit(w.output, w.answer, "worker", w.live[3])[0]


@pytest.mark.parametrize("limit", ["MAX_PENDING_SYNTHESIS", "MAX_PENDING_PER_LEGACY",
    "MAX_SYNTHESIS_RECORDS", "MAX_SYNTHESIS_RECORDS_PER_LEGACY"])
def test_full_queue_falls_back_with_same_frozen_answer_no_job_growth(speech_world, monkeypatch, limit):
    w = speech_world
    monkeypatch.setattr(voice_limits, limit, 0)
    with w.live[1]() as db:
        before = db.scalar(select(func.count()).select_from(VoiceJob))
    rendered = asyncio.run(w.renderer.render(w.output, w.answer, "worker", w.live[3]))
    assert rendered.delivery == "standard"
    assert w.standard.calls[0][0] == w.answer.text
    with w.live[1]() as db:
        assert db.scalar(select(func.count()).select_from(VoiceJob)) == before


def test_duplicate_job_reused_when_queue_full_and_enrollment_cap_creates_nothing(speech_world, monkeypatch):
    w = speech_world
    identity = admit(w)
    monkeypatch.setattr(voice_limits, "MAX_PENDING_PER_LEGACY", 1)
    assert admit(w) == identity
    monkeypatch.setattr(voice_limits, "MAX_ENROLLMENTS_PER_LEGACY", 1)
    with w.live[1]() as db:
        profile = db.get(VoiceProfile, w.version.voice_profile_id)
        revision = profile.revision
        with pytest.raises(HTTPException) as error:
            reserve(db, revision=revision)
        assert error.value.status_code == 429 and profile.revision == revision


def test_capacity_api_preview_rejects_and_message_returns_standard_same_text(speech_world, monkeypatch):
    from app.models.conversation import Message, MessageRole
    from tests.test_voice_synthesis_l21 import auth
    w = speech_world
    monkeypatch.setattr(w.live[3], "voice_message_playback_enabled", True)
    monkeypatch.setattr(voice_limits, "MAX_PENDING_SYNTHESIS", 0)
    client = w.live[0]
    preview = client.post("/api/v1/legacies/1/voice-profile/preview", headers=auth(1))
    assert preview.status_code == 503
    assert preview.json()["detail"]["code"] == "voice_capacity_exceeded"
    conversation_id = w.output.brain.prepared.actor.conversation_id
    with w.live[1].begin() as db:
        message = Message(conversation_id=conversation_id, role=MessageRole.ASSISTANT, content=w.answer.text)
        db.add(message)
        db.flush()
        identity = message.id
    response = client.post(f"/api/v1/legacy-conversations/{conversation_id}/messages/{identity}/speech",
        headers=auth(3))
    assert response.status_code == 200
    assert response.headers["x-voice-delivery"] == "standard_fallback"
    with w.live[1]() as db:
        assert db.scalar(select(func.count()).select_from(VoiceJob).where(VoiceJob.kind == "synthesize")) == 0


def test_worker_restart_takes_expired_lease_and_old_completion_cannot_publish(speech_world):
    w = speech_world
    identity = admit(w)
    old = w.worker.claim()
    request = w.worker._input(*old)
    result = asyncio.run(FakeClonedSpeechProvider().synthesize(request))
    with w.live[1].begin() as db:
        db.get(VoiceJob, identity).lease_expires_at = utcnow() - timedelta(seconds=1)
    restarted = VoiceSynthesisWorker(w.live[1], w.worker.storage, settings=w.live[3],
        manifest=w.worker.manifest, provider=FakeClonedSpeechProvider())
    try:
        with pytest.raises(RuntimeError, match="not_ready"):
            restarted.run_once()
        restarted.warmup()
        assert restarted.run_once() == "ready"
        with w.live[1].begin() as db:
            with pytest.raises(StaleVoiceClaim):
                VoiceJobService().reserve_generated(db, *old, result, w.worker.storage)
        with w.live[1]() as db:
            assert db.get(VoiceJob, identity).attempts == 2
            assert db.get(VoiceProfile, w.version.voice_profile_id).status == "active"
    finally:
        restarted.close()
    assert restarted.readiness is None


def test_repeated_worker_death_terminates_attempts_instead_of_permanent_running(speech_world):
    w = speech_world
    identity = admit(w)
    for attempt in range(3):
        assert w.worker.claim()[0] == identity
        with w.live[1].begin() as db:
            job = db.get(VoiceJob, identity)
            assert job.attempts == attempt + 1
            job.lease_expires_at = utcnow() - timedelta(seconds=1)
    assert w.worker.claim() is None
    with w.live[1]() as db:
        job = db.get(VoiceJob, identity)
        assert job.state == "failed" and job.attempts == 3 and job.lease_token is None


def test_crash_after_private_write_is_fenced_and_purged_with_all_flags_off(speech_world):
    w = speech_world
    identity = admit(w)
    claim = w.worker.claim()
    result = asyncio.run(w.worker.provider.synthesize(w.worker._input(*claim)))
    with w.live[1].begin() as db:
        asset = w.worker.jobs.reserve_generated(db, *claim, result, w.worker.storage)
        asset_id, key = asset.id, asset.object_key
    w.worker.storage.put_generated(key, pcm_wav(result.pcm_s16le))
    with w.live[1].begin() as db:
        db.get(VoiceAsset, asset_id).writer_deadline = utcnow() - timedelta(seconds=1)
    settings = w.live[3].model_copy(update={key: False for key in (
        "voice_cloning_enabled", "voice_enrollment_enabled", "voice_message_playback_enabled", "voice_live_enabled")})
    cleaner = VoiceWorker(w.live[1], w.worker.storage, settings=settings, purge_only=True)
    try:
        assert cleaner.provider is None and cleaner.run_once() == "purged"
        assert cleaner.run_once() == "idle"
        with w.live[1]() as db:
            assert db.get(VoiceJob, identity).state == "failed"
            asset = db.get(VoiceAsset, asset_id)
            assert asset.state == "purged" and asset.absence_checks > 0
            assert db.get(VoiceProfile, w.version.voice_profile_id).status == "active"
        with w.live[1].begin() as db:
            with pytest.raises(StaleVoiceClaim):
                w.worker.jobs.reserve_generated(db, *claim, result, w.worker.storage)
    finally:
        cleaner.close()


@pytest.mark.parametrize("failure", ["read", "write", "malformed", "oom"])
def test_bounded_provider_storage_failures_never_publish_partial_audio(speech_world, monkeypatch, failure):
    w = speech_world
    identity = admit(w)
    def unavailable(*_):
        raise StorageError("storage_unavailable")
    if failure == "read":
        monkeypatch.setattr(w.worker.storage, "read_private", unavailable)
    elif failure == "write":
        monkeypatch.setattr(w.worker.storage, "put_generated", unavailable)
    elif failure == "malformed":
        w.worker.provider = FakeClonedSpeechProvider("malformed")
    else:
        class OOM(FakeClonedSpeechProvider):
            async def synthesize(self, request):
                raise VoiceProviderFailure("voice_gpu_out_of_memory")
        w.worker.provider = OOM()
    assert w.worker.run_once() in {"failed", "retry_wait"}
    with w.live[1]() as db:
        assert db.scalar(select(func.count()).select_from(VoiceAsset).where(
            VoiceAsset.job_id == identity, VoiceAsset.state == "available")) == 0
        assert db.get(VoiceProfile, w.version.voice_profile_id).status == "active"


def test_shutdown_cancels_and_joins_provider_before_closing_worker(speech_world):
    w = speech_world
    entered, exited = threading.Event(), threading.Event()
    class Slow(FakeClonedSpeechProvider):
        async def synthesize(self, request):
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                exited.set()
    identity = admit(w)
    w.worker.provider = Slow()
    async def run():
        running = asyncio.create_task(asyncio.to_thread(w.worker.run_once))
        assert await asyncio.to_thread(entered.wait, 3)
        w.worker.lifecycle.request_stop()
        assert await asyncio.wait_for(running, 3) == "failed"
        assert exited.is_set()
    asyncio.run(run())
    assert w.worker.claim() is None
    with w.live[1]() as db:
        assert db.get(VoiceJob, identity).safe_error_code == "voice_worker_shutdown"


def test_purge_storage_uncertainty_survives_restart_and_retries_without_ceiling(speech_world, monkeypatch):
    w = speech_world
    admit(w)
    assert w.worker.run_once() == "ready"
    with w.live[1].begin() as db:
        request_account_voice_purge(db, 1)
        for job in db.scalars(select(VoiceJob).where(VoiceJob.kind == "purge")):
            job.attempts = 1000000  # durable purge must not compute enormous powers
        for asset in db.scalars(select(VoiceAsset)):
            asset.writer_deadline = None
    real_erase = w.worker.storage.erase_registered
    monkeypatch.setattr(w.worker.storage, "erase_registered", lambda *_: False)
    first = VoiceWorker(w.live[1], w.worker.storage, settings=w.live[3], purge_only=True)
    assert first.run_once() == "retry_wait"
    first.close()
    with w.live[1].begin() as db:
        assert not list(db.scalars(select(VoiceAsset).where(VoiceAsset.state == "purged")))
        for job in db.scalars(select(VoiceJob).where(VoiceJob.kind == "purge")):
            job.next_attempt_at = utcnow() - timedelta(seconds=1)
    monkeypatch.setattr(w.worker.storage, "erase_registered", real_erase)
    restarted = VoiceWorker(w.live[1], w.worker.storage, settings=w.live[3], purge_only=True)
    try:
        for _ in range(10):
            if restarted.run_once() == "idle":
                break
        with w.live[1]() as db:
            assert all(asset.state == "purged" for asset in db.scalars(select(VoiceAsset)))
            assert db.get(VoiceProfile, w.version.voice_profile_id).status == "deleted"
            assert all(job.authoritative_text == "[purged]" for job in db.scalars(
                select(VoiceJob).where(VoiceJob.kind == "synthesize")))
    finally:
        restarted.close()


def test_safe_observability_rejects_private_dynamic_fields(caplog):
    with caplog.at_level(logging.INFO, logger="legarya.voice"):
        emit("worker_ready", reason="arbitrary-private-text")
        emit("arbitrary-private-text")
        emit("admission_rejected", reason="capacity")
    records = [json.loads(item.message) for item in caplog.records if item.name == "legarya.voice"]
    assert records == [{"event": "voice_worker_ready"},
        {"event": "voice_admission_rejected", "reason": "capacity"}]


def test_failed_warmup_never_retains_ready_state(speech_world):
    w = speech_world
    w.worker.provider = FakeClonedSpeechProvider("malformed")
    with pytest.raises(VoiceProviderFailure):
        w.worker.warmup()
    assert w.worker.readiness is None
    with pytest.raises(RuntimeError, match="not_ready"):
        w.worker.run_once()


def test_unix_service_notifications_and_signal_handlers_are_safe(monkeypatch):
    from app.services.voice_worker_lifecycle import WorkerLifecycle
    import signal
    import socket
    from types import SimpleNamespace
    sent = []
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def settimeout(self, value): assert value == 1
        def sendto(self, data, address): sent.append((data, address))
    monkeypatch.setenv("NOTIFY_SOCKET", "@synthetic-l21")
    monkeypatch.setattr(socket, "socket", lambda *_: Socket())
    monkeypatch.setattr(socket, "AF_UNIX", 1, raising=False)
    previous = signal.getsignal(signal.SIGTERM)
    lifecycle = WorkerLifecycle()
    with lifecycle.signals():
        lifecycle.notify("READY=1")
        lifecycle.notify("WATCHDOG=1")
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert lifecycle.stopping.is_set()
    assert signal.getsignal(signal.SIGTERM) == previous
    assert [data for data, _ in sent] == [b"READY=1", b"WATCHDOG=1", b"STOPPING=1"]
    assert all(address.startswith("\0") for _, address in sent)
