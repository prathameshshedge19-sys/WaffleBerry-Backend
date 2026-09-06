import base64
import time
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from app.models.conversation import Message, MessageRole
from app.models.turn import ConversationTurn, TurnEffect
from app.services import realtime_responses as output, realtime_sessions as sessions, realtime_transcripts as transcripts
from app.services.realtime_provider import ProviderEvent
from tests.test_realtime_l15 import live, create, authenticate, connected
from tests.test_realtime_transcripts_l15 import leased, emit, TEXT
from tests.test_conversation_turns_l14 import canonical_snapshot


def generation():
    return output.Output(str(uuid4()), 1, 1, str(uuid4()))


def provider(out, kind, **payload):
    if kind == "response_created":
        payload = {"response": {"id": "resp_test", "metadata": {"generation_id": out.claim}}, **payload}
    elif kind == "response_done":
        payload = {"response": {"id": "resp_test", "status": "completed"}, **payload}
    else:
        payload = {"response_id": "resp_test", "item_id": "item_test", "content_index": 0, **payload}
    return out.provider(ProviderEvent(kind, 1, payload))


def generated(out):
    provider(out, "response_created")
    provider(out, "audio", delta=base64.b64encode(bytes(2400)).decode())
    provider(out, "output_transcript_delta", delta="Disposable response.")
    provider(out, "output_transcript_done", transcript="Disposable response.")
    provider(out, "audio_done")
    return provider(out, "response_done")[0]


def ack(out, end):
    return dict(type="playback_drained", **out.binding(), sequence=end["sequence"], samples=end["samples"], seal=end["seal"])


def test_no_proof_until_matching_full_playback_and_duplicate_terminal():
    out = generation(); end = generated(out)
    assert out.provider_done and out.final and not out.retired
    assert provider(out, "response_done") == []
    proof = out.acknowledge(ack(out, end))
    assert proof.transcript == "Disposable response."
    out.retired = True
    assert out.acknowledge(ack(out, end)) is None
    assert provider(out, "audio", delta="AAA=") == []
    assert provider(out, "output_transcript_done", transcript="Late") == []


@pytest.mark.parametrize("field,value", [("session_id",str(uuid4())),("generation",2),("turn_id",2),
                                        ("active_generation_id",str(uuid4())),("response_id","old")])
def test_foreign_ack_is_not_a_completion(field, value):
    out = generation(); end = generated(out); value_ack = ack(out, end); value_ack[field] = value
    assert out.acknowledge(value_ack) is None


@pytest.mark.parametrize("field,value", [("sequence",7),("samples",1),("seal","x"*43)])
def test_forged_audio_totals_or_seal_rejected(field,value):
    out = generation(); end = generated(out); value_ack = ack(out,end); value_ack[field]=value
    with pytest.raises(sessions.RealtimeError): out.acknowledge(value_ack)


def test_premature_ack_overflow_mixed_item_and_transcript_bounds():
    out=generation();provider(out,"response_created")
    with pytest.raises(sessions.RealtimeError): out.acknowledge(dict(type="playback_drained",**out.binding(),sequence=-1,samples=0))
    for _ in range(20): provider(out,"audio",delta=base64.b64encode(bytes(48000)).decode())
    with pytest.raises(sessions.RealtimeError,match="overrun"): provider(out,"audio",delta="AAA=")
    out=generation();provider(out,"response_created");provider(out,"audio",delta="AAA=")
    with pytest.raises(sessions.RealtimeError): provider(out,"output_transcript_done",item_id="other",transcript="No")
    with pytest.raises(sessions.RealtimeError): provider(out,"output_transcript_delta",delta="x"*8001)


@pytest.mark.parametrize("interrupt_first",[True,False])
def test_terminal_atomicity_and_duplicate_finalization(live,interrupt_first):
    before=canonical_snapshot(live[1]);sid,gen=leased(live)
    with live[1]() as db:
        receipt=transcripts.admit(db,sid,"worker",gen,"A",TEXT,live[3])
        turn=db.get(ConversationTurn,receipt["turn_id"]);claim=turn.claim_token
        proof=output.PlaybackProof("Disposable reply.","resp",0,1200)
        args=(db,sid,"worker",gen,turn.id,claim,live[3])
        assert output.terminate(*args,None if interrupt_first else proof)
        assert output.terminate(*args,proof) is None
        assert output.terminate(*args) is None
        db.expire_all(); turn=db.get(ConversationTurn,turn.id)
        assert turn.state==("interrupted" if interrupt_first else "completed")
        assert bool(turn.assistant_message_id) is not interrupt_first
        assert db.scalar(select(func.count()).select_from(TurnEffect))==0
    assert canonical_snapshot(live[1])==before


