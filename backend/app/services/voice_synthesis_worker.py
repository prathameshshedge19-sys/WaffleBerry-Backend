"""Warm, single-concurrency L21.4 IndicF5 synthesis worker."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import hashlib
import io
import json
import math
import logging
import struct
import time
import wave

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.turn import ConversationTurn
from app.models.user import User
from app.models.voice_profile import (
    VoiceAsset, VoiceConsentReceipt, VoiceJob, VoiceProfile, VoiceProfileVersion,
)
from app.services.authorization import can_view_legacy_as_persona
from app.services.indicf5_provider import IndicF5Provider
from app.services.media_storage import StorageError
from app.services import turn_observability as obs
from app.services.voice_profiles import StaleVoiceClaim, VoiceJobService, aware, utcnow
from app.services.voice_providers import (
    ClonedSpeechRequest, FakeClonedSpeechProvider, VoiceProviderFailure,
    reference_binding_digest, validate_speech, validate_synthesis_request,
)
from app.services.voice_storage import VoiceStorage
from app.services.voice_observability import emit as voice_event
from app.services.voice_worker_lifecycle import WorkerLifecycle
from app.services.voice_synthesis_manifest import (
    inference_config_digest, load_and_verify_manifest, test_manifest,
)


def pcm_wav(pcm: bytes) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(pcm)
    return stream.getvalue()


def _warm_reference() -> bytes:
    pcm = b"".join(struct.pack("<h", round(math.sin(i * 2 * math.pi * 220 / 24000) * 1600))
        for i in range(24000))
    return pcm_wav(pcm)


@dataclass(frozen=True)
class WorkerReadiness:
    provider: str
    device: str
    manifest_digest: str
    warmup_ms: int


class VoiceSynthesisWorker:
    def __init__(self, sessions=SessionLocal, storage=None, *, provider=None,
                 manifest=None, settings=None):
        self.sessions = sessions
        self.settings = settings or get_settings()
        self.storage = storage if isinstance(storage, VoiceStorage) else VoiceStorage(storage)
        if manifest is None:
            manifest = (test_manifest() if self.settings.voice_synthesis_provider == "fake"
                else load_and_verify_manifest(self.settings.voice_synthesis_manifest_path,
                    self.settings.voice_synthesis_artifact_path))
        if self.settings.voice_synthesis_manifest_digest and manifest.digest != self.settings.voice_synthesis_manifest_digest:
            raise VoiceProviderFailure("voice_manifest_digest_mismatch")
        self.manifest = manifest
        self.provider = provider or (FakeClonedSpeechProvider()
            if self.settings.voice_synthesis_provider == "fake"
            else IndicF5Provider(manifest, device=self.settings.voice_synthesis_device))
        self.jobs = VoiceJobService()
        self.runner = asyncio.Runner()
        self.readiness: WorkerReadiness | None = None
        self.lifecycle = WorkerLifecycle()

    async def _synthesize_with_heartbeat(self, job_id, token, request):
        from app.services.voice_runtime_cleanup import runtime_admission
        with runtime_admission(lambda: self._input(job_id, token, reference_required=False)):
            task = asyncio.create_task(self.provider.synthesize(request))
        interval = max(5, self.settings.voice_worker_lease_seconds // 3)
        renewed = time.monotonic()
        deadline = renewed + self.settings.voice_synthesis_job_timeout_seconds
        try:
            while True:
                if self.lifecycle.stopping.is_set():
                    raise VoiceProviderFailure("voice_worker_shutdown")
                if time.monotonic() >= deadline:
                    raise VoiceProviderFailure("voice_synthesis_timeout")
                self.lifecycle.notify("WATCHDOG=1")
                done, _ = await asyncio.wait({task}, timeout=min(interval, .25))
                if task in done:
                    return await task
                # Fresh authorization/lifecycle/claim check while the GPU runs.
                # Session is independent of the controller; no transaction spans
                # an await. Also checks revoke/delete/replacement and turn loss.
                await asyncio.to_thread(self._input, job_id, token, reference_required=False)
                if time.monotonic() - renewed >= interval:
                    with self.sessions.begin() as db:
                        self.jobs.heartbeat(db, job_id, token,
                            lease_seconds=self.settings.voice_worker_lease_seconds)
                    renewed = time.monotonic()
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    def warmup(self):
        self.readiness = None
        started = time.monotonic()
        reference = _warm_reference()
        text = "नमस्कार."
        request = ClonedSpeechRequest(legacy_id=1, profile_version_id="warmup",
            authoritative_text=text,
            authoritative_text_digest=hashlib.sha256(text.encode()).hexdigest(),
            reference_audio=reference,
            reference_audio_digest=hashlib.sha256(reference).hexdigest(),
            reference_transcript=text,
            reference_transcript_digest=hashlib.sha256(text.encode()).hexdigest(),
            reference_binding_digest="0" * 64, language="mr",
            model_manifest_digest=self.manifest.digest, purpose="preview",
            operation_generation=1)
        result = self.runner.run(asyncio.wait_for(
            self.provider.synthesize(request),
            timeout=self.settings.voice_synthesis_warmup_timeout_seconds))
        validate_speech(result, text, 1)
        if self.lifecycle.stopping.is_set():
            raise VoiceProviderFailure("voice_worker_shutdown")
        self.readiness = WorkerReadiness(self.provider.provider_name,
            self.settings.voice_synthesis_device, self.manifest.digest,
            round((time.monotonic() - started) * 1000))
        return self.readiness

    def claim(self):
        if self.lifecycle.stopping.is_set():
            return None
        with self.sessions.begin() as db:
            job = self.jobs.claim(db, "synthesize",
                lease_seconds=self.settings.voice_worker_lease_seconds)
            return (job.id, job.lease_token) if job else None

    def _input(self, job_id, token, *, reference_required=True):
        with self.sessions() as db:
            job = db.get(VoiceJob, job_id)
            version = db.get(VoiceProfileVersion, job.version_id) if job else None
            profile = db.get(VoiceProfile, version.voice_profile_id) if version else None
            reference = db.get(VoiceAsset, version.reference_asset_id) if version else None
            legacy = db.get(Legacy, job.legacy_id) if job else None
            actor = db.get(User, job.requested_by_user_id) if job else None
            consent = db.get(VoiceConsentReceipt, version.consent_receipt_id) if version else None
            if (job is None or job.state != "running" or job.lease_token != token
                    or job.lease_expires_at is None or aware(job.lease_expires_at) <= utcnow()
                    or version is None or version.status != "ready"
                    or version.operation_generation != job.operation_generation
                    or version.legacy_id != job.legacy_id
                    or profile is None or profile.id != job.voice_profile_id
                    or profile.status != "active" or profile.current_version_id != version.id
                    or reference is None or reference.kind != "reference"
                    or reference.legacy_id != job.legacy_id
                    or reference.voice_profile_id != profile.id
                    or reference.version_id != version.id
                    or reference.state != "available" or not job.authoritative_text
                    or legacy is None or legacy.deletion_requested_at is not None or actor is None
                    or actor.deletion_requested_at is not None
                    or consent is None or consent.revoked_at is not None
                    or job.model_manifest_digest != self.manifest.digest
                    or job.inference_config_digest != inference_config_digest()):
                raise StaleVoiceClaim()
            asr = version.asr_manifest_json or {}
            expected_binding = reference_binding_digest(
                audio_digest=version.reference_audio_digest or "",
                transcript_digest=version.reference_transcript_digest or "",
                raw_transcript_digest=asr.get("raw_transcript_sha256", ""),
                language=version.language, asr_model=asr.get("model", ""),
                asr_revision=asr.get("revision", ""),
                recipe_revision=version.preparation_recipe_revision or "")
            if expected_binding != version.binding_digest:
                raise VoiceProviderFailure("voice_reference_mismatch")
            if job.purpose == "preview":
                if legacy.owner_user_id != actor.id or job.conversation_id is not None or job.message_id is not None:
                    raise VoiceProviderFailure("voice_authorization_changed")
            elif job.purpose == "message":
                message = db.get(Message, job.message_id)
                conversation = db.get(Conversation, job.conversation_id)
                if (message is None or conversation is None
                        or message.conversation_id != conversation.id
                        or message.role != MessageRole.ASSISTANT
                        or conversation.user_id != actor.id or conversation.legacy_id != legacy.id
                        or conversation.mode != "legacy"
                        or message.content != job.authoritative_text
                        or not can_view_legacy_as_persona(db, actor.id, legacy)):
                    raise VoiceProviderFailure("voice_authorization_changed")
            elif job.purpose == "live":
                turn = db.get(ConversationTurn, job.realtime_turn_id)
                conversation = db.get(Conversation, job.conversation_id)
                if (turn is None or conversation is None
                        or turn.conversation_id != conversation.id
                        or turn.legacy_id != legacy.id or turn.actor_user_id != actor.id
                        or turn.input_mode != "realtime_voice" or turn.mode != "legacy"
                        or turn.state != "streaming"
                        or turn.claim_token != job.realtime_claim_token
                        or conversation.user_id != actor.id
                        or conversation.legacy_id != legacy.id
                        or conversation.mode != "legacy"
                        or not can_view_legacy_as_persona(db, actor.id, legacy)):
                    raise VoiceProviderFailure("voice_authorization_changed")
            else:
                raise VoiceProviderFailure("voice_authorization_changed")
            if not reference_required:
                return None
            identity = (reference.sha256, reference.byte_count, reference.object_key,
                reference.object_version)
            audio = self.storage.read_private(reference)
            if (len(audio) != identity[1] or hashlib.sha256(audio).hexdigest() != identity[0]
                    or not self.storage.verify(reference)):
                raise VoiceProviderFailure("voice_reference_mismatch")
            request = ClonedSpeechRequest(legacy_id=job.legacy_id,
                profile_version_id=version.id,
                authoritative_text=job.authoritative_text,
                authoritative_text_digest=job.authoritative_text_digest,
                reference_audio=audio, reference_audio_digest=version.reference_audio_digest,
                reference_transcript=version.reference_transcript,
                reference_transcript_digest=version.reference_transcript_digest,
                reference_binding_digest=version.binding_digest, language="mr",
                model_manifest_digest=job.model_manifest_digest, purpose=job.purpose,
                operation_generation=job.operation_generation)
            validate_synthesis_request(request)
            return request

    def run_job(self, job_id, token):
        started = time.monotonic()
        timings = {}
        try:
            request = self._input(job_id, token)
            timings["voice_input_ms"] = (time.monotonic() - started) * 1000
            inference_started = time.monotonic()
            result = self.runner.run(self._synthesize_with_heartbeat(job_id, token, request))
            timings["voice_inference_ms"] = (time.monotonic() - inference_started) * 1000
            storage_started = time.monotonic()
            validate_speech(result, request.authoritative_text, request.operation_generation)
            if len(result.pcm_s16le) > self.settings.voice_synthesis_max_seconds * 48000:
                raise VoiceProviderFailure("voice_synthesis_output_too_large")
            wav = pcm_wav(result.pcm_s16le)
            if len(wav) > self.settings.voice_synthesis_max_bytes:
                raise VoiceProviderFailure("voice_synthesis_output_too_large")
            with self.sessions.begin() as db:
                asset = self.jobs.reserve_generated(db, job_id, token, result, self.storage,
                    retention_seconds=self.settings.voice_generated_retention_seconds)
                asset_id, key, deadline = asset.id, asset.object_key, asset.writer_deadline
            stored = self.storage.put_generated(key, wav)
            digest = hashlib.sha256(wav).hexdigest()
            with self.sessions() as db:
                registered = db.get(VoiceAsset, asset_id)
                registered.object_version = stored.version
                registered.sha256 = digest
                if not self.storage.verify(registered):
                    raise StorageError("storage_verification_failed")
            with self.sessions.begin() as db:
                self.jobs.publish_generated(db, job_id, token, result,
                    asset_id=asset_id, stored=stored, wav_digest=digest)
            voice_event("job_completed")
            timings["voice_storage_ms"] = (time.monotonic() - storage_started) * 1000
            return "ready"
        except StaleVoiceClaim:
            voice_event("stale_publication_blocked", reason="stale")
            if "asset_id" in locals():
                with self.sessions.begin() as db:
                    self.jobs.schedule_asset_purge(db, legacy_id=request.legacy_id,
                        asset_id=asset_id, not_before=deadline)
            return "stale"
        except (VoiceProviderFailure, StorageError, OSError) as exc:
            voice_event("job_failed", reason="provider")
            code = getattr(exc, "code", "voice_synthesis_failed")
            try:
                with self.sessions.begin() as db:
                    self.jobs.fail(db, job_id, token, code,
                        retryable=code in {"voice_storage_unavailable", "voice_worker_busy"})
                    if "asset_id" in locals():
                        self.jobs.schedule_asset_purge(db, legacy_id=request.legacy_id,
                            asset_id=asset_id, not_before=deadline)
            except StaleVoiceClaim:
                return "stale"
            return "retry_wait" if code in {"voice_storage_unavailable", "voice_worker_busy"} else "failed"
        except RuntimeError as exc:
            voice_event("job_failed", reason="provider")
            code = "voice_gpu_out_of_memory" if "out of memory" in str(exc).lower() else "voice_synthesis_failed"
            try:
                with self.sessions.begin() as db:
                    self.jobs.fail(db, job_id, token, code, retryable=False)
            except StaleVoiceClaim:
                return "stale"
            return "failed"
        finally:
            timings["voice_cycle_ms"] = (time.monotonic() - started) * 1000
            obs.emit("voice_worker_stage", values=timings)

    def run_once(self):
        if self.readiness is None:
            raise RuntimeError("voice_worker_not_ready")
        with self.sessions.begin() as db:
            self.jobs.reconcile_interrupted_assets(db)
        started = time.monotonic()
        claim = self.claim()
        obs.emit("voice_worker_stage", values={"voice_claim_ms": (time.monotonic() - started) * 1000})
        return self.run_job(*claim) if claim else "idle"

    def close(self):
        self.readiness = None
        self.lifecycle.request_stop()
        self.runner.close()


def main():
    parser = argparse.ArgumentParser(description="Warm private IndicF5 synthesis worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args()
    if args.poll_seconds < 0.25:
        parser.error("poll-seconds must be at least 0.25")
    settings = get_settings()
    if not (settings.voice_cloning_enabled
            and (settings.voice_message_playback_enabled or settings.voice_live_enabled)):
        parser.error("Preserved voice playback is disabled")
    logging.basicConfig(level=logging.INFO)
    voice_event("worker_starting")
    worker = None
    try:
        worker = VoiceSynthesisWorker(settings=settings)
        with worker.lifecycle.signals():
            ready = worker.warmup()
            worker.lifecycle.notify("READY=1")
            voice_event("worker_ready")
            print(json.dumps({"event": "voice_synthesis_worker_ready",
                "provider": ready.provider, "device": ready.device,
                "manifest_digest": ready.manifest_digest,
                "warmup_ms": ready.warmup_ms}), flush=True)
            while not worker.lifecycle.stopping.is_set():
                worker.lifecycle.notify("WATCHDOG=1")
                outcome = worker.run_once()
                print(json.dumps({"event": "voice_synthesis_worker_cycle",
                    "outcome": outcome}), flush=True)
                if args.once:
                    return
                if outcome != "ready":
                    worker.lifecycle.stopping.wait(args.poll_seconds)
    except Exception:
        # Never print arbitrary dependency exceptions, DSNs, paths or payloads.
        voice_event("worker_not_ready", reason="unavailable")
        if worker is None:
            voice_event("manifest_failure", reason="manifest")
        raise SystemExit(1) from None
    finally:
        voice_event("worker_stopping", reason="shutdown")
        if worker is not None:
            worker.close()


if __name__ == "__main__":
    main()
