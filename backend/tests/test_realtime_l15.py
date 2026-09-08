import asyncio
from datetime import timedelta
import hashlib
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings
from app.main import app
from app.database import Base, build_engine, get_db
from app.models.conversation import Conversation, Message
from app.models.legacy import Legacy
from app.models.memory import Memory
from app.models.personality import LegacyPersonalityProfile
from app.models.progress import BuilderActivity
from app.models.realtime_session import RealtimeSession as Live
from app.models.turn import ConversationTurn
from app.schemas.realtime import SessionCreate
from app.services import realtime_sessions as service
from app.services.realtime_provider import get_realtime_provider, parse_event, response_usage, session_configuration
from app.services.security import create_access_token, decode_token
from tests.fake_realtime import FakeRealtimeProvider
from tests.test_conversation_turns_l14 import seed

ORIGIN = "http://localhost:5600"

class LiveContext(tuple):
    def __repr__(self):
        return "<isolated realtime test context>"


@pytest.fixture
def live(test_context, monkeypatch, tmp_path):
    client, _, codes, old_provider = test_context
    # HTTP revocation and WebSocket heartbeat run concurrently. StaticPool's
    # single in-memory SQLite connection cannot represent those transactions.
    engine=build_engine("sqlite:///"+(tmp_path/"live.db").as_posix())
    Base.metadata.create_all(engine)
    sessions=sessionmaker(bind=engine,expire_on_commit=False,autoflush=False)
    def database():
        with sessions() as db: yield db
    monkeypatch.setitem(app.dependency_overrides,get_db,database)
    seed((client,sessions,codes,old_provider))
    settings = get_settings()
    monkeypatch.setattr(settings, "realtime_enabled", True)
    monkeypatch.setattr(settings, "cors_origins", ORIGIN)
    fake = FakeRealtimeProvider()
    app.dependency_overrides[get_realtime_provider] = lambda: fake
    try:
        yield LiveContext((client, sessions, fake, settings))
    finally:
        engine.dispose()


def headers(actor=1, origin=ORIGIN):
    return {"Authorization": "Bearer " + create_access_token(actor), "Origin": origin}


def create(live, actor=1, **payload):
    return live[0].post("/api/v1/realtime/sessions", json=payload or {"legacy_id": 1, "mode": "rya"}, headers=headers(actor))


def authenticate(client, grant):
    ws = client.websocket_connect("/api/v1/realtime/connect", headers={"Origin": ORIGIN})
    return ws


def connected(ws, grant):
    ws.send_json({"type": "authenticate", "ticket": grant["ticket"]})
    assert ws.receive_json()["type"] == "connecting"
    assert ws.receive_json()["type"] == "ready"


@pytest.mark.parametrize("actor,mode,role", [(1,"rya","owner"),(2,"rya","collaborator"),(3,"legacy","viewer")])
def test_authorized_scope_and_no_effects(live, actor, mode, role):
    client, factory, fake, settings = live
    with factory() as db:
        before = {model: db.scalar(select(func.count()).select_from(model)) for model in
                  (Conversation, Message, ConversationTurn, Memory, BuilderActivity, LegacyPersonalityProfile)}
    response = create(live, actor, legacy_id=1, mode=mode)
    assert response.status_code == 201, response.text
    grant = response.json()
    assert grant["conversation_id"] is None
    assert "openai" not in response.text.lower()
    with factory() as db:
        row = db.get(Live, grant["session_id"])
        assert row.role == role and row.actor_user_id == actor
        assert row.ticket_hash == hashlib.sha256(grant["ticket"].encode()).hexdigest()
        assert grant["ticket"] not in repr(row.__dict__)
        assert before == {model: db.scalar(select(func.count()).select_from(model)) for model in before}
    with authenticate(client, grant) as ws:
        connected(ws, grant)
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        ws.send_json({"type": "end_call"})
        assert ws.receive_json() == {"type": "ended", "reason": "client_end"}
    assert fake.closed and fake.cancelled
    with factory() as db:
        row = db.get(Live, grant["session_id"])
        assert row.state == "ended" and row.active_actor_id is None and row.lease_owner is None


