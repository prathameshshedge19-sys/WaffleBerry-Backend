"""Durable L16 job claim/purge foundation.

Extraction is intentionally fenced and reported as deferred until Phase C adds
deterministic parsers and intelligence. No worker path imports canonical memory.
"""

import asyncio
import hashlib
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import exists, or_, select, update

from app.database import SessionLocal
from app.models.media_source import ArtifactKind, ArtifactState, MediaArtifact, MediaProcessingJob, MediaSource, ProcessingJobKind, ProcessingJobState, SourceState
from app.services.media_sources import _aware, utcnow
from app.services.media_storage import SourceStorage, StorageError, get_source_storage


class MediaWorker:
    def __init__(self, sessions=SessionLocal, storage: SourceStorage | None = None, *, lease_seconds: int = 120):
        self.sessions = sessions
        self.storage = storage or get_source_storage()
        self.lease_seconds = lease_seconds

    def claim(self):
        now = utcnow()
        token = str(uuid4())
        with self.sessions.begin() as db:
            # Lock the source before its jobs. Deletion uses the same order,
            # so a claim cannot race a generation bump or deadlock on the job.
            source = db.scalar(select(MediaSource).where(
                MediaSource.state != SourceState.DELETED.value,
                exists(select(MediaProcessingJob.id).where(
                    MediaProcessingJob.source_id == MediaSource.id,
                    MediaProcessingJob.legacy_id == MediaSource.legacy_id,
                    MediaProcessingJob.state.in_(("queued", "retry_wait", "running")),
                    or_(MediaProcessingJob.next_attempt_at.is_(None), MediaProcessingJob.next_attempt_at <= now),
                    or_(MediaProcessingJob.lease_token.is_(None), MediaProcessingJob.lease_expires_at <= now),
                )),
            ).order_by(MediaSource.created_at, MediaSource.id).with_for_update().limit(1))
            if source is None:
                return None
            job = db.scalar(select(MediaProcessingJob).where(
                MediaProcessingJob.source_id == source.id,
                MediaProcessingJob.legacy_id == source.legacy_id,
                MediaProcessingJob.state.in_(("queued", "retry_wait", "running")),
                or_(MediaProcessingJob.next_attempt_at.is_(None), MediaProcessingJob.next_attempt_at <= now),
                or_(MediaProcessingJob.lease_token.is_(None), MediaProcessingJob.lease_expires_at <= now),
            ).order_by(MediaProcessingJob.created_at, MediaProcessingJob.id).with_for_update().limit(1))
            if job is None:
                return None
            result = db.execute(update(MediaProcessingJob).where(
                MediaProcessingJob.id == job.id,
                MediaProcessingJob.state.in_(("queued", "retry_wait", "running")),
            ).values(state="running", attempts=MediaProcessingJob.attempts + 1, lease_token=token,
                     lease_expires_at=now + timedelta(seconds=self.lease_seconds), started_at=job.started_at or now))
            if result.rowcount != 1:
                return None
            if job.kind == ProcessingJobKind.EXTRACT.value:
                db.execute(update(MediaSource).where(MediaSource.id == job.source_id, MediaSource.generation == job.generation, MediaSource.state == SourceState.QUEUED.value).values(state=SourceState.PROCESSING.value, processing_started_at=now))
            return job.id, token

    def run_once(self):
        claim = self.claim()
        if claim is None:
            return "idle"
        job_id, token = claim
        with self.sessions() as db:
            job = db.get(MediaProcessingJob, job_id)
            if job is None or job.lease_token != token:
                return "stale"
        if job.kind == ProcessingJobKind.PURGE.value:
            return self._purge(job_id, token)
        with self.sessions() as db:
            source = db.get(MediaSource, job.source_id)
            visual_reference = source is not None and source.processing_purpose == "visual_reference"
        if visual_reference:
            return self._validate_visual_reference(job_id, token)
        return self._deferred_extract(job_id, token)

    def _validate_visual_reference(self, job_id, token):
        from app.services.visual_reference import MAX_BYTES, VisualReferenceError, validate_visual_reference
        with self.sessions() as db:
            job = db.get(MediaProcessingJob, job_id)
            source = db.scalar(select(MediaSource).where(MediaSource.id == job.source_id, MediaSource.legacy_id == job.legacy_id)) if job else None
            if not self._visual_claim_current(source, job, token):
                return "stale"
            artifact = db.scalar(select(MediaArtifact).where(
                MediaArtifact.source_id == source.id, MediaArtifact.legacy_id == source.legacy_id,
                MediaArtifact.generation == job.generation, MediaArtifact.kind == ArtifactKind.ORIGINAL.value,
                MediaArtifact.state == ArtifactState.AVAILABLE.value,
            ))
            if source.state == "uploading":
                # Reservation creates the durable job before any bytes arrive.
                db.rollback()
                with self.sessions.begin() as pending:
                    pending.execute(update(MediaProcessingJob).where(MediaProcessingJob.id == job_id, MediaProcessingJob.lease_token == token).values(
                        state="retry_wait", lease_token=None, lease_expires_at=None,
                        next_attempt_at=utcnow() + timedelta(seconds=30)))
                return "awaiting_upload"
        metadata = None
        code = None
        try:
            if source.kind != "image" or artifact is None:
                raise VisualReferenceError("visual_original_unavailable")
            from app.services.visual_storage import VisualStorage
            data = VisualStorage(self.storage).read_original(artifact.object_key, artifact.object_version)
            if len(data) > MAX_BYTES:
                raise VisualReferenceError("visual_image_too_large")
            digest = hashlib.sha256(data).hexdigest()
            if len(data) != source.size_bytes or len(data) != artifact.byte_size or digest != source.sha256 or digest != artifact.sha256:
                raise VisualReferenceError("visual_original_mismatch")
            metadata = validate_visual_reference(data, expected_mime_type=source.detected_mime_type or source.declared_mime_type)
        except VisualReferenceError as exc:
            code = exc.code
        except (StorageError, OSError):
            code = "source_storage_read_failed"
        with self.sessions.begin() as db:
            # Never publish validation after deletion, lease loss, retry or expiry.
            current = db.scalar(select(MediaSource).where(MediaSource.id == source.id, MediaSource.legacy_id == source.legacy_id).with_for_update())
            job = db.scalar(select(MediaProcessingJob).where(MediaProcessingJob.id == job_id).with_for_update())
            if not self._visual_claim_current(current, job, token):
                return "stale"
            now = utcnow()
            job.state = "failed" if code else "succeeded"
            job.stage = "visual_validation_failed" if code else "visual_reference_validated"
            job.finished_at = now
            job.last_error_code = code
            job.lease_token = job.lease_expires_at = None
            current.state = "failed" if code else "ready"
            current.last_error_code = code
            current.processing_finished_at = now
            current.updated_at = now
            # Original admission's signature check is not full decoder safety.
            current.safety_state = "rejected" if code else "clean"
            if metadata:
                current.metadata_json = {"visual_reference": metadata}
            return "failed" if code else "visual_reference_validated"

    @staticmethod
    def _visual_claim_current(source, job, token):
        return (source is not None and job is not None and source.processing_purpose == "visual_reference"
                and job.kind == "extract" and job.state == "running" and job.lease_token == token
                and job.lease_expires_at is not None and _aware(job.lease_expires_at) > utcnow()
                and source.generation == job.generation and source.state not in {"deleting", "deleted"})

    def _deferred_extract(self, job_id, token):
        now = utcnow()
        with self.sessions.begin() as db:
            job = db.get(MediaProcessingJob, job_id)
            if job is None or job.lease_token != token:
                return "stale"
            source = db.scalar(select(MediaSource).where(MediaSource.id == job.source_id, MediaSource.legacy_id == job.legacy_id))
            if source is None or source.state == SourceState.DELETING.value or source.generation != job.generation:
                job.state = ProcessingJobState.CANCELLED.value; job.last_error_code = "source_deleted"; job.finished_at = now; job.lease_token = job.lease_expires_at = None
                return "cancelled"
            job.state = ProcessingJobState.FAILED.value; job.stage = "awaiting_phase_c"; job.last_error_code = "extraction_deferred_phase_c"; job.finished_at = now; job.lease_token = job.lease_expires_at = None
            source.state = SourceState.FAILED.value; source.processing_finished_at = now; source.last_error_code = job.last_error_code
            return "deferred"

    def _purge(self, job_id, token):
        with self.sessions() as db:
            job = db.get(MediaProcessingJob, job_id)
            if job is None or job.lease_token != token:
                return "stale"
            artifacts = list(db.scalars(select(MediaArtifact).where(MediaArtifact.source_id == job.source_id, MediaArtifact.legacy_id == job.legacy_id, MediaArtifact.state != ArtifactState.PURGED.value)).all())
        try:
            for artifact in artifacts:
                self.storage.delete(artifact.object_key, version=artifact.object_version)
        except StorageError:
            with self.sessions.begin() as db:
                db.execute(update(MediaProcessingJob).where(MediaProcessingJob.id == job_id, MediaProcessingJob.lease_token == token).values(state=ProcessingJobState.RETRY_WAIT.value, next_attempt_at=utcnow() + timedelta(seconds=30), lease_token=None, lease_expires_at=None, last_error_code="storage_delete_failed"))
            return "retry_wait"
        now = utcnow()
        with self.sessions.begin() as db:
            job = db.get(MediaProcessingJob, job_id)
            source = db.scalar(select(MediaSource).where(MediaSource.id == job.source_id, MediaSource.legacy_id == job.legacy_id)) if job else None
            if job is None or job.lease_token != token or source is None or source.state != SourceState.DELETING.value or source.generation != job.generation:
                return "stale"
            db.execute(update(MediaArtifact).where(MediaArtifact.source_id == source.id, MediaArtifact.legacy_id == source.legacy_id).values(state=ArtifactState.PURGED.value, purged_at=now))
            job.state = ProcessingJobState.SUCCEEDED.value; job.stage = "purged"; job.finished_at = now; job.lease_token = job.lease_expires_at = None
            source.state = SourceState.DELETED.value; source.purged_at = now; source.processing_finished_at = source.processing_finished_at or now
            return "purged"


