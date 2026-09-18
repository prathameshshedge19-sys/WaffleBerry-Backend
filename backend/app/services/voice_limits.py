"""Conservative admission ceilings, not a GPU throughput/scheduling policy.

Purge is never capacity-limited. Historical ceilings deliberately fail closed
instead of deleting consent, job or absence-proof evidence to make room.
"""
from sqlalchemy import func, select

from app.models.voice_profile import VoiceJob, VoiceProfileVersion

MAX_PENDING_SYNTHESIS = 64
MAX_PENDING_PER_LEGACY = 4
MAX_SYNTHESIS_RECORDS = 10000
MAX_SYNTHESIS_RECORDS_PER_LEGACY = 1000
MAX_ENROLLMENTS = 10000
MAX_ENROLLMENTS_PER_LEGACY = 32
_ADMISSION_LOCK = 2100621


def capacity_available(db, scope, kind):
    # Non-blocking global transaction lock avoids lock-order inversion with
    # Legacy-first commands. PostgreSQL is mandatory in production. SQLite is
    # only the single-writer debug adapter; PostgreSQL races have separate tests.
    if db.bind.dialect.name == "postgresql":
        if not db.scalar(select(func.pg_try_advisory_xact_lock(_ADMISSION_LOCK))):
            return False
    db.flush()
    if kind == "enrollment":
        return (len(scope.versions) < MAX_ENROLLMENTS_PER_LEGACY
            and db.scalar(select(func.count()).select_from(VoiceProfileVersion)) < MAX_ENROLLMENTS)
    jobs = [job for job in scope.jobs.values() if job.kind == "synthesize"]
    pending = {"queued", "running", "retry_wait"}
    return (len(jobs) < MAX_SYNTHESIS_RECORDS_PER_LEGACY
        and sum(job.state in pending for job in jobs) < MAX_PENDING_PER_LEGACY
        and db.scalar(select(func.count()).select_from(VoiceJob).where(
            VoiceJob.kind == "synthesize")) < MAX_SYNTHESIS_RECORDS
        and db.scalar(select(func.count()).select_from(VoiceJob).where(
            VoiceJob.kind == "synthesize", VoiceJob.state.in_(pending))) < MAX_PENDING_SYNTHESIS)