@pytest.mark.parametrize("actor,payload,status", [
    (4,{"legacy_id":1,"mode":"rya"},403), (3,{"legacy_id":1,"mode":"rya"},403),
    (2,{"legacy_id":1,"mode":"legacy"},403), (1,{"legacy_id":999,"mode":"rya"},403),
    (1,{"legacy_id":1,"mode":"rya","role":"owner"},422),
    (1,{"legacy_id":1,"mode":"rya","tools":[]},422),
    (1,{"conversation_id":999},403), (1,{"conversation_id":1,"mode":"legacy"},422)])
def test_scope_rejection(live, actor, payload, status):
    assert create(live, actor, **payload).status_code == status
    assert not live[2].connected


def test_disabled_unauthenticated_and_origin(live, monkeypatch):
    client, _, _, settings = live
    assert client.post("/api/v1/realtime/sessions", json={"legacy_id":1,"mode":"rya"}).status_code == 401
    assert client.post("/api/v1/realtime/sessions", json={"legacy_id":1,"mode":"rya"}, headers=headers(origin="https://evil.example")).status_code == 403
    monkeypatch.setattr(settings, "realtime_enabled", False)
    assert create(live).status_code == 503


def test_incomplete_setup(live):
    with live[1].begin() as db:
        db.get(Legacy,1).setup_status = "collecting_identity"
    response = create(live)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "realtime_setup_incomplete"


def test_existing_conversation_scope_and_immutable_binding(live):
    with live[1].begin() as db:
        conversation = Conversation(user_id=1, legacy_id=1, mode="rya", title="Existing")
        db.add(conversation); db.flush(); cid=conversation.id
    assert create(live, 2, conversation_id=cid).status_code == 403
    grant=create(live, conversation_id=cid).json()
    with live[1]() as db:
        sid,generation,_=service.consume(db,grant["ticket"],ORIGIN,"binding",live[3])
        service.owned(db,sid,"binding",generation,live[3],ready=True)
    with live[1].begin() as db:
        assert service.bind_once(db, grant["session_id"], 1, lambda _: pytest.fail("must not create"), live[3],owner="binding",generation=generation) == cid


def test_one_call_per_actor_and_creation_rate(live, monkeypatch):
    grant=create(live).json()
    assert create(live).status_code == 409
    with live[1].begin() as db:
        service.revoke(db,1)
    monkeypatch.setattr(live[3],"realtime_creations_per_minute",1)
    assert create(live).status_code == 429


@pytest.mark.parametrize("case", ["expired","used","forged","wrong_origin"])
def test_ticket_security(live, case):
    grant=create(live).json()
    with live[1]() as db:
        if case == "expired":
            db.get(Live,grant["session_id"]).ticket_expires_at = service.now()-timedelta(seconds=1)
            db.commit()
        if case == "used":
            service.consume(db,grant["ticket"],ORIGIN,"first-owner",live[3])
        with pytest.raises(service.RealtimeError) as caught:
            service.consume(db,"x"*43 if case == "forged" else grant["ticket"],"https://evil.example" if case == "wrong_origin" else ORIGIN,"second-owner",live[3])
        assert caught.value.code == {"expired":"realtime_ticket_expired","used":"realtime_ticket_used","forged":"realtime_ticket_invalid","wrong_origin":"realtime_ticket_invalid"}[case]


@pytest.mark.parametrize("native", [{"type":"session.update","session":{"tools":[]}},
    {"type":"response.create"},{"type":"provider_event","event":{}},
    {"type":"ping","ticket":"secret"},{"type":"audio","audio":"AAA="}])
def test_browser_protocol_injection(live, native):
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        ws.send_json(native)
        assert ws.receive_json()["code"] == "realtime_protocol_error"
        assert ws.receive_json()["type"] == "ended"
    assert live[2].closed


