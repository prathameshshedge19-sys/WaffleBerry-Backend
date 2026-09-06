import asyncio
import base64
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select

from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.realtime_session import RealtimeSession as Live
from app.models.turn import ConversationTurn, TurnEffect
from app.services import realtime_sessions as sessions, realtime_transcripts as transcripts
from app.services.conversation_turns import TurnActorContext, prepare_turn
from app.services.realtime_provider import ProviderEvent
from tests.conftest import FakeMemoryProvider
from tests.test_conversation_turns_l14 import canonical_snapshot
from tests.test_realtime_l15 import live, create, authenticate, connected, headers, ORIGIN

TEXT = "My mother loved jasmine flowers."


def leased(live, actor=1, **scope):
    grant = create(live, actor, **scope).json()
    with live[1]() as db:
        sid, gen, _ = sessions.consume(db, grant["ticket"], ORIGIN, "worker", live[3])
        sessions.owned(db, sid, "worker", gen, live[3], ready=True)
    return sid, gen


def emit(ws, fake, kind, **payload):
    ws.portal.call(lambda: fake.emit(kind, payload))


def input_event(ws):
    # Phase C assertions remain about admission; Phase D output has its own tests.
    while True:
        value = ws.receive_json()
        if value["type"] not in {"assistant_thinking", "assistant_interrupted"}:
            return value


def ordered(order, kind, item="A", **payload):
    return order.observe(ProviderEvent(kind, 1, {"item_id": item, **payload}))


def test_committed_order_over_completion_order_and_duplicate():
    order = transcripts.TranscriptOrder(1)
    ordered(order, "input_committed", previous_item_id=None)
    ordered(order, "input_committed", "B", previous_item_id="A")
    ordered(order, "input_transcript_done", "B", transcript="Second.")
    assert list(order.ready()) == []
    ordered(order, "input_transcript_done", transcript="First.")
    assert list(order.ready()) == [("A", "First.", False), ("B", "Second.", False)]
    ordered(order, "input_transcript_done", transcript="First.")
    assert list(order.ready()) == []
    with pytest.raises(sessions.RealtimeError, match="conflict"):
        ordered(order, "input_transcript_done", transcript="Changed.")


def test_uncommitted_final_missing_predecessor_and_stale_generation():
    order = transcripts.TranscriptOrder(1)
    ordered(order, "input_transcript_done", "B", transcript="Second")
    ordered(order, "input_committed", "B", previous_item_id="A")
    order.observe(ProviderEvent("input_committed", 0, {"item_id": "A", "previous_item_id": None}))
    assert list(order.ready()) == []
    ordered(order, "input_committed", previous_item_id=None)
    ordered(order, "input_transcript_failed")
    assert list(order.ready()) == [("A", None, True), ("B", "Second", False)]


def test_queue_and_metadata_are_bounded():
    order = transcripts.TranscriptOrder(1, depth=2)
    ordered(order, "input_transcript_delta", delta="one")
    ordered(order, "input_transcript_delta", "B", delta="two")
    with pytest.raises(sessions.RealtimeError, match="overrun"):
        ordered(order, "input_transcript_delta", "C", delta="three")


@pytest.mark.parametrize("bad", [None, 3, "x" * 8001, "a\x00b"])
def test_invalid_transcripts(bad):
    with pytest.raises(sessions.RealtimeError): transcripts.normalize(bad)


@pytest.mark.parametrize("actor,mode,role", [(1,"rya","owner"),(2,"rya","collaborator"),(3,"legacy","viewer")])
def test_atomic_final_scope_parity_and_zero_canonical_effects(live, actor, mode, role):
    before = canonical_snapshot(live[1])
    sid, gen = leased(live, actor, legacy_id=1, mode=mode)
    with live[1]() as db:
        original = db.scalar(select(func.count()).select_from(Conversation))
        result = transcripts.admit(db, sid, "worker", gen, "A", "  My mother\n loved  jasmine flowers. ", live[3])
        assert db.scalar(select(func.count()).select_from(Conversation)) == original + 1
        turn = db.get(ConversationTurn, result["turn_id"])
        assert turn.input_mode == "realtime_voice" and turn.state == "streaming" and turn.claim_token
        assert turn.actor_user_id == actor and turn.mode == mode and turn.legacy_id == 1
        assert turn.assistant_message_id is None
        assert db.get(Live, sid).conversation_id == result["conversation_id"]
        assert db.scalar(select(func.count()).select_from(TurnEffect)) == 0
        prepared = asyncio.run(transcripts.prepare_admitted(db, sid, "worker", gen, turn.id, live[3], FakeMemoryProvider()))
        conversation = db.get(Conversation, result["conversation_id"])
        legacy = db.get(Legacy, 1)
        message = db.get(Message, result["message_id"])
        text_actor = TurnActorContext.from_authorized(conversation, legacy, message, actor_id=actor, role=role, input_mode="text")
        expected = asyncio.run(prepare_turn(db, text_actor, conversation, legacy, message, FakeMemoryProvider()))
        assert [(t.role,t.content) for t in prepared.turns] == [(t.role,t.content) for t in expected.turns]
        assert replace(prepared.actor, input_mode="text") == expected.actor
        assert prepared.actor.role == role
        db.rollback()
    assert canonical_snapshot(live[1]) == before
    prefix = "legacy-conversations" if mode == "legacy" else "conversations"
    history = live[0].get(f"/api/v1/{prefix}/{result['conversation_id']}/messages?legacy_id=1", headers=headers(actor))
    assert history.status_code == 200 and [m["content"] for m in history.json()] == [TEXT]


