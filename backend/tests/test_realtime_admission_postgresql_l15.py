import pytest
from sqlalchemy import func, select
from app.config import get_settings
from app.models.conversation import Conversation, Message
from app.models.realtime_session import RealtimeSession as Live
from app.models.turn import ConversationTurn
from app.services import realtime_sessions as sessions, realtime_transcripts as transcripts
from tests.test_realtime_postgresql_l15 import pg, race, grant


def test_repeated_first_final_races_exactly_once(pg, monkeypatch):
    settings=get_settings()
    monkeypatch.setattr(settings,"realtime_creations_per_minute",60)
    for iteration in range(25):
        with pg() as db:
            issued=grant(db)
            sid,gen,_=sessions.consume(db,issued["ticket"],"http://localhost:5600","worker",settings)
            sessions.owned(db,sid,"worker",gen,settings,ready=True)
        result=race(pg,lambda db,i: transcripts.admit(db,sid,"worker",gen,"A","Disposable jasmine flowers.",settings))
        assert result[0]["conversation_id"]==result[1]["conversation_id"]
        assert result[0]["turn_id"]==result[1]["turn_id"] and result[0]["message_id"]==result[1]["message_id"]
        assert sorted(r["replayed"] for r in result)==[False,True]
        conflict=race(pg,lambda db,i: transcripts.admit(db,sid,"worker",gen,"A","Disposable jasmine flowers." if i==0 else "Different.",settings))
        assert sum(isinstance(r,dict) for r in conflict)==1 and "realtime_transcript_conflict" in conflict
        stale=race(pg,lambda db,i: transcripts.admit(db,sid,"worker",gen if i==0 else gen-1,"A","Disposable jasmine flowers.",settings))
        assert sum(isinstance(r,dict) for r in stale)==1 and "realtime_access_changed" in stale
        with pg() as db:
            cid=result[0]["conversation_id"]
            assert db.get(Live,sid).conversation_id==cid
            assert db.scalar(select(func.count()).select_from(ConversationTurn).where(ConversationTurn.conversation_id==cid))==1
            assert db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id==cid))==1
            assert db.scalar(select(func.count()).select_from(Conversation))==iteration+1
            sessions.close_owned(db,sid,"worker",gen,"client_end",settings)