@pytest.mark.parametrize("binary",[False,True])
def test_oversized_or_binary_frame(live,binary):
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        if binary: ws.send_bytes(b"x"*9000)
        else: ws.send_text("x"*9000)
        assert ws.receive_json()["code"] == "realtime_protocol_error"


def test_websocket_origin_and_query_rejected_before_provider(live):
    for path,origin in [("/api/v1/realtime/connect", "https://evil.example"),("/api/v1/realtime/connect?ticket=leak",ORIGIN)]:
        with pytest.raises(WebSocketDisconnect):
            with live[0].websocket_connect(path,headers={"Origin":origin}):
                pass
    assert not live[2].connected


def test_reconnect_and_stale_owner(live):
    grant=create(live).json()
    with live[1]() as db:
        sid,generation,_=service.consume(db,grant["ticket"],ORIGIN,"old",live[3])
        service.owned(db,sid,"old",generation,live[3],ready=True)
        service.close_owned(db,sid,"old",generation,"browser_disconnect",live[3])
        new=service.reconnect(db,sid,1,ORIGIN,live[3])
        _,current,_=service.consume(db,new["ticket"],ORIGIN,"new",live[3])
        assert current > generation
        service.close_owned(db,sid,"old",generation,"client_end",live[3])
        with pytest.raises(service.RealtimeError): service.owned(db,sid,"old",generation,live[3])
        db.rollback()
        assert db.get(Live,sid,populate_existing=True).lease_owner == "new"


@pytest.mark.parametrize("case",["lease","session","reconnect"])
def test_crash_expiry_recovery(live,case):
    grant=create(live).json()
    with live[1]() as db:
        sid,generation,_=service.consume(db,grant["ticket"],ORIGIN,"dead",live[3])
        if case == "reconnect":
            service.close_owned(db,sid,"dead",generation,"browser_disconnect",live[3])
        row=db.get(Live,sid)
        setattr(row,{"lease":"lease_expires_at","session":"expires_at","reconnect":"reconnect_until"}[case],service.now()-timedelta(seconds=60))
        db.commit()
        service.sweep(db,live[3])
        row=db.get(Live,sid,populate_existing=True)
        assert row.state == "ended" and row.lease_owner is None and row.active_actor_id is None
        assert db.scalar(select(func.count()).select_from(ConversationTurn)) == 0


def test_logout_ends_call_and_denies_old_token(live):
    # Reuse the pre-logout token: create()/headers() mint a fresh token on each
    # call, which legitimately becomes newer than logout at a second boundary.
    old_headers=headers()
    payload={"legacy_id":1,"mode":"rya"}
    grant=live[0].post("/api/v1/realtime/sessions",json=payload,headers=old_headers).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        assert live[0].post("/api/v1/auth/logout",headers=old_headers).status_code == 204
        ws.send_json({"type":"ping"})
        assert ws.receive_json()["code"] == "realtime_access_changed"
    assert live[0].post("/api/v1/realtime/sessions",json=payload,headers=old_headers).status_code == 403
    # The existing auth behavior is deliberately unchanged for text/API access.
    assert live[0].get("/api/v1/auth/me",headers=old_headers).status_code == 200


@pytest.mark.parametrize("actor,role",[(2,"collaborator"),(3,"viewer")])
def test_revoke_membership_hook(live,actor,role):
    from app.models.collaboration import LegacyCollaborator
    from app.models.viewer import LegacyViewerAccess
    grant=create(live,actor,legacy_id=1,mode="rya" if role=="collaborator" else "legacy").json()
    model=LegacyCollaborator if actor==2 else LegacyViewerAccess
    with live[1]() as db:
        record=db.scalar(select(model).where(model.user_id==actor,model.legacy_id==1)).id
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        response=live[0].post(f"/api/v1/access/legacies/1/members/{role}/{record}/revoke",headers=headers())
        assert response.status_code == 200,response.text
        ws.send_json({"type":"ping"})
        assert ws.receive_json()["code"] == "realtime_access_changed"
    assert live[2].closed


