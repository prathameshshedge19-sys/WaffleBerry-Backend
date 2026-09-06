"""Real independent PostgreSQL connections; no production database access."""
from sqlalchemy import func, select
import pytest

from app.config import get_settings
from app.models.conversation import Message, MessageRole
from app.models.turn import ConversationTurn
from app.services import realtime_responses as output, realtime_sessions as sessions, realtime_transcripts as transcripts
from tests.test_realtime_postgresql_l15 import pg, race, grant


@pytest.mark.parametrize("scenario",["completion_interrupt","ack_interrupt","terminal_disconnect","duplicate"])
def test_output_terminal_races(pg,monkeypatch,scenario):
    settings=get_settings();monkeypatch.setattr(settings,"realtime_creations_per_minute",60)
    for iteration in range(15):
        with pg() as db:
            issued=grant(db)
            sid,gen,_=sessions.consume(db,issued["ticket"],"http://localhost:5600","output",settings)
            sessions.owned(db,sid,"output",gen,settings,ready=True)
            receipt=transcripts.admit(db,sid,"output",gen,"input","Disposable race.",settings)
            turn=db.get(ConversationTurn,receipt["turn_id"]);identity=turn.id;claim=turn.claim_token;cid=turn.conversation_id
        proof=output.PlaybackProof("Fully acknowledged disposable reply.","resp",0,1200)
        if scenario=="ack_interrupt":
            from tests.test_realtime_responses_l15 import generated, ack
            gate=output.Output(sid,gen,identity,claim)
            sealed=generated(gate)
        def operation(db,index):
            if scenario=="terminal_disconnect" and index==1:
                sessions.close_owned(db,sid,"output",gen,"browser_disconnect",settings)
                return "disconnected"
            evidence=gate.acknowledge(ack(gate,sealed)) if scenario=="ack_interrupt" and index==0 else proof
            return output.terminate(db,sid,"output",gen,identity,claim,settings,evidence if index==0 or scenario=="duplicate" else None)
        results=race(pg,operation)
        with pg() as db:
            turn=db.get(ConversationTurn,identity)
            assert turn.state in {"completed","interrupted"}
            count=db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id==cid,Message.role==MessageRole.ASSISTANT))
            assert count==(1 if turn.state=="completed" else 0)
            assert sum(isinstance(result,dict) for result in results)<=1
            # An old connection cannot write after it has been fenced.
            sessions.close_owned(db,sid,"output",gen,"client_end",settings)
            with pytest.raises(sessions.RealtimeError): output.terminate(db,sid,"output",gen,identity,claim,settings,proof)
            db.rollback()
            sessions.revoke(db,1)  # retire a reconnectable test session before the next independent race
            db.commit()
