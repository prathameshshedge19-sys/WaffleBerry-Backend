"""L21.5 speech-only rendering for one frozen realtime Legacy answer.

This module has no brain, retrieval, tool, or canonical-effect dependency. It
either retrieves a generation-fenced L21 preserved result or renders the exact
same frozen text through the existing standard TTS provider.
"""

from __future__ import annotations

import asyncio
import anyio
from contextlib import suppress
from dataclasses import dataclass
from datetime import timezone
import hashlib
import io
import time
import wave

from sqlalchemy import select

from app.database import SessionLocal
from app.models.user import User
from app.models.voice_profile import VoiceAsset, VoiceJob
from app.services import realtime_responses as responses
from app.services import realtime_sessions as sessions
from app.services import turn_observability as obs
from app.services.media_storage import StorageError
from app.services.voice import VoiceProviderError, get_voice_provider
from app.services.voice_profiles import AuthorizedSpeechContext, LegacySpeechOrchestrator, VoiceJobService, utcnow
from app.services.voice_storage import VoiceStorage
from app.services.voice_synthesis_manifest import inference_config_digest, test_manifest


class SpeechRenderFailure(RuntimeError):
    pass


class SpeechRenderCancelled(SpeechRenderFailure):
    pass


class PreservedSpeechUnavailable(SpeechRenderFailure):
    pass


@dataclass(frozen=True, repr=False)
class RenderedSpeech:
    pcm_s16le: bytes
    sample_rate: int
    channels: int
    text_digest: str
    delivery: str
    render_ms: int
    preserved_job_id: str | None = None


