"""Production-dialect admission ceilings and restart recovery."""
from datetime import timedelta

from sqlalchemy import func, select

from app.models.voice_profile import VoiceJob
from app.services import voice_limits
from app.services.voice_profiles import AuthorizedSpeechContext, LegacySpeechOrchestrator, VoiceJobService, utcnow
from tests.test_voice_postgresql_l21 import pg_voice, parallel
from tests.voice_l21_helpers import activate, prepare, reserve


def test_global_capacity_is_atomic_across_distinct_legacy_transactions(pg_voice, monkeypatch):
    for legacy_id in (1, 2):
        with pg_voice() as db:
            activate(db, prepare(db, reserve(db, legacy_id=legacy_id)))
    monkeypatch.setattr(voice_limits, "MAX_PENDING_SYNTHESIS", 1)
    def admit(legacy_id):
        with pg_voice.begin() as db:
            job = LegacySpeechOrchestrator().admit_synthesis(db,
                AuthorizedSpeechContext(legacy_id, "legacy", 1, None, 1),
                authoritative_text="Synthetic QA.", purpose="preview", request_key="quota")
            return job.id if job else None
    results = parallel(lambda: admit(1), lambda: admit(2))
    assert sum(value is not None for value in results) == 1
    assert admit(1) in results and admit(2) in results
    with pg_voice() as db:
        assert db.scalar(select(func.count()).select_from(VoiceJob).where(
            VoiceJob.kind == "synthesize")) == 1


def test_transaction_rollback_releases_capacity_and_expired_third_claim_fails(pg_voice, monkeypatch):
    with pg_voice() as db:
        version = prepare(db, reserve(db))
        activate(db, version)
    def enqueue(db):
        return LegacySpeechOrchestrator().admit_synthesis(db,
            AuthorizedSpeechContext(1, "legacy", 1, None, 1),
            authoritative_text="Synthetic QA.", purpose="preview", request_key="rollback")
    with pg_voice() as db:
        assert enqueue(db) is not None
        db.flush()
        db.rollback()  # simulate admission transaction/DB conflict
    with pg_voice.begin() as db:
        identity = enqueue(db).id
    for _ in range(3):
        with pg_voice.begin() as db:
            assert VoiceJobService().claim(db, "synthesize").id == identity
        with pg_voice.begin() as db:
            db.get(VoiceJob, identity).lease_expires_at = utcnow() - timedelta(seconds=1)
    with pg_voice.begin() as db:
        assert VoiceJobService().claim(db, "synthesize") is None
    with pg_voice() as db:
        assert db.get(VoiceJob, identity).state == "failed"