class MediaIntelligenceWorker(MediaWorker):
    """Phase C worker facade; extraction remains separate from chat/realtime."""

    def __init__(self, sessions=SessionLocal, storage: SourceStorage | None = None, *, provider=None, transcriber=None, lease_seconds: int = 120):
        super().__init__(sessions=sessions, storage=storage, lease_seconds=lease_seconds)
        self._provider = provider
        self._transcriber = transcriber
        self.intelligence = None
        self._runner = asyncio.Runner()

    def _normal_intelligence(self):
        if self.intelligence is not None:
            return self.intelligence
        provider = self._provider
        if provider is None:
            from app.services.media_intelligence import get_source_analysis_provider
            provider = get_source_analysis_provider()
        from app.services.media_intelligence import MediaIntelligenceService
        self.intelligence = MediaIntelligenceService(provider, storage=self.storage, transcriber=self._transcriber)
        # The provider owns pooled async connections. Keep their event loop
        # alive across jobs instead of closing it after every claim.
        return self.intelligence

    def run_once(self):
        claim = self.claim()
        if claim is None:
            return "idle"
        job_id, token = claim
        with self.sessions() as db:
            job = db.get(MediaProcessingJob, job_id)
            if job is None or job.lease_token != token:
                return "stale"
            kind = job.kind
            source = db.scalar(select(MediaSource).where(MediaSource.id == job.source_id, MediaSource.legacy_id == job.legacy_id))
            purpose = source.processing_purpose if source else None
        if kind == ProcessingJobKind.PURGE.value:
            return self._purge(job_id, token)
        if purpose == "visual_reference":
            return self._validate_visual_reference(job_id, token)
        if purpose != "source_review":
            return "purpose_rejected"
        return self._runner.run(self._normal_intelligence().process_claim(self.sessions, job_id, token))

    def close(self):
        try:
            provider = self.intelligence.provider if self.intelligence is not None else self._provider
            client = getattr(provider, "client", None)
            if client is not None:
                self._runner.run(client.close())
        finally:
            self._runner.close()


