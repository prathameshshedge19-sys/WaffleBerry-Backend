"""Explicit real ASR acceptance through the application WebSocket; disposable data.

Uses synthesized fixtures, NOT human-microphone acceptance. All credentials stay
server-side. Never touches the configured application database or production.
"""
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys
import threading
import time
import wave
import base64

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from openai import OpenAI
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from app.config import get_settings
from app.database import Base, build_engine, get_db
from app.main import app
from app.models.conversation import Message
from app.models.legacy import Legacy
from app.models.realtime_session import RealtimeSession as Live
from app.models.turn import ConversationTurn, TurnEffect
from app.models.user import User
from app.services.memory import MemoryAnalysis
from app.services.realtime_provider import RealOpenAIRealtimeProvider, get_realtime_provider
from app.services.realtime_transcripts import prepare_admitted
from app.services.security import create_access_token

SAMPLES = {
    "english": "My mother loved jasmine flowers.",
    "marathi": "\u092e\u093e\u091d\u094d\u092f\u093e \u0906\u0908\u0932\u093e \u092e\u094b\u0917\u0931\u094d\u092f\u093e\u091a\u0940 \u092b\u0941\u0932\u0947 \u0906\u0935\u0921\u093e\u092f\u091a\u0940.",
    "hindi_english": "\u092e\u0947\u0930\u0940 \u092e\u093e\u0901 \u0915\u094b jasmine flowers \u092c\u0939\u0941\u0924 \u092a\u0938\u0902\u0926 \u0925\u0947\u0964",
    "german": "Meine Mutter liebte Jasminbl\u00fcten.",
}


class PreparationFixture:
    """Only grounding dependencies are deterministic; real production core runs."""
    model = "acceptance-fixture"
    embedding_model = "acceptance-fixture"
    embedding_version = "fixture-v1"
    embedding_dimensions = 4

    async def analyze(self, legacy, source_text, existing_memories=()):
        return MemoryAnalysis(source_language="unspecified", normalized_query=source_text, memories=[])

    async def embed(self, texts):
        return [[1., 0., 0., 0.] for _ in texts]


class ObservedProvider(RealOpenAIRealtimeProvider):
    def __init__(self):
        super().__init__()
        self.counts = Counter()
        self.identities = {}

    async def receive(self):
        event = await super().receive()
        if event:
            self.counts[event.kind] += 1
            identity = event.payload.get("item_id")
            if identity:
                self.identities.setdefault(identity, []).append(event.kind)
        return event


