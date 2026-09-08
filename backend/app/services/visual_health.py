"""Operator-only CLI health; no HTTP/public discovery or content metadata."""
from datetime import datetime, timezone
from sqlalchemy import func, select
from app.models.visual_companion import VisualGenerationJob as Job
from app.services.visual_companions import aware

PURGE_AGE_SECONDS = 3600
REPEATED_FAILURE_ATTEMPTS = 5
SAFE_CODES = {'visual_remote_erasure_unproven','visual_writer_pending','visual_erasure_unconfirmed'}


def health_summary(sessions, now=None):
    now = now or datetime.now(timezone.utc)
    with sessions() as db:
        active = (Job.kind == 'purge', Job.state.in_(('queued','running','retry_wait')))
        count = db.scalar(select(func.count()).select_from(Job).where(*active))
        oldest = db.scalar(select(func.min(Job.created_at)).where(*active))
        repeated = db.scalar(select(func.count()).select_from(Job).where(*active,
            Job.attempts >= REPEATED_FAILURE_ATTEMPTS))
        remote = db.scalar(select(func.count()).select_from(Job).where(*active,
            Job.last_error_code == 'visual_remote_erasure_unproven'))
        failed = db.execute(select(Job.last_error_code, Job.updated_at).where(*active,
            Job.last_error_code.in_(SAFE_CODES)).order_by(Job.updated_at.desc()).limit(1)).first()
        age = max(0, int((now-aware(oldest)).total_seconds())) if oldest else 0
        return {'event':'visual_worker_health','status':'error' if age>=PURGE_AGE_SECONDS or repeated else 'ok',
            'pending_purge_count':count,'oldest_purge_age_seconds':age,
            'repeated_failure_count':repeated,'remote_reconcile_pending_count':remote,
            'last_cleanup_error_code':failed[0] if failed else None,
            'last_cleanup_error_at':aware(failed[1]).isoformat() if failed else None,
            'thresholds':{'oldest_purge_seconds':PURGE_AGE_SECONDS,
                'repeated_failure_attempts':REPEATED_FAILURE_ATTEMPTS,'legacy_cleanup_backlog_admission_stop':2}}