def test_future_bind_once_transaction(live):
    grant=create(live).json()
    with live[1]() as db:
        sid,generation,_=service.consume(db,grant["ticket"],ORIGIN,"binding",live[3])
        service.owned(db,sid,"binding",generation,live[3],ready=True)
    calls=[]
    def factory(row):
        calls.append(1)
        conversation=Conversation(user_id=row.actor_user_id,legacy_id=row.legacy_id,mode=row.mode,title="Voice")
        db.add(conversation)
        return conversation
    with live[1].begin() as db:
        cid=service.bind_once(db,grant["session_id"],1,factory,live[3],owner="binding",generation=generation)
    with live[1].begin() as db:
        assert service.bind_once(db,grant["session_id"],1,factory,live[3],owner="binding",generation=generation) == cid
    assert len(calls)==1


def test_provider_wire_and_usage():
    config=session_configuration(get_settings(),"cedar")
    assert config["model"]=="gpt-realtime-2.1" and config["reasoning"]=={"effort":"low"}
    assert config["tools"]==[] and config["tool_choice"]=="none"
    assert config["audio"]["input"]["turn_detection"]["create_response"] is False
    assert config["audio"]["input"]["transcription"]["model"]=="gpt-live-transcribe"
    event=parse_event({"type":"response.output_audio.delta","delta":"secret-audio","arbitrary":"unsafe"},7)
    assert event.generation==7 and "secret-audio" not in repr(event) and "arbitrary" not in event.payload
    parsed=response_usage({"usage":{"input_tokens":20,"output_tokens":10,"input_token_details":{"cached_tokens":3,"audio_tokens":7},"output_token_details":{"audio_tokens":8}}},"gpt-realtime-2.1")
    assert parsed.audio_output_tokens.value==8 and parsed.cached_input_tokens.value==3
    assert response_usage({},"gpt-realtime-2.1").input_tokens.status=="unavailable"


def test_fake_harness_bounded_and_stale_generation():
    async def run():
        fake=FakeRealtimeProvider(depth=2)
        await fake.connect("marin",2)
        await fake.append_audio(bytes(960))
        fake.emit("audio",generation=1)
        fake.emit("speech_started")
        with pytest.raises(asyncio.QueueFull): fake.emit("audio")
        assert (await fake.receive()).generation==1
        assert (await fake.receive()).generation==2
        assert fake.audio_bytes==960
        await fake.cancel();await fake.close()
        assert fake.cancelled and fake.closed
    asyncio.run(run())


def test_client_rate_limit(live,monkeypatch):
    monkeypatch.setattr(live[3],"realtime_messages_per_second",2)
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        for _ in range(5): ws.send_json({"type":"ping"})
        events=[ws.receive_json()]
        while events[-1]["type"] not in {"error","ended"}: events.append(ws.receive_json())
        assert events[-1].get("code")=="realtime_rate_limit"
    assert live[2].closed


def test_bridge_queue_overflow(live):
    from app.services.realtime_provider import ProviderEvent
    class Burst(FakeRealtimeProvider):
        count = 0
        async def receive(self):
            self.count += 1
            return ProviderEvent("input_transcript_delta",self.generation,{"item_id":str(self.count),"delta":"bounded partial"})
    fake=Burst()
    app.dependency_overrides[get_realtime_provider]=lambda:fake
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        value=ws.receive_json()
        while value["type"]=="transcript_provisional": value=ws.receive_json()
        assert value["code"]=="realtime_queue_overrun"
    assert fake.closed