def test_duplicate_conflict_stale_and_existing_binding(live):
    sid, gen = leased(live, conversation_id=1)
    with live[1]() as db:
        first = transcripts.admit(db, sid, "worker", gen, "A", TEXT, live[3])
        again = transcripts.admit(db, sid, "worker", gen, "A", TEXT, live[3])
        assert again["message_id"] == first["message_id"] and again["replayed"]
        assert first["conversation_id"] == 1
        with pytest.raises(sessions.RealtimeError, match="conflict"):
            transcripts.admit(db, sid, "worker", gen, "A", "Different", live[3])
        with pytest.raises(sessions.RealtimeError, match="access_changed"):
            transcripts.admit(db, sid, "worker", gen - 1, "A", TEXT, live[3])
        with pytest.raises(sessions.RealtimeError, match="busy"):
            transcripts.admit(db, sid, "worker", gen, "B", "Second", live[3])
        assert db.scalar(select(func.count()).select_from(ConversationTurn)) == 1


def test_failed_message_insert_rolls_back_new_chat_binding_and_turn(live):
    sid, gen = leased(live)
    def fail(*_): raise RuntimeError("Injected insert failure")
    with live[1]() as db:
        count = db.scalar(select(func.count()).select_from(Conversation))
        event.listen(Message, "before_insert", fail)
        try:
            with pytest.raises(RuntimeError): transcripts.admit(db, sid, "worker", gen, "A", TEXT, live[3])
        finally: event.remove(Message, "before_insert", fail)
        assert db.get(Live, sid).conversation_id is None
        assert db.scalar(select(func.count()).select_from(Conversation)) == count
        assert db.scalar(select(func.count()).select_from(ConversationTurn)) == 0


def test_ws_pcm_partial_final_busy_and_reconcile(live):
    client, factory, fake, _ = live
    before = canonical_snapshot(factory)
    grant = create(live).json()
    with authenticate(client, grant) as ws:
        connected(ws, grant)
        ws.send_json({"type":"audio_frame","sequence":0,"pcm":base64.b64encode(bytes(2400)).decode()})
        ws.send_json({"type":"ping"})
        assert input_event(ws)["type"] == "pong" and fake.audio_bytes == 2400
        emit(ws,fake,"input_transcript_delta",item_id="A",delta="My mother")
        assert input_event(ws)["type"] == "transcript_provisional"
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(ConversationTurn)) == 0
            assert db.get(Live,grant["session_id"]).conversation_id is None
        emit(ws,fake,"input_committed",item_id="A",previous_item_id=None)
        emit(ws,fake,"input_committed",item_id="B",previous_item_id="A")
        emit(ws,fake,"input_transcript_done",item_id="B",transcript="Second utterance")
        emit(ws,fake,"input_transcript_done",item_id="A",transcript=TEXT)
        first = input_event(ws)
        assert first["type"] == "transcript_final" and first["content"] == TEXT
        assert input_event(ws)["code"] == "realtime_turn_busy"
        emit(ws,fake,"input_transcript_done",item_id="A",transcript=TEXT)
        ws.send_json({"type":"ping"})
        assert input_event(ws)["type"] == "pong"
        ws.close()
        assert input_event(ws)["reason"] == "browser_disconnect"
    with factory() as db:
        assert db.get(ConversationTurn,first["turn_id"]).state == "interrupted"
        replacement = sessions.reconnect(db,grant["session_id"],1,ORIGIN,live[3])
    with authenticate(client,replacement) as ws:
        connected(ws,replacement)
        again = input_event(ws)
        assert again["message_id"] == first["message_id"] and again["replayed"]
        ws.send_json({"type":"end_call"})
        assert input_event(ws)["type"] == "ended"
    assert canonical_snapshot(factory) == before