class LiveSpeechRenderer:
    def __init__(self, session_factory=SessionLocal, storage=None, standard_provider=None):
        self.sessions = session_factory
        self.storage = storage if isinstance(storage, VoiceStorage) else VoiceStorage(storage)
        self.standard_provider = standard_provider
        self.jobs = VoiceJobService()

    @staticmethod
    def _verify_answer(answer, output):
        if (not isinstance(answer, responses.AuthoritativeAnswer)
                or answer.completion_status != "completed"
                or not answer.text or len(answer.text) > 4096
                or hashlib.sha256(answer.text.encode("utf-8")).hexdigest() != answer.text_digest
                or answer.turn_id != output.turn_id or answer.response_generation != output.claim):
            raise SpeechRenderFailure("authoritative_answer_invalid")

    @staticmethod
    def _turn(db, output, owner, settings):
        if output.retired:
            raise sessions.RealtimeError("realtime_access_changed")
        turn = responses.locked_turn(db, output.session_id, owner, output.connection,
            output.turn_id, output.claim, settings)
        if turn.state != "streaming" or turn.mode != "legacy":
            raise sessions.RealtimeError("realtime_access_changed")
        return turn

    def _guard(self, output, owner, settings):
        with self.sessions() as db:
            turn = self._turn(db, output, owner, settings)
            user = db.get(User, turn.actor_user_id)
            if user is None or turn.mode != "legacy":
                raise sessions.RealtimeError("realtime_access_changed")
            value = (turn.legacy_id, turn.actor_user_id, turn.conversation_id,
                user.voice_preference or "marin")
            db.rollback()
            return value

    def _admit(self, output, answer, owner, settings):
        legacy_id, actor_id, conversation_id, voice = self._guard(output, owner, settings)
        if not (settings.voice_live_enabled and settings.voice_cloning_enabled):
            return None, voice
        manifest_digest = settings.voice_synthesis_manifest_digest
        if not manifest_digest and settings.legarya_debug and settings.voice_synthesis_provider == "fake":
            manifest_digest = test_manifest().digest
        if not manifest_digest:
            return None, voice
        with self.sessions() as db:
            turn = self._turn(db, output, owner, settings)
            context = AuthorizedSpeechContext(turn.legacy_id, turn.mode,
                turn.actor_user_id, turn.id, output.connection)
            job = LegacySpeechOrchestrator().admit_synthesis(db, context,
                authoritative_text=answer.text, purpose="live",
                request_key=f"live:{output.session_id}:{turn.id}:{output.claim}",
                model_manifest_digest=manifest_digest,
                inference_config_digest=inference_config_digest(),
                conversation_id=turn.conversation_id,
                realtime_claim_token=output.claim)
            db.commit()
            return (job.id if job is not None else None), voice

    def _snapshot(self, output, answer, owner, settings, job_id):
        with self.sessions() as db:
            turn = self._turn(db, output, owner, settings)
            job = db.get(VoiceJob, job_id)
            if (job is None or job.purpose != "live" or job.realtime_turn_id != output.turn_id
                    or job.realtime_claim_token != output.claim
                    or job.legacy_id != turn.legacy_id or job.conversation_id != turn.conversation_id
                    or job.requested_by_user_id != turn.actor_user_id
                    or job.authoritative_text != answer.text
                    or job.authoritative_text_digest != answer.text_digest):
                raise SpeechRenderCancelled("live_job_stale")
            version = LegacySpeechOrchestrator().resolve_version(db, AuthorizedSpeechContext(
                turn.legacy_id, turn.mode, turn.actor_user_id, turn.id, output.connection))
            if (version is None or job.version_id != version.id
                    or job.operation_generation != version.operation_generation):
                raise PreservedSpeechUnavailable("preserved_speech_stale")
            asset = db.scalar(select(VoiceAsset).where(VoiceAsset.job_id == job.id,
                VoiceAsset.kind == "generated", VoiceAsset.state == "available"))
            if asset is not None and (asset.expires_at is None
                    or asset.expires_at.replace(tzinfo=timezone.utc) <= utcnow()):
                raise PreservedSpeechUnavailable("preserved_speech_expired")
            result = (job.state, asset.id if asset else None)
            db.rollback()
            return result

    def _read_preserved(self, output, answer, owner, settings, job_id, asset_id):
        with self.sessions() as db:
            self._turn(db, output, owner, settings)
            job = db.get(VoiceJob, job_id)
            asset = db.get(VoiceAsset, asset_id)
            expiry = asset.expires_at if asset is not None else None
            if expiry is not None and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if (job is None or job.state != "succeeded" or job.purpose != "live"
                    or job.authoritative_text_digest != answer.text_digest
                    or asset is None or asset.job_id != job.id or asset.state != "available"
                    or asset.mime_type != "audio/wav" or expiry is None or expiry <= utcnow()):
                # No bytes have been admitted to Output yet. A revoked,
                # replaced, expired, or purged clone may therefore use the
                # same-text standard fallback; ensure_current still fails
                # closed after publication begins and never switches voices.
                raise PreservedSpeechUnavailable("live_result_unavailable")
            expected = (asset.byte_count, asset.sha256)
            db.expunge(asset)
            db.rollback()
        try:
            value = self.storage.read_private(asset)
        except StorageError as exc:
            raise PreservedSpeechUnavailable("voice_storage_unavailable") from exc
        if len(value) != expected[0] or hashlib.sha256(value).hexdigest() != expected[1]:
            raise PreservedSpeechUnavailable("voice_storage_verification_failed")
        try:
            with wave.open(io.BytesIO(value), "rb") as source:
                if (source.getnchannels() != 1 or source.getsampwidth() != 2
                        or source.getframerate() != 24000 or source.getcomptype() != "NONE"):
                    raise PreservedSpeechUnavailable("voice_output_format_invalid")
                pcm = source.readframes(source.getnframes())
                if source.readframes(1):
                    raise PreservedSpeechUnavailable("voice_output_format_invalid")
        except (EOFError, wave.Error) as exc:
            raise PreservedSpeechUnavailable("voice_output_format_invalid") from exc
        if not pcm or len(pcm) % 2 or len(pcm) > 24000 * 2 * 120:
            raise PreservedSpeechUnavailable("voice_output_format_invalid")
        self._guard(output, owner, settings)
        return pcm

    def _cancel(self, job_id, output):
        if not job_id:
            return
        with self.sessions.begin() as db:
            self.jobs.cancel_live(db, job_id, turn_id=output.turn_id, claim=output.claim)

    async def _standard(self, output, answer, owner, settings, voice, started):
        await asyncio.to_thread(self._guard, output, owner, settings)
        try:
            provider = self.standard_provider or get_voice_provider()
            pcm = await provider.synthesize_pcm(answer.text, voice)
        except VoiceProviderError as exc:
            raise SpeechRenderFailure(exc.kind) from exc
        await asyncio.to_thread(self._guard, output, owner, settings)
        if (not isinstance(pcm, bytes) or not pcm or len(pcm) % 2
                or len(pcm) > 24000 * 2 * 120
                or hashlib.sha256(answer.text.encode("utf-8")).hexdigest() != answer.text_digest):
            raise SpeechRenderFailure("standard_speech_invalid")
        return RenderedSpeech(pcm, 24000, 1, answer.text_digest, "standard",
            round((time.monotonic() - started) * 1000))

    async def render(self, output, answer, owner, settings):
        self._verify_answer(answer, output)
        started = time.monotonic()
        job_id = None
        admission = asyncio.create_task(asyncio.to_thread(self._admit, output, answer, owner, settings))
        try:
            job_id, voice = await asyncio.shield(admission)
            if job_id is not None:
                output.preserved_job_id = job_id
                output.telemetry("realtime_speech_admitted")
                deadline = time.monotonic() + settings.voice_live_synthesis_timeout_seconds
                try:
                    while time.monotonic() < deadline:
                        state, asset_id = await asyncio.to_thread(self._snapshot,
                            output, answer, owner, settings, job_id)
                        if state == "succeeded" and asset_id:
                            pcm = await asyncio.to_thread(self._read_preserved,
                                output, answer, owner, settings, job_id, asset_id)
                            output.telemetry("realtime_synthesis_ready",
                                duration_ms=(time.monotonic() - started) * 1000)
                            return RenderedSpeech(pcm, 24000, 1, answer.text_digest,
                                "preserved", round((time.monotonic() - started) * 1000), job_id)
                        if state == "succeeded":
                            raise PreservedSpeechUnavailable("preserved_speech_missing")
                        if state in {"failed", "cancelled"}:
                            raise PreservedSpeechUnavailable("preserved_speech_failed")
                        await asyncio.sleep(.25)
                    raise PreservedSpeechUnavailable("preserved_speech_timeout")
                except PreservedSpeechUnavailable:
                    await asyncio.to_thread(self._cancel, job_id, output)
            return await self._standard(output, answer, owner, settings, voice, started)
        except sessions.RealtimeError as exc:
            raise SpeechRenderCancelled("realtime_access_changed") from exc
        except asyncio.CancelledError:
            # Cancelling to_thread does not stop its transaction. Join admission
            # and retain its committed identity before cancelling/purging the job.
            with anyio.CancelScope(shield=True):
                while not admission.done():
                    with suppress(asyncio.CancelledError):
                        await asyncio.shield(admission)
                if not admission.cancelled() and admission.exception() is None:
                    job_id, _ = admission.result()
                    output.preserved_job_id = job_id
                await self._cancel_owned(job_id, output)
            raise

    async def fallback_before_start(self, output, answer, rendered, owner, settings):
        if output.voice_delivery is not None or output.samples or rendered.delivery != "preserved":
            raise SpeechRenderCancelled("speech_already_started")
        self._verify_answer(answer, output)
        await self._cancel_owned(rendered.preserved_job_id, output)
        _, _, _, voice = await asyncio.to_thread(self._guard, output, owner, settings)
        return await self._standard(output, answer, owner, settings, voice, time.monotonic())

    async def ensure_current(self, output, answer, rendered, owner, settings):
        if rendered.delivery == "preserved":
            state, asset_id = await asyncio.to_thread(self._snapshot, output, answer,
                owner, settings, rendered.preserved_job_id)
            if state != "succeeded" or not asset_id:
                raise PreservedSpeechUnavailable("preserved_speech_stale")
        else:
            await asyncio.to_thread(self._guard, output, owner, settings)

    async def cancel_current(self, output):
        """Fence and purge any preserved artifact owned by an interrupted output."""
        await self._cancel_owned(output.preserved_job_id, output)

    async def _cancel_owned(self, job_id, output):
        with anyio.CancelScope(shield=True):
            task = asyncio.create_task(asyncio.to_thread(self._cancel, job_id, output))
            while not task.done():
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(task)
            task.result()


def get_live_speech_renderer():
    return LiveSpeechRenderer()
