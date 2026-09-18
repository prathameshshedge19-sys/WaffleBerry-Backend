"""Opt-in L21.5 live-job transactions on the existing disposable PG harness."""
import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.config import get_settings
from app.models.turn import ConversationTurn
from app.models.user import User
from app.models.viewer import LegacyViewerAccess
from app.models.voice_profile import VoiceAsset, VoiceJob
from app.schemas.realtime import SessionCreate
from app.services import realtime_sessions as sessions, realtime_transcripts as transcripts
from app.services.realtime_responses import Output
from app.services.realtime_speech import LiveSpeechRenderer, PreservedSpeechUnavailable
from app.services.voice_profiles import VoiceJobService, StaleVoiceClaim
from app.services.voice_providers import FakeClonedSpeechProvider
from tests.test_realtime_speech_audit_l21 import answer_for, ExactStandard
from tests.test_voice_models_l21 import live_synthesis_migration
from tests.test_voice_postgresql_l21 import pg_voice, parallel
from tests.voice_l21_helpers import reserve, prepare, activate


@pytest.fixture
def pg_live(pg_voice):
    settings = get_settings().model_copy(update={"voice_live_enabled": True,
        "voice_cloning_enabled": True, "voice_synthesis_provider": "fake"})
    with pg_voice() as db:
        db.get(User, 3).is_verified = True
        db.add(LegacyViewerAccess(legacy_id=1, user_id=3, status="active")); db.commit()
        version = prepare(db, reserve(db)); activate(db, version)
        version_state = SimpleNamespace(reference_transcript=version.reference_transcript,
            operation_generation=version.operation_generation)
        now = sessions.now().timestamp()
        grant = sessions.authorize(db, 3, SessionCreate(legacy_id=1, mode="legacy"),
            "http://localhost:5600", {"iat": now, "exp": now + 1800}, settings)
        sid, generation, _ = sessions.consume(db, grant["ticket"], "http://localhost:5600", "audit", settings)
        sessions.owned(db, sid, "audit", generation, settings, ready=True)
        receipt = transcripts.admit(db, sid, "audit", generation, "audit", "Synthetic question", settings)
        turn = db.get(ConversationTurn, receipt["turn_id"])
        turn.state, turn.claim_token = "streaming", str(uuid4()); db.commit()
        output = Output(sid, generation, turn.id, turn.claim_token, text_first=True)
    renderer = LiveSpeechRenderer(pg_voice, standard_provider=ExactStandard())
    return SimpleNamespace(factory=pg_voice, settings=settings, output=output,
        answer=answer_for(output), renderer=renderer, version=version_state)


def test_postgresql_duplicate_live_admission_is_one_job(pg_live):
    w = pg_live
    first, second = parallel(*(lambda: w.renderer._admit(w.output, w.answer, "audit", w.settings) for _ in range(2)))
    assert first[0] == second[0]
    with w.factory() as db:
        jobs = list(db.scalars(sa.select(VoiceJob).where(VoiceJob.purpose == "live")))
        assert len(jobs) == 1 and jobs[0].priority == 90
        assert jobs[0].authoritative_text == w.answer.text
        assert jobs[0].authoritative_text_digest == w.answer.text_digest
        assert jobs[0].realtime_turn_id == w.output.turn_id
        assert jobs[0].realtime_claim_token == w.output.claim


def test_postgresql_live_publication_cancel_race_purges_asset(pg_live):
    w = pg_live
    # Repeat using distinct full request identities on the same authorized turn.
    for iteration in range(10):
        job_id, _ = w.renderer._admit(w.output, w.answer, "audit", w.settings)
        with w.factory.begin() as db:
            claimed = VoiceJobService().claim(db, "synthesize")
            assert claimed.id == job_id
            token = claimed.lease_token
        speech = asyncio.run(FakeClonedSpeechProvider().synthesize(
            authoritative_text=w.answer.text, reference_audio=b"synthetic",
            reference_text=w.version.reference_transcript, language="mr",
            operation_generation=w.version.operation_generation))
        with w.factory.begin() as db:
            asset = VoiceJobService().reserve_generated(db, job_id, token, speech,
                SimpleNamespace(backend_name="local", bucket_name=None, encryption_key_id=None))
            asset_id, size = asset.id, asset.byte_count
        overlap = threading.Barrier(2)
        pids = []
        def publish():
            with w.factory.begin() as db:
                pids.append(db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one())
                overlap.wait(timeout=5)
                try:
                    VoiceJobService().publish_generated(db, job_id, token, speech, asset_id=asset_id,
                        stored=SimpleNamespace(byte_size=size, version=None), wav_digest="a" * 64)
                    return "published"
                except StaleVoiceClaim:
                    return "stale"
        def cancel():
            with w.factory.begin() as db:
                pids.append(db.execute(sa.text("SELECT pg_backend_pid()")).scalar_one())
                overlap.wait(timeout=5)
                VoiceJobService().cancel_live(db, job_id, turn_id=w.output.turn_id, claim=w.output.claim)
        results = parallel(publish, cancel)
        assert len(set(pids)) == 2
        assert results[0] in {"published", "stale"}
        with w.factory() as db:
            assert db.get(VoiceJob, job_id).state == "cancelled"
            assert db.get(VoiceAsset, asset_id).state == "purge_pending"
            assert db.scalar(sa.select(sa.func.count()).select_from(VoiceAsset).where(
                VoiceAsset.job_id == job_id, VoiceAsset.state == "available")) == 0


def test_postgresql_live_downgrade_refuses_without_losing_binding(pg_live):
    w = pg_live
    job_id, _ = w.renderer._admit(w.output, w.answer, "audit", w.settings)
    with w.factory.kw["bind"].begin() as conn:
        context = MigrationContext.configure(conn)
        with Operations.context(context), pytest.raises(RuntimeError, match="live voice jobs exist"):
            live_synthesis_migration.downgrade()
        assert conn.execute(sa.select(VoiceJob.realtime_claim_token).where(VoiceJob.id == job_id)).scalar_one() == w.output.claim
        # The migration's LIVE constraint remains present and rejects missing binding.
        with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
            conn.execute(sa.update(VoiceJob).where(VoiceJob.id == job_id).values(realtime_turn_id=None))