@pytest.mark.parametrize("case", ["empty", "failure", "provisional", "silence"])
def test_silence_failure_and_unfinished_shutdown_never_persist(live, case):
    grant = create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        if case != "silence":
            emit(ws,live[2],"input_transcript_delta",item_id="A",delta="Unfinished")
            assert input_event(ws)["type"] == "transcript_provisional"
        if case in {"empty","failure"}:
            emit(ws,live[2],"input_committed",item_id="A",previous_item_id=None)
            emit(ws,live[2],"input_transcript_done" if case == "empty" else "input_transcript_failed",item_id="A",transcript=" \n ")
            assert input_event(ws)["type"] == "utterance_failed"
        ws.send_json({"type":"end_call"})
        if case == "provisional": assert input_event(ws)["code"] == "realtime_unfinished_speech"
        assert input_event(ws)["type"] == "ended"
    with live[1]() as db:
        assert db.get(Live,grant["session_id"]).conversation_id is None
        assert db.scalar(select(func.count()).select_from(ConversationTurn)) == 0


@pytest.mark.parametrize("frame", [
    {"type":"transcript_final","content":TEXT},
    {"type":"audio_frame","sequence":0,"pcm":"!bad"},
    {"type":"audio_frame","sequence":1,"pcm":"AAA="},
    {"type":"audio_frame","sequence":0,"pcm":"AA=="},
    {"type":"audio_frame","sequence":0,"pcm":"AAA=","conversation_id":2},
])
def test_browser_cannot_forge_final_or_invalid_audio(live, frame):
    grant = create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant); ws.send_json(frame)
        assert input_event(ws)["code"] == "realtime_protocol_error"
    with live[1]() as db: assert db.scalar(select(func.count()).select_from(ConversationTurn)) == 0


def test_crash_sweep_releases_user_only_claim_without_effects(live):
    sid,gen=leased(live)
    with live[1]() as db:
        result=transcripts.admit(db,sid,"worker",gen,"A",TEXT,live[3])
        db.get(Live,sid).lease_expires_at=sessions.now()-timedelta(seconds=1)
        db.commit();sessions.sweep(db,live[3])
        assert db.get(ConversationTurn,result["turn_id"],populate_existing=True).state=="interrupted"
        assert db.get(Message,result["message_id"]).content==TEXT


def test_text_waits_for_live_claim_then_works_after_end(live):
    sid,gen=leased(live,conversation_id=1)
    with live[1]() as db: transcripts.admit(db,sid,"worker",gen,"A",TEXT,live[3])
    path="/api/v1/conversations/1/messages?legacy_id=1"
    assert live[0].post(path,headers=headers(),json={"content":"Continue","client_turn_id":"typed"}).status_code==409
    with live[1]() as db: sessions.close_owned(db,sid,"worker",gen,"client_end",live[3])
    assert live[0].post(path,headers=headers(),json={"content":"Continue","client_turn_id":"typed"}).status_code==201


def test_final_during_bounded_shutdown_is_admitted_once(live):
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        emit(ws,live[2],"input_transcript_delta",item_id="A",delta="My mother")
        assert input_event(ws)["type"]=="transcript_provisional"
        ws.send_json({"type":"end_call"})
        emit(ws,live[2],"input_committed",item_id="A",previous_item_id=None)
        emit(ws,live[2],"input_transcript_done",item_id="A",transcript=TEXT)
        assert input_event(ws)["type"]=="transcript_final"
        assert input_event(ws)["type"]=="ended"
    with live[1]() as db:
        assert db.scalar(select(func.count()).select_from(ConversationTurn))==1


def test_asr_failure_preserves_prior_final(live):
    grant=create(live).json()
    with authenticate(live[0],grant) as ws:
        connected(ws,grant)
        emit(ws,live[2],"input_committed",item_id="A",previous_item_id=None)
        emit(ws,live[2],"input_transcript_done",item_id="A",transcript=TEXT)
        result=input_event(ws)
        emit(ws,live[2],"input_committed",item_id="B",previous_item_id="A")
        emit(ws,live[2],"input_transcript_failed",item_id="B")
        assert input_event(ws)["code"]=="realtime_transcription_failed"
        ws.send_json({"type":"end_call"})
        assert input_event(ws)["type"]=="ended"
    with live[1]() as db:
        assert db.get(Message,result["message_id"]).content==TEXT
        assert db.scalar(select(func.count()).select_from(ConversationTurn))==1


def test_reconnect_can_recover_expired_lease_without_sweeper(live):
    sid,gen=leased(live)
    with live[1]() as db:
        result=transcripts.admit(db,sid,"worker",gen,"A",TEXT,live[3])
        db.get(Live,sid).lease_expires_at=sessions.now()-timedelta(seconds=1)
        db.commit()
        sessions.reconnect(db,sid,1,ORIGIN,live[3])
        assert db.get(ConversationTurn,result["turn_id"],populate_existing=True).state=="interrupted"