def main():
    settings = get_settings()
    folder = Path(__file__).resolve().parents[3] / "backups" / "l15-phase-c"
    folder.mkdir(parents=True, exist_ok=True)
    database = folder / ("provider-disposable-" + str(time.time_ns()) + ".sqlite3")
    engine = build_engine("sqlite:///" + database.as_posix())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory.begin() as db:
        db.add(User(id=1, full_name="Disposable acceptance", email="l15-disposable@example.com", password_hash="not-a-login", is_verified=True))
        db.flush()
        db.add(Legacy(id=1, owner_user_id=1, subject_name="Disposable mother", relationship_to_owner="mother", setup_status="active", is_self=False))
    def get_test_db():
        with factory() as db: yield db
    app.dependency_overrides[get_db] = get_test_db
    original_flag, original_origins = settings.realtime_enabled, settings.cors_origins
    settings.realtime_enabled = True
    settings.cors_origins = "http://localhost:5500"
    headers = {"Authorization": "Bearer " + create_access_token(1), "Origin": "http://localhost:5500"}
    tts = OpenAI(api_key=settings.openai_api_key)
    results = []
    try:
        with TestClient(app) as client:
            for language, source in SAMPLES.items():
                result = {"language": language, "fixture": "synthetic disposable speech", "model": settings.realtime_model,
                          "transcription_model": settings.realtime_transcription_model, "passed": False}
                provider = ObservedProvider()
                app.dependency_overrides[get_realtime_provider] = lambda: provider
                stop = threading.Event()
                pump = None
                try:
                    audio_path = folder / (language + ".wav")
                    if not audio_path.exists():
                        audio = tts.audio.speech.create(model=settings.voice_tts_model, voice="marin", input=source, response_format="wav")
                        audio_path.write_bytes(audio.content)
                    with wave.open(str(audio_path), "rb") as wav:
                        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
                        pcm = wav.readframes(24000 * 15 + 1)
                        assert 0 < len(pcm) <= 24000 * 15 * 2
                    issued = client.post("/api/v1/realtime/sessions", headers=headers, json={"legacy_id":1,"mode":"rya"})
                    assert issued.status_code == 201
                    grant = issued.json()
                    with client.websocket_connect("/api/v1/realtime/connect", headers={"Origin":headers["Origin"]}) as ws:
                        ws.send_json({"type":"authenticate","ticket":grant["ticket"]})
                        assert ws.receive_json()["type"] == "connecting"
                        assert ws.receive_json()["type"] == "ready"
                        def send_audio():
                            data = pcm + bytes(24000 * 2 * 16)
                            for sequence, offset in enumerate(range(0, len(data), 2400)):
                                if stop.is_set(): return
                                ws.send_json({"type":"audio_frame","sequence":sequence,"pcm":base64.b64encode(data[offset:offset+2400]).decode()})
                                if stop.wait(.05): return
                            ws.send_json({"type":"end_call"})
                        pump = threading.Thread(target=send_audio, daemon=True)
                        pump.start()
                        provisional = 0
                        while True:
                            event = ws.receive_json()
                            if event["type"] == "transcript_provisional": provisional += 1
                            if event["type"] in {"error", "utterance_failed", "ended"}: raise RuntimeError(event.get("code", "no_final"))
                            if event["type"] == "transcript_final": break
                        stop.set(); pump.join(timeout=3)
                        result["provisional_events"] = provisional
                        result["accepted_text"] = event["content"]  # This is only the declared disposable sample.
                        with factory() as db:
                            live = db.get(Live, grant["session_id"])
                            prepared = asyncio.run(prepare_admitted(db, live.id, live.lease_owner, live.connection_generation,
                                event["turn_id"], settings, PreparationFixture()))
                            result["l14_preparation"] = prepared.actor.input_mode == "realtime_voice" and prepared.turns[-1].content == event["content"]
                            result["turn_count"] = db.scalar(select(func.count()).select_from(ConversationTurn).where(ConversationTurn.conversation_id==event["conversation_id"]))
                            result["message_count"] = db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id==event["conversation_id"]))
                            result["effect_count"] = db.scalar(select(func.count()).select_from(TurnEffect))
                        history = client.get(f"/api/v1/conversations/{event['conversation_id']}/messages?legacy_id=1",headers=headers)
                        result["normal_chat_refresh"] = history.status_code == 200 and history.json()[0]["content"] == event["content"]
                        ws.send_json({"type":"end_call"})
                        assert ws.receive_json()["type"] == "ended"
                    result["matching_committed_identity"] = any("input_committed" in kinds and "input_transcript_done" in kinds and "input_transcript_delta" in kinds for kinds in provider.identities.values())
                    result["passed"] = (provisional > 0 and result["l14_preparation"] and result["normal_chat_refresh"]
                        and result["turn_count"] == result["message_count"] == 1 and result["effect_count"] == 0
                        and result["matching_committed_identity"] and not provider.counts["response_created"])
                except Exception as error:
                    result["error_type"] = type(error).__name__  # No provider payload/credential/error repr.
                finally:
                    stop.set()
                    if pump: pump.join(timeout=3)
                    result["provider_events"] = dict(provider.counts)
                results.append(result)
                print(json.dumps(result), flush=True)
    finally:
        settings.realtime_enabled, settings.cors_origins = original_flag, original_origins
        app.dependency_overrides.clear()
        engine.dispose()
    report = {"results":results,"disposable_database":str(database),"human_microphone_verified":False,
              "quality_claim":"One synthesized sample per language; no equal-quality or human microphone claim."}
    (Path(__file__).resolve().parents[1]/"docs"/"L15_PHASE_C_PROVIDER_ACCEPTANCE.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
