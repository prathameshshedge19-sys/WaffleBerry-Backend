"""Separate durable L21.3 reference-preparation and purge worker."""

from __future__ import annotations

import asyncio
import json
import time

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models.voice_profile import VoiceAsset, VoiceJob
from app.services.media_storage import StorageError
from app.services.voice_profiles import StaleVoiceClaim, VoiceJobService
from app.services.voice_providers import VoiceProviderFailure
from app.services.voice_reference import (
    VoicePreparationError, get_reference_preparation_provider,
    sweep_voice_temp_root,
)
from app.services.voice_storage import VoiceStorage
from app.services.voice_worker_lifecycle import WorkerLifecycle
from app.services.voice_observability import emit as voice_event


class VoiceWorker:
    def __init__(self, sessions=SessionLocal, storage=None, *, provider=None,
                 settings=None, purge_only=False):
        self.sessions = sessions
        self.settings = settings or get_settings()
        self.storage = storage if isinstance(storage, VoiceStorage) else VoiceStorage(storage)
        self.purge_only = purge_only
        self.provider = None if purge_only else (provider or get_reference_preparation_provider(self.settings))
        self.jobs = VoiceJobService()
        self.runner = asyncio.Runner()
        self.lifecycle = WorkerLifecycle()

    def claim(self, kind):
        if self.lifecycle.stopping.is_set():
            return None
        with self.sessions.begin() as db:
            job = self.jobs.claim(db, kind,
                lease_seconds=self.settings.voice_worker_lease_seconds)
            return (job.id, job.lease_token) if job else None

    async def _prepare_with_heartbeat(self, job_id, token, **arguments):
        from app.services.voice_runtime_cleanup import runtime_admission
        def current():
            with self.sessions() as db:
                job = db.get(VoiceJob, job_id)
                if job is None or job.state != "running" or job.lease_token != token:
                    raise StaleVoiceClaim()
        with runtime_admission(current):
            task = asyncio.create_task(self.provider.prepare(**arguments))
        interval = max(5, self.settings.voice_worker_lease_seconds // 3)
        try:
            while True:
                done, _pending = await asyncio.wait({task}, timeout=interval)
                if task in done:
                    return await task
                with self.sessions.begin() as db:
                    self.jobs.heartbeat(db, job_id, token,
                        lease_seconds=self.settings.voice_worker_lease_seconds)
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    def _run_prepare(self, job_id, token):
        with self.sessions() as db:
            job = db.get(VoiceJob, job_id)
            original = db.scalar(select(VoiceAsset).where(
                VoiceAsset.legacy_id == job.legacy_id,
                VoiceAsset.version_id == job.version_id,
                VoiceAsset.kind == "original",
                VoiceAsset.state == "available")) if job else None
            if job is None or job.lease_token != token or original is None:
                return "stale"
            identity = (job.legacy_id, job.version_id, job.operation_generation,
                original.id, original.mime_type, original.sha256, original.byte_count)
        try:
            source = self.storage.read_original(original)
            if (len(source) != identity[6]
                    or not self.storage.verify(original)):
                raise VoicePreparationError("voice_original_mismatch")
            result = self.runner.run(self._prepare_with_heartbeat(job_id, token,
                source=source, language="mr", operation_generation=identity[2],
                declared_mime=identity[4]))
            with self.sessions.begin() as db:
                asset = self.jobs.reserve_reference(db, job_id, token, result,
                    self.storage)
                reference_id = asset.id
                reference_key = asset.object_key
                writer_deadline = asset.writer_deadline
            stored = self.storage.put_reference(reference_key, result.reference_audio)
            # Verify the exact bytes under the registered key before READY.
            with self.sessions() as db:
                registered = db.get(VoiceAsset, reference_id)
                if registered is None:
                    raise StaleVoiceClaim()
                registered.object_version = stored.version
                if not self.storage.verify(registered):
                    raise StorageError("storage_verification_failed")
            with self.sessions.begin() as db:
                self.jobs.publish_reference(db, job_id, token, result,
                    reference_asset_id=reference_id, stored=stored,
                    model_manifest={"provider": "none",
                        "artifact_state": "not_created_l21_3"},
                    asr_manifest={"provider": "transformers",
                        "weights_loaded_once": True, "remote_code": False},
                    inference_config={"task": "reference_preparation",
                        "translation": False})
            return "ready"
        except (VoicePreparationError, VoiceProviderFailure) as exc:
            retryable = exc.code in {"voice_media_timeout", "voice_asr_failed",
                "voice_media_tool_unavailable", "voice_asr_model_unavailable", "voice_worker_busy"}
            try:
                with self.sessions.begin() as db:
                    self.jobs.fail(db, job_id, token, exc.code,
                        retryable=retryable)
            except StaleVoiceClaim:
                return "stale"
            return "retry_wait" if retryable else "failed"
        except (StorageError, OSError):
            reference_failed = "reference_id" in locals()
            try:
                with self.sessions.begin() as db:
                    if reference_failed:
                        self.jobs.abandon_reference(db, legacy_id=identity[0],
                            asset_id=reference_id, not_before=writer_deadline)
                    self.jobs.fail(db, job_id, token,
                        "voice_storage_unavailable", retryable=not reference_failed)
            except StaleVoiceClaim:
                return "stale"
            return "failed" if reference_failed else "retry_wait"
        except StaleVoiceClaim:
            if "reference_id" in locals():
                try:
                    with self.sessions.begin() as db:
                        self.jobs.abandon_reference(db, legacy_id=identity[0],
                            asset_id=reference_id, not_before=writer_deadline)
                except Exception:
                    pass
            return "stale"
        except Exception:
            try:
                with self.sessions.begin() as db:
                    self.jobs.fail(db, job_id, token,
                        "voice_preparation_failed", retryable=False)
            except StaleVoiceClaim:
                return "stale"
            return "failed"

    def _run_purge(self, job_id, token):
        with self.sessions() as db:
            job = db.get(VoiceJob, job_id)
            if job is None or job.lease_token != token:
                return "stale"
            asset_id = (job.request_key.split(":", 2)[1]
                if job.request_key.startswith("asset-purge:") else None)
            assets = ([db.get(VoiceAsset, asset_id)] if asset_id else list(db.scalars(
                select(VoiceAsset).where(VoiceAsset.legacy_id == job.legacy_id,
                    VoiceAsset.version_id == job.version_id,
                    VoiceAsset.state != "purged"))))
            assets = [asset for asset in assets if asset is not None]
        try:
            for asset in assets:
                if not self.storage.erase_registered(self.sessions, asset.id):
                    raise StorageError("storage_delete_uncertain")
            with self.sessions.begin() as db:
                if asset_id:
                    self.jobs.publish_asset_purge(db, job_id, token, asset_id)
                else:
                    self.jobs.publish_purge(db, job_id, token)
            return "purged"
        except (StorageError, OSError):
            try:
                with self.sessions.begin() as db:
                    self.jobs.fail(db, job_id, token,
                        "voice_storage_delete_failed", retryable=True)
            except StaleVoiceClaim:
                return "stale"
            return "retry_wait"
        except StaleVoiceClaim:
            return "stale"

    def run_once(self):
        with self.sessions.begin() as db:
            self.jobs.reconcile_expired_original_writes(db)
            self.jobs.reconcile_interrupted_assets(db)
        purge = self.claim("purge")
        if purge:
            return self._run_purge(*purge)
        prepare = None if self.purge_only else self.claim("prepare")
        if prepare:
            return self._run_prepare(*prepare)
        return "idle"

    def close(self):
        self.lifecycle.request_stop()
        self.runner.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Prepare private preserved-voice references")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--purge-only", action="store_true",
        help="Run mandatory cleanup without enrollment, models or feature flags")
    parser.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args()
    if args.poll_seconds < 1:
        parser.error("poll-seconds must be at least 1")
    settings = get_settings()
    if not args.purge_only and not (settings.voice_cloning_enabled and settings.voice_enrollment_enabled):
        parser.error("Voice enrollment is disabled")
    if not args.purge_only:
        sweep_voice_temp_root(settings)
    worker = VoiceWorker(settings=settings, purge_only=args.purge_only)
    print(json.dumps({"event": "voice_worker_ready",
        "provider": worker.provider.provider_name if worker.provider else "purge-only"}), flush=True)
    try:
        with worker.lifecycle.signals():
            worker.lifecycle.notify("READY=1")
            while not worker.lifecycle.stopping.is_set():
                worker.lifecycle.notify("WATCHDOG=1")
                started = time.monotonic()
                try:
                    outcome = worker.run_once()
                except Exception:
                    outcome = "worker_unavailable"
                print(json.dumps({"event": "voice_worker_cycle", "outcome": outcome,
                    "duration_ms": round((time.monotonic() - started) * 1000)}),
                    flush=True)
                if args.once:
                    return
                if outcome in {"idle", "worker_unavailable", "failed", "retry_wait"}:
                    worker.lifecycle.stopping.wait(args.poll_seconds)
    finally:
        worker.close()


if __name__ == "__main__":
    main()