@pytest.mark.parametrize("failure",["connect","disconnect","exception"])
def test_provider_failures_release_resources(live,failure):
    class Broken(FakeRealtimeProvider):
        async def connect(self,voice,generation):
            await super().connect(voice,generation)
            if failure=="connect": raise service.RealtimeError("realtime_provider_failed",502)
        async def receive(self):
            if failure=="exception": raise RuntimeError("private provider data must never escape")
            raise service.RealtimeError("realtime_provider_connection",502)
    fake=Broken();app.dependency_overrides[get_realtime_provider]=lambda:fake
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        ws.send_json({"type":"authenticate","ticket":grant["ticket"]})
        events=[]
        while not events or events[-1]["type"]!="ended": events.append(ws.receive_json())
        assert "private provider" not in json.dumps(events)
    assert fake.closed
    with live[1]() as db:
        row=db.get(Live,grant["session_id"])
        assert row.lease_owner is None and row.state in {"failed","reconnecting"}


def test_reauthorization_ignores_cached_membership(live):
    from app.models.collaboration import LegacyCollaborator
    grant=create(live,2).json()
    with live[1]() as db:
        cached=db.scalar(select(LegacyCollaborator).where(LegacyCollaborator.user_id==2))
        assert cached.status=="active"
        # Separate committed access update, without relying on the explicit hook.
        with live[1].begin() as other:
            other.get(LegacyCollaborator,cached.id).status="revoked"
        with pytest.raises(service.RealtimeError):
            service.reauthorize(db,db.get(Live,grant["session_id"]),live[3])


def test_ticket_and_content_absent_from_telemetry(live,monkeypatch):
    from app.services import turn_observability as obs
    records=[]
    class Sink:
        def emit(self,record,level): records.append(record)
    monkeypatch.setattr(obs,"sink",Sink())
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        ws.send_json({"type":"end_call"})
        assert ws.receive_json()["type"]=="ended"
    rendered=json.dumps(records)
    assert grant["ticket"] not in rendered
    assert "transcript" not in rendered and "password" not in rendered
    assert "realtime_session_authorized" in rendered and "realtime_session_ready" in rendered


def test_marin_cedar_and_no_silent_substitution(live):
    from app.models.user import User
    with live[1].begin() as db: db.get(User,1).voice_preference="cedar"
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        assert live[2].voice=="cedar"
        ws.send_json({"type":"end_call"});ws.receive_json()
    with pytest.raises(service.RealtimeError): session_configuration(live[3],"unknown")


def test_database_cancellation_waits_for_thread():
    import threading
    from app.api.routes.realtime import database_call
    async def run():
        entered,release,finished=threading.Event(),threading.Event(),threading.Event()
        def operation():
            entered.set()
            assert release.wait(5)
            finished.set()
        task=asyncio.create_task(database_call(operation))
        while not entered.is_set(): await asyncio.sleep(.001)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert finished.is_set()
    asyncio.run(run())


def test_abrupt_browser_disconnect_cleans_and_allows_reconnect(live):
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        ws.close()
        assert ws.receive_json()["reason"]=="browser_disconnect"
    assert live[2].closed
    response=live[0].post('/api/v1/realtime/sessions/'+grant["session_id"]+'/reconnect',headers=headers())
    assert response.status_code==200
    assert response.json()["generation"]>grant["generation"]


def test_session_expiry_is_capped_by_access_token(live):
    timestamp=service.now()
    with live[1]() as db:
        grant=service.authorize(db,1,SessionCreate(legacy_id=1,mode="rya"),ORIGIN,
            {"iat":timestamp.timestamp(),"exp":(timestamp+timedelta(seconds=60)).timestamp()},live[3])
        assert service.utc(grant["expires_at"])<=timestamp+timedelta(seconds=60)


def test_future_binding_rejects_stale_generation(live):
    grant=create(live).json()
    with live[1]() as db:
        sid,generation,_=service.consume(db,grant["ticket"],ORIGIN,"binding",live[3])
        service.owned(db,sid,"binding",generation,live[3],ready=True)
        with pytest.raises(service.RealtimeError):
            service.bind_once(db,sid,1,lambda _:pytest.fail("stale factory"),live[3],owner="binding",generation=generation-1)