def test_assistant_insert_failure_rolls_back_entire_completion(live):
    sid,gen=leased(live)
    with live[1]() as db:
        receipt=transcripts.admit(db,sid,"worker",gen,"A",TEXT,live[3])
        turn=db.get(ConversationTurn,receipt["turn_id"]);claim=turn.claim_token;identity=turn.id
        def fail(*args): raise RuntimeError("injected")
        event.listen(Message,"before_insert",fail)
        try:
            with pytest.raises(RuntimeError): output.terminate(db,sid,"worker",gen,identity,claim,live[3],output.PlaybackProof("Reply","resp",0,1200))
        finally: event.remove(Message,"before_insert",fail)
        db.expire_all();turn=db.get(ConversationTurn,identity)
        assert turn.state=="streaming" and turn.assistant_message_id is None
        assert db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id==turn.conversation_id))==1


@pytest.mark.parametrize("reason",["revoked","stale_connection","stale_claim"])
def test_completion_rechecks_authorization_and_claim(live,reason):
    sid,gen=leased(live)
    with live[1]() as db:
        receipt=transcripts.admit(db,sid,"worker",gen,"A",TEXT,live[3])
        turn=db.get(ConversationTurn,receipt["turn_id"]);claim=turn.claim_token;identity=turn.id
        if reason=="revoked":sessions.revoke(db,1);db.commit()
        if reason=="stale_connection":gen+=1
        if reason=="stale_claim":claim=str(uuid4())
        with pytest.raises(sessions.RealtimeError):output.terminate(db,sid,"worker",gen,identity,claim,live[3],output.PlaybackProof("Reply","resp",0,1200))
        db.expire_all();assert db.get(ConversationTurn,identity).assistant_message_id is None


def test_stop_before_provider_created_cancels_late_response(live):
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        emit(ws,live[2],"input_committed",item_id="A")
        emit(ws,live[2],"input_transcript_done",item_id="A",transcript=TEXT)
        receipt=receive(ws,"transcript_final");thinking=receive(ws,"assistant_thinking")
        binding={k:thinking[k] for k in ("session_id","generation","turn_id","active_generation_id","response_id")}
        ws.send_json(dict(type="interrupt",**binding));receive(ws,"assistant_interrupted")
        emit(ws,live[2],"response_created",response={"id":"late_created","metadata":{"generation_id":thinking["active_generation_id"]}})
        ws.send_json({"type":"ping"});receive(ws,"pong")
        assert "late_created" in live[2].cancelled_responses
        ws.send_json({"type":"end_call"});assert ws.receive_json()["type"]=="ended"
    with live[1]() as db:assert db.get(ConversationTurn,receipt["turn_id"]).state=="interrupted"


def test_bounded_microphone_burst_waits_for_provider_write(live,monkeypatch):
    import asyncio
    started=False
    async def slow_create(*args,**kwargs):
        nonlocal started
        started=True
        await asyncio.sleep(.9)
    monkeypatch.setattr(live[2],"create_response",slow_create)
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        emit(ws,live[2],"input_committed",item_id="A")
        emit(ws,live[2],"input_transcript_done",item_id="A",transcript=TEXT)
        receive(ws,"transcript_final");receive(ws,"assistant_thinking")
        async def wait_for(predicate):
            for _ in range(150):
                if predicate():return
                await asyncio.sleep(.02)
            raise AssertionError("Bounded burst did not recover")
        ws.portal.call(lambda:wait_for(lambda:started))
        for sequence in range(20):ws.send_json(dict(type="audio_frame",sequence=sequence,pcm=base64.b64encode(bytes(2400)).decode()))
        ws.portal.call(lambda:wait_for(lambda:live[2].audio_bytes==48000))
        ws.send_json({"type":"ping"});receive(ws,"pong")
        ws.send_json({"type":"end_call"});receive(ws,"assistant_interrupted")
        assert ws.receive_json()["type"]=="ended"


def test_output_telemetry_contains_no_content_or_ack_secret(monkeypatch):
    from app.services import turn_observability as obs
    records=[]
    class Sink:
        def emit(self,record,level):records.append(record)
    monkeypatch.setattr(obs,"sink",Sink())
    out=generation();end=generated(out)
    out.acknowledge(dict(type="playback_started",**out.binding(),sequence=-1,samples=0))
    out.acknowledge(dict(type="playback_progress",**out.binding(),sequence=0,samples=1200))
    out.acknowledge(ack(out,end))
    import json
    serialized=json.dumps(records)
    assert "Disposable response" not in serialized and end["seal"] not in serialized
    assert {r["event"] for r in records}>={"realtime_first_audio","realtime_first_playback","realtime_playback_ack"}
    assert all(r["correlation"]["generation_attempt_id"]==out.claim for r in records)


