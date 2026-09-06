"""Real provider + L14 + simulated playback receipts, never human acceptance.

Uses only a newly created disposable SQLite database and synthesized fixture.
No audio is sent to a physical output device by this probe.
"""
import base64
from collections import Counter
import json
from pathlib import Path
import sys
import threading
import time
import wave

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker
from app.config import get_settings
from app.database import Base, build_engine, get_db
from app.main import app
from app.models.user import User
from app.models.legacy import Legacy
from app.models.conversation import Message
from app.models.turn import ConversationTurn, TurnEffect
from app.services.memory import get_memory_provider
from app.services.realtime_provider import RealOpenAIRealtimeProvider, get_realtime_provider
from app.services.security import create_access_token


class Observed(RealOpenAIRealtimeProvider):
    def __init__(self):
        super().__init__();self.counts=Counter();self.cancel_requests=0;self.errors=[]
    async def receive(self):
        event=await super().receive()
        if event:self.counts[event.kind]+=1
        return event
    async def _send(self,event):
        if event["type"]=="response.cancel":self.cancel_requests+=1
        return await super()._send(event)


def main():
    folder=Path(__file__).resolve().parents[3]/"backups"/"l15-phase-e";folder.mkdir(exist_ok=True)
    database=folder/("provider-output-"+str(time.time_ns())+".sqlite3")
    engine=build_engine("sqlite:///"+database.as_posix());Base.metadata.create_all(engine)
    factory=sessionmaker(bind=engine,expire_on_commit=False,autoflush=False)
    with factory.begin() as db:
        db.add(User(id=1,full_name="Disposable",email="output@example.com",password_hash="not-a-login",is_verified=True))
        db.flush();db.add(Legacy(id=1,owner_user_id=1,subject_name="Disposable mother",relationship_to_owner="mother",setup_status="active",is_self=False))
    def database_dependency():
        with factory() as db:yield db
    settings=get_settings();settings.realtime_enabled=True;settings.cors_origins="http://127.0.0.1:5500";settings.realtime_creations_per_minute=60
    app.dependency_overrides[get_db]=database_dependency
    memory_provider=get_memory_provider()
    app.dependency_overrides[get_memory_provider]=lambda:memory_provider
    headers={"Origin":"http://127.0.0.1:5500","Authorization":"Bearer "+create_access_token(1)}
    with wave.open(str(folder.parent/"l15-phase-c"/"english.wav"),"rb") as wav:
        assert (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())==(1,2,24000)
        pcm=wav.readframes(24000*15)
    results=[]
    try:
        with TestClient(app) as client:
            for scenario,voice in [("normal","marin"),("normal","cedar"),("barge_in","marin"),
                                   ("post_generation","marin"),("stop","cedar"),("end","marin")]:
                with factory.begin() as db:db.get(User,1).voice_preference=voice
                provider=Observed();app.dependency_overrides[get_realtime_provider]=lambda:provider
                result={"scenario":scenario,"voice":voice,"passed":False,"physical_playback":False}
                stop=threading.Event();pumps=[];turn_id=None;audio_count=0;end=None;played=False
                second_speech=threading.Event();barge_binding=None;next_turn_id=None
                try:
                    issued=client.post("/api/v1/realtime/sessions",headers=headers,json={"legacy_id":1,"mode":"rya"})
                    assert issued.status_code==201
                    with client.websocket_connect("/api/v1/realtime/connect",headers={"Origin":headers["Origin"]}) as ws:
                        ws.send_json({"type":"authenticate","ticket":issued.json()["ticket"]})
                        assert ws.receive_json()["type"]=="connecting"
                        assert ws.receive_json()["type"]=="ready"
                        def microphone():
                            sequence=0;second_offset=None
                            while not stop.is_set():
                                if second_speech.is_set() and second_offset is None:
                                    second_offset=0
                                    ws.send_json(dict(type="interrupt",**barge_binding))
                                if second_offset is not None:
                                    frame=pcm[second_offset:second_offset+2400] if second_offset<len(pcm) else bytes(2400)
                                    second_offset+=2400
                                else:
                                    frame=pcm[sequence*2400:(sequence+1)*2400] if sequence*2400<len(pcm) else bytes(2400)
                                ws.send_json({"type":"audio_frame","sequence":sequence,"pcm":base64.b64encode(frame).decode()})
                                sequence+=1
                                if stop.wait(.05):return
                                if sequence>2000:ws.send_json({"type":"end_call"});return
                        pump=threading.Thread(target=microphone,daemon=True);pump.start();pumps.append(pump)
                        total=0;binding=None;interrupted=False;sent_end=False;scheduled=time.monotonic()
                        while True:
                            event=ws.receive_json();kind=event["type"]
                            if kind in {"error","utterance_failed"}:raise RuntimeError(event.get("code","provider_error"))
                            if kind=="transcript_final":
                                if turn_id is None:turn_id=event["turn_id"]
                                else:
                                    next_turn_id=event["turn_id"]
                                    ws.send_json({"type":"end_call"});sent_end=True
                            if kind=="assistant_audio":
                                audio_count+=1;total+=len(base64.b64decode(event["pcm"]))/2
                                binding={k:event[k] for k in ("session_id","generation","turn_id","active_generation_id","response_id")}
                                if scenario in {"barge_in","stop","end"} and not interrupted:
                                    interrupted=True
                                    if scenario=="barge_in":
                                        barge_binding=binding;second_speech.set()
                                    else:
                                        ws.send_json(dict(type="end_call") if scenario=="end" else dict(type="interrupt",**binding))
                                    sent_end=scenario=="end"
                                if scenario=="normal":
                                    scheduled=max(scheduled,time.monotonic())+len(base64.b64decode(event["pcm"]))/48000
                                    def played_frame(deadline=scheduled,sequence=event["sequence"],samples=int(total),bound=binding):
                                        if stop.wait(max(0,deadline-time.monotonic())):return
                                        ws.send_json(dict(type="playback_progress",**bound,sequence=sequence,samples=samples))
                                    player=threading.Thread(target=played_frame,daemon=True);player.start();pumps.append(player)
                            if kind=="assistant_audio_end":
                                end=event
                                if scenario=="post_generation":
                                    ws.send_json(dict(type="interrupt",**binding));interrupted=True
                                elif scenario=="normal":
                                    # Waits for the complete simulated ordered playback clock.
                                    for player in pumps[1:]:player.join(timeout=30)
                                    ws.send_json(dict(type="playback_drained",**binding,sequence=end["sequence"],samples=end["samples"],seal=end["seal"]))
                                    played=True
                            if kind in {"assistant_completed","assistant_interrupted"} and not sent_end:
                                if scenario=="barge_in":continue  # admit a second real ASR utterance in the same call
                                # Receive cancelled terminal usage before closing the provider.
                                if interrupted:time.sleep(.3)
                                ws.send_json({"type":"end_call"});sent_end=True
                            if kind=="ended":break
                        stop.set()
                    with factory() as db:
                        turn=db.get(ConversationTurn,turn_id)
                        result.update(state=turn.state,assistant_messages=int(turn.assistant_message_id is not None),
                                      messages=db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id==turn.conversation_id)),
                                      effects=db.scalar(select(func.count()).select_from(TurnEffect).where(TurnEffect.turn_id==turn.id)))
                    result.update(audio_frames=audio_count,provider_events=dict(provider.counts),cancel_requests=provider.cancel_requests,
                                  playback_receipt_simulated=played,accepted_voice=provider.accepted_configuration["audio"]["output"]["voice"],
                                  next_turn_ordered=next_turn_id is not None and next_turn_id>turn_id)
                    result["passed"]=(audio_count>0 and result["effects"]==(2 if scenario=="normal" else 0) and result["accepted_voice"]==voice and
                        result["state"]==("completed" if scenario=="normal" else "interrupted") and
                        result["assistant_messages"]==(1 if scenario=="normal" else 0) and
                        (scenario not in {"barge_in","stop"} or provider.cancel_requests>0) and
                        (scenario!="barge_in" or result["next_turn_ordered"] and provider.counts["input_transcript_done"]>=2))
                except Exception as error:
                    result["error_type"]=type(error).__name__;result["provider_events"]=dict(provider.counts)
                    if isinstance(error,RuntimeError):result["safe_code"]=str(error) if str(error).startswith("realtime_") else "probe_failed"
                finally:
                    stop.set()
                    for pump in pumps:pump.join(timeout=2)
                results.append(result);print(json.dumps(result),flush=True)
    finally:
        app.dependency_overrides.clear();engine.dispose()
    report=dict(results=results,disposable_database=str(database),human_browser_acceptance=False,
                limitation="Real provider and application socket; synthesized input and simulated playback receipts. No physical hearing/barge-in claim.")
    (Path(__file__).resolve().parents[1]/"docs"/"L15_PHASE_E_TRANSPORT_ACCEPTANCE.json").write_text(json.dumps(report,indent=2)+"\n")
    return 0 if all(r["passed"] for r in results) else 1


if __name__=="__main__":raise SystemExit(main())
