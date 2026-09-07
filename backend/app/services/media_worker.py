"""Durable L16 job claim/purge foundation.

Extraction is intentionally fenced and reported as deferred until Phase C adds
deterministic parsers and intelligence. No worker path imports canonical memory.
"""

import asyncio
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import exists, or_, select, update

from app.database import SessionLocal
from app.models.media_source import ArtifactState, MediaArtifact, MediaProcessingJob, MediaSource, ProcessingJobKind, ProcessingJobState, SourceState
from app.services.media_sources import utcnow
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
        return self._deferred_extract(job_id, token)

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
        if provider is None:
            from app.services.media_intelligence import get_source_analysis_provider
            provider = get_source_analysis_provider()
        from app.services.media_intelligence import MediaIntelligenceService
        self.intelligence = MediaIntelligenceService(provider, storage=self.storage, transcriber=transcriber)

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
        if kind == ProcessingJobKind.PURGE.value:
            return self._purge(job_id, token)
        return asyncio.run(self.intelligence.process_claim(self.sessions, job_id, token))