def receive(ws, kind):
    for _ in range(50):
        value=ws.receive_json()
        assert value["type"] not in {"error","ended","utterance_failed"},value
        if value["type"]==kind: return value
    raise AssertionError("Missing expected event")


def begin(ws,live,item="A",previous=None):
    emit(ws,live[2],"input_committed",item_id=item,previous_item_id=previous)
    emit(ws,live[2],"input_transcript_done",item_id=item,transcript=TEXT)
    receipt=receive(ws,"transcript_final");thinking=receive(ws,"assistant_thinking")
    async def wait_prepared():
        import asyncio
        for _ in range(100):
            if any(claim==thinking["active_generation_id"] for _,claim in live[2].requests): return
            await asyncio.sleep(.02)
        raise AssertionError("L14 preparation was not used")
    ws.portal.call(wait_prepared)
    emit(ws,live[2],"response_created",response={"id":"resp_"+item,"metadata":{"generation_id":thinking["active_generation_id"]}})
    return receipt,receive(ws,"assistant_started")


def audio(ws,live,binding):
    emit(ws,live[2],"audio",response_id=binding["response_id"],item_id="output_"+binding["response_id"],content_index=0,
         delta=base64.b64encode(bytes(2400)).decode())
    return receive(ws,"assistant_audio")


def done(ws,live,binding):
    fields=dict(response_id=binding["response_id"],item_id="output_"+binding["response_id"],content_index=0)
    emit(ws,live[2],"output_transcript_done",**fields,transcript="Disposable reply.")
    emit(ws,live[2],"audio_done",**fields)
    emit(ws,live[2],"response_done",response={"id":binding["response_id"],"status":"completed"})
    return receive(ws,"assistant_audio_end")


@pytest.mark.parametrize("action",["complete","interrupt","provider_barge","post_generation","end","provider_disconnect","browser_disconnect"])
def test_socket_generation_terminal_and_next_turn(live,action):
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant);receipt,binding=begin(ws,live);audio(ws,live,binding)
        bound={k:binding[k] for k in ("session_id","generation","turn_id","active_generation_id","response_id")}
        if action in {"complete","post_generation"}:
            end=done(ws,live,binding)
            with live[1]() as db: assert db.get(ConversationTurn,receipt["turn_id"]).assistant_message_id is None
        if action=="complete":
            control=dict(type="playback_drained",**bound,sequence=end["sequence"],samples=end["samples"],seal=end["seal"])
            ws.send_json(control);receive(ws,"assistant_completed");ws.send_json(control)
        elif action in {"interrupt","provider_barge","post_generation"}:
            if action=="provider_barge":emit(ws,live[2],"speech_started",item_id="B")
            else:ws.send_json(dict(type="interrupt",**bound))
            receive(ws,"assistant_interrupted")
            if action!="post_generation": assert binding["response_id"] in live[2].cancelled_responses
            emit(ws,live[2],"audio",response_id=binding["response_id"],delta="AAA=")
            emit(ws,live[2],"output_transcript_done",response_id=binding["response_id"],transcript="Late")
            next_receipt,next_binding=begin(ws,live,"B","A")
            assert next_receipt["turn_id"]>receipt["turn_id"]
            ws.send_json(dict(type="interrupt",**bound)) # stale interruption must not kill B
            ws.send_json({"type":"ping"});receive(ws,"pong")
            with live[1]() as db: assert db.get(ConversationTurn,next_receipt["turn_id"]).state=="streaming"
        elif action=="provider_disconnect":
            ws.portal.call(lambda: live[2].events.put_nowait(sessions.RealtimeError("realtime_provider_connection")))
            assert ws.receive_json()["type"]=="error"
        elif action=="browser_disconnect":
            ws.close()
        if action not in {"browser_disconnect","provider_disconnect"}: ws.send_json({"type":"end_call"})
        for _ in range(5):
            if ws.receive_json()["type"]=="ended": break
        else: raise AssertionError("No clean end")
    with live[1]() as db:
        turn=db.get(ConversationTurn,receipt["turn_id"])
        assert turn.state==("completed" if action=="complete" else "interrupted")
        assert bool(turn.assistant_message_id)==(action=="complete")
        assert db.scalar(select(func.count()).select_from(ConversationTurn).where(ConversationTurn.state=="streaming"))==0
