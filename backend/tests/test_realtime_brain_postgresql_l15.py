"""L15 shared effects versus duplicate finalization/revocation, real PostgreSQL."""
import asyncio
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models.memory import Memory
from app.models.legacy import Legacy
from app.models.progress import BuilderActivity
from app.models.turn import ConversationTurn, TurnEffect
from app.services import realtime_brain as brain, realtime_responses as output
from app.services import realtime_sessions as sessions, realtime_transcripts as transcripts
from app.services.memory import MemoryAnalysis, MemoryCandidate
from app.schemas.realtime import SessionCreate
from tests.conftest import FakeMemoryProvider
from tests.test_realtime_postgresql_l15 import pg, race, grant


@pytest.mark.parametrize("scenario", ["duplicate_effects", "revocation_effects"])
def test_shared_live_effect_races(pg, monkeypatch, scenario):
    settings = get_settings()
    monkeypatch.setattr(settings, "realtime_creations_per_minute", 60)
    for iteration in range(10):
        content = f"Disposable astronomy contribution number {iteration}."
        provider = FakeMemoryProvider()
        provider.analyses[content] = MemoryAnalysis(source_language="english", normalized_query=content, memories=[
            MemoryCandidate(canonical_text=content, category="education", confidence=1)])
        with pg() as db:
            legacy_id = iteration + 1
            if iteration:
                db.add(Legacy(id=legacy_id, owner_user_id=1, setup_status="active", subject_name="Disposable"))
                db.commit()
            stamp = sessions.now().timestamp()
            issued = sessions.authorize(db, 1, SessionCreate(legacy_id=legacy_id, mode="rya"),
                "http://localhost:5600", {"iat": stamp, "exp": stamp + 1800}, settings)
            sid, gen, _ = sessions.consume(db, issued["ticket"], "http://localhost:5600", "brain", settings)
            sessions.owned(db, sid, "brain", gen, settings, ready=True)
            receipt = transcripts.admit(db, sid, "brain", gen, "input", content, settings)
            turn = db.get(ConversationTurn, receipt["turn_id"])
            out = output.Output(sid, gen, turn.id, turn.claim_token)
            prepared = asyncio.run(transcripts.prepare_admitted(db, sid, "brain", gen, turn.id, settings, provider))
            out.brain = brain.bind(db, out, "brain", settings, prepared)
            output.terminate(db, sid, "brain", gen, turn.id, out.claim, settings, output.PlaybackProof("Heard.", "resp", 0, 1200))
        def operation(db, index):
            if scenario == "revocation_effects" and index == 1:
                sessions.revoke(db, 1); db.commit(); return "revoked"
            return brain.finalize(pg.kw["bind"], out, "brain", settings).memories_saved
        results = race(pg, operation)
        with pg() as db:
            receipt = db.get(TurnEffect, (out.turn_id, "memory"))
            effects = db.scalar(select(func.count()).select_from(TurnEffect).where(TurnEffect.turn_id == out.turn_id))
            assert effects <= 2
            if scenario == "duplicate_effects":
                assert results == [1, 1] and effects == 2
            assert db.scalar(select(func.count()).select_from(Memory).where(Memory.source_message_id == out.brain.prepared.actor.user_message_id)) <= 1
            if receipt is None:
                assert effects == 0
            activity = db.scalar(select(BuilderActivity).where(BuilderActivity.legacy_id == legacy_id))
            if activity:
                assert activity.contribution_count == 1
            db.rollback()
            sessions.revoke(db, 1); db.commit()