def main():
    """Separate durable worker entry point; never started by a web request."""
    import argparse
    import json
    import time
    from app.config import get_settings
    parser = argparse.ArgumentParser(description="Process private L16 sources and pending erasure")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args()
    if args.poll_seconds < 1:
        parser.error("poll-seconds must be at least 1")
    if not get_settings().media_enabled:
        parser.error("Media processing is disabled")
    worker = MediaIntelligenceWorker()
    try:
        while True:
            started = time.monotonic()
            try:
                outcome = worker.run_once()
                from app.services.legacy_deletion import finalize_one
                cleanup = finalize_one(worker.sessions, worker.storage)
                if cleanup == "legacy_erased":
                    outcome = cleanup
            except Exception:
                # Never emit parser/provider/storage exception bodies or contents.
                outcome = "worker_unavailable"
            print(json.dumps({"event": "media_worker_cycle", "outcome": outcome, "duration_ms": round((time.monotonic() - started) * 1000)}), flush=True)
            if args.once:
                return
            if outcome in {"idle", "worker_unavailable", "failed", "retry_wait"}:
                time.sleep(args.poll_seconds)
    finally:
        try:
            worker.close()
        except Exception:
            print(json.dumps({"event": "media_worker_shutdown", "outcome": "close_failed"}), flush=True)


if __name__ == "__main__":
    main()
