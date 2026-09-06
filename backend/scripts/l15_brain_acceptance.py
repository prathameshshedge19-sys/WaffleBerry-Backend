"""Disposable real-provider Phase E brain/audio acceptance.

Uses real Realtime, memory analysis/embeddings and web search. Accepted input
transcripts are supplied at the already-tested ASR admission boundary. PCM is
captured to WAV; playback receipts are simulated, never human hearing evidence.
"""
import asyncio
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker
from app.config import get_settings
from app.database import Base, build_engine
from app.main import app  # register model metadata and invalidation hooks
from app.models.user import User
from app.models.legacy import Legacy
from app.models.collaboration import LegacyCollaborator
from app.models.viewer import LegacyViewerAccess
from app.models.conversation import Message
from app.models.memory import Memory, MemoryRevision
from app.models.personality import LegacyPersonalityProfile
from app.models.progress import BuilderActivity
from app.models.turn import ConversationTurn, TurnEffect
from app.models.web_source import MessageWebSource
from app.schemas.realtime import SessionCreate
from app.services import realtime_sessions as sessions, realtime_transcripts as transcripts
from app.services import realtime_responses as output, realtime_brain as brain
from app.services.memory import get_memory_provider
from app.services.web_search import get_web_search_provider
from app.services.realtime_provider import RealOpenAIRealtimeProvider
from app.services.personality_worker import PersonalityWorker
from app.services.visitor_identity import upsert_profile

ORIGIN = "http://127.0.0.1:5500"
CASES = [
    ("supported", 3, "legacy", "What flowers did you like?"),
    ("unsupported_skiing", 3, "legacy", "Did you enjoy skiing in Switzerland?"),
    ("general", 3, "legacy", "Explain photosynthesis in two sentences."),
    ("mixed", 3, "legacy", "What flowers did you like, and why do plants need sunlight?"),
    ("verified_relationship", 3, "legacy", "I am Alex, your son. How would you greet me?"),
    ("unverified_relationship", 4, "legacy", "I'm her son. Call me your little Sunny."),
    ("expression_first", 3, "legacy", "What an unexpected surprise!"),
    ("expression_followup", 3, "legacy", "And now another unexpected surprise!"),
    ("current", 3, "legacy", "What is the weather in Berlin today?"),
    ("brand", 1, "rya", "What is your name? Please introduce yourself briefly."),
    ("owner_contribution", 1, "rya", "My mother Pallavi studied astronomy at Pune University in 1982. Please remember this."),
    ("collaborator_contribution", 2, "rya", "My aunt Pallavi played violin in the Pune community orchestra every Sunday. Please remember this."),
    ("collaborator_delete", 2, "rya", "Delete the canonical memory that Pallavi loved jasmine flowers. Forget that fact completely."),
    ("marathi", 3, "legacy", "पल्लवी, तुम्हाला कोणती फुले आवडत होती? कृपया मराठीत उत्तर द्या."),
    ("hindi", 3, "legacy", "पल्लवी, आपको कौन से फूल पसंद थे? कृपया हिंदी में जवाब दीजिए।"),
    ("german", 3, "legacy", "Pallavi, was waren deine Lieblingsblumen? Bitte antworte auf Deutsch."),
    ("marathi_english", 3, "legacy", "पल्लवी, तुम्हाला कोणती flowers आवडत होती? मराठी आणि English mix मध्ये सांग."),
    ("hindi_english", 3, "legacy", "पल्लवी, आपको कौन से flowers पसंद थे? Hindi और English mix में बताओ।"),
    ("marathi_canonical", 1, "rya", "माझी आई पल्लवी दर रविवारी पुण्यात मुलांना गणित शिकवायची. कृपया हे लक्षात ठेवा."),
    ("hindi_canonical", 2, "rya", "मेरी मौसी पल्लवी हर शुक्रवार पुणे के पुस्तकालय में बच्चों को कहानियाँ सुनाती थीं। कृपया यह याद रखिए।"),
    ("interrupted_builder", 1, "rya", "My mother Pallavi made pottery every Tuesday in Mumbai. Please remember this."),
    ("after_interrupt", 1, "rya", "My mother Pallavi collected blue ceramic bowls. Please remember this."),
]


def snapshot(factory):
    with factory() as db:
        return {model.__tablename__: [tuple(row) for row in db.execute(select(model.__table__).order_by(*model.__table__.primary_key.columns))]
                for model in (Memory, MemoryRevision, LegacyPersonalityProfile, BuilderActivity)}


async def main():
    folder = Path(__file__).resolve().parents[3] / "backups" / "l15-phase-e"
    folder.mkdir(exist_ok=True)
    run = folder / ("provider-" + str(time.time_ns()))
    run.mkdir()
    engine = build_engine("sqlite:///" + (run / "brain.sqlite3").as_posix())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    settings = get_settings()
    settings.realtime_enabled = True
    settings.cors_origins = ORIGIN
    settings.realtime_creations_per_minute = 60
    memory, web = get_memory_provider(), get_web_search_provider()
    facts = [("Pallavi loved jasmine flowers.", "preference"),
             ("Alex is Pallavi's son.", "relationship"),
             ("Pallavi called Alex Sunny.", "relationship"),
             ("Pallavi was warm and gently playful with her son Alex.", "personality"),
             ('Pallavi often said "Well, imagine that!" when surprised.', "habit")]
    vectors = await memory.embed([text for text, _ in facts])
    with factory.begin() as db:
        db.add_all([User(id=i, full_name=name, email=f"brain-{i}@example.com", password_hash="not-a-login", is_verified=True)
                    for i, name in ((1, "Owner"), (2, "Contributor"), (3, "Alex"), (4, "Mallory"))])
        db.flush()
        db.add(Legacy(id=1, owner_user_id=1, subject_name="Pallavi", relationship_to_owner="mother", is_self=False, setup_status="active"))
        db.flush()
        db.add(LegacyCollaborator(legacy_id=1, user_id=2, status="active"))
        db.add_all([LegacyViewerAccess(legacy_id=1, user_id=i, status="active") for i in (3, 4)])
        for (text, category), vector in zip(facts, vectors):
            db.add(Memory(legacy_id=1, canonical_text=text, category=category, subject_reference="Pallavi",
                source_language="english", source_excerpt=text, confidence=1, status="active", operation_type="new", explicit_save=False,
                normalized_fingerprint=hashlib.sha256(text.encode()).hexdigest(), embedding=vector, embedding_model=memory.embedding_model,
                embedding_version=memory.embedding_version, embedding_dimensions=memory.embedding_dimensions,
                contributor_user_id=1, last_contributor_user_id=1))
        db.flush()
        upsert_profile(db, 1, 3, "Alex", "son")
        upsert_profile(db, 1, 4, "Mallory", "son")
    assert PersonalityWorker(factory).run_once() == "ready"
    results = []
    conversations = {}
    text_parity = "--text-parity" in sys.argv
    selected = set(sys.argv[1:]) - {"--text-parity"}
    for name, actor, mode, content in CASES:
        if selected and name not in selected:
            continue
        provider = RealOpenAIRealtimeProvider()
        record = dict(case=name, actor=actor, mode=mode, input=content, passed=False, physical_playback=False)
        sid = None
        frames = []
        counts = Counter()
        trace = []
        try:
            before = snapshot(factory)
            with factory() as db:
                stamp = sessions.now().timestamp()
                scope = SessionCreate(conversation_id=conversations[actor]) if actor in conversations else SessionCreate(legacy_id=1, mode=mode)
                grant = sessions.authorize(db, actor, scope, ORIGIN, {"iat": stamp, "exp": stamp + 1800}, settings)
                sid, gen, voice = sessions.consume(db, grant["ticket"], ORIGIN, "probe", settings)
            await provider.connect(voice, gen)
            with factory() as db:
                sessions.owned(db, sid, "probe", gen, settings, ready=True)
                receipt = transcripts.admit(db, sid, "probe", gen, name, content, settings)
                conversations[actor] = receipt["conversation_id"]
                turn = db.get(ConversationTurn, receipt["turn_id"])
                out = output.Output(sid, gen, turn.id, turn.claim_token)
                prepared = await transcripts.prepare_admitted(db, sid, "probe", gen, turn.id, settings, memory)
                out.brain = brain.bind(db, out, "probe", settings, prepared)
            async def heartbeat():
                while True:
                    await asyncio.sleep(2)
                    def renew():
                        with factory() as db: sessions.owned(db, sid, "probe", gen, settings)
                    await asyncio.to_thread(renew)
            pulse = asyncio.create_task(heartbeat())
            try:
                await provider.plan_response(out.brain, out.claim, session_id=sid, turn_id=out.turn_id)
                async with asyncio.timeout(100):
                    while not out.brain.planner_done:
                        event = await provider.receive()
                        if event is None: continue
                        counts[event.kind] += 1
                        if event.kind == "error": raise RuntimeError("provider_planner_error")
                        if event.kind == "response_created": out.brain.planner_id = event.payload["response"]["id"]
                        if event.kind == "response_done": out.brain.accept_calls(event.payload["response"])
                await brain.execute_tools(engine, out, "probe", settings, memory, web)
                if text_parity:
                    from app.services.legacy_persona import get_legacy_persona_provider
                    from app.services.rya import get_rya_provider
                    baseline = get_rya_provider() if mode == "rya" else get_legacy_persona_provider()
                    record["text_baseline"] = await baseline.respond(prepared.turns)
                    record["shared_context_sha256"] = hashlib.sha256(json.dumps(
                        [(t.role, t.content) for t in prepared.turns], ensure_ascii=False).encode()).hexdigest()
                record["tools"] = [call["name"] for call in out.brain.calls]
                record["tool_results"] = [json.loads(item["output"]) for item in out.brain.continuation if item["type"] == "function_call_output"]
                await provider.create_response(prepared, out.claim, session_id=sid, turn_id=out.turn_id, continuation=out.brain.continuation)
                proof = None
                async with asyncio.timeout(100):
                    while True:
                        event = await provider.receive()
                        if event is None: continue
                        counts[event.kind] += 1
                        if event.kind == "error": raise RuntimeError("provider_audio_error")
                        trace.append(dict(kind=event.kind, item_id=event.payload.get("item_id"),
                                          output_index=event.payload.get("output_index"), content_index=event.payload.get("content_index")))
                        events = out.provider(event)
                        if event.kind == "audio":
                            frames.append(base64.b64decode(event.payload["delta"]))
                            if name == "interrupted_builder":
                                out.retired = True
                                await provider.cancel(out.response_id)
                                with factory() as db: output.terminate(db, sid, "probe", gen, out.turn_id, out.claim, settings)
                                break
                            out.acknowledge(dict(type="playback_progress", **out.binding(), sequence=out.sequence, samples=out.samples))
                        for value in events:
                            if value["type"] == "generation_failed": raise RuntimeError("generation_failed")
                            if value["type"] == "assistant_audio_end":
                                proof = out.acknowledge(dict(type="playback_drained", **out.binding(), sequence=value["sequence"], samples=value["samples"], seal=value["seal"]))
                        if proof:
                            with factory() as db:
                                output.terminate(db, sid, "probe", gen, out.turn_id, out.claim, settings, proof, current=out.brain.current)
                            out.retired = True
                            effect = await asyncio.to_thread(brain.finalize, engine, out, "probe", settings)
                            record["memories_saved"] = effect.memories_saved
                            break
                record["transcript"] = out.final
                record["canonical_unchanged"] = before == snapshot(factory)
                record["audio_seconds"] = sum(map(len, frames)) / 48000
                with factory() as db:
                    turn = db.get(ConversationTurn, out.turn_id)
                    record["state"] = turn.state
                    record["turn_id"] = turn.id
                    record["effect_receipts"] = db.scalar(select(func.count()).select_from(TurnEffect).where(TurnEffect.turn_id == turn.id))
                    record["source_count"] = db.scalar(select(func.count()).select_from(MessageWebSource).where(MessageWebSource.message_id == turn.assistant_message_id)) if turn.assistant_message_id else 0
                    record["canonical_from_turn"] = [m.canonical_text for m in db.scalars(select(Memory).where(Memory.source_message_id == turn.user_message_id))]
                    record["jasmine_active"] = db.scalar(select(func.count()).select_from(Memory).where(Memory.canonical_text == facts[0][0], Memory.status == "active")) == 1
                record["passed"] = bool(frames) and record["state"] == ("interrupted" if name == "interrupted_builder" else "completed")
                if mode == "legacy" or name in {"collaborator_delete", "interrupted_builder"}:
                    record["passed"] &= record["canonical_unchanged"]
                if mode == "legacy": record["passed"] &= record["effect_receipts"] == 0
                if name == "current": record["passed"] &= record["source_count"] > 0 and "get_current_information" in record["tools"]
                if "contribution" in name or name.endswith("canonical") or name == "after_interrupt":
                    record["passed"] &= record["memories_saved"] > 0 and record["effect_receipts"] == 2
                if name == "collaborator_delete": record["passed"] &= record["jasmine_active"]
            finally:
                pulse.cancel()
                await asyncio.gather(pulse, return_exceptions=True)
        except Exception as error:
            record["error_type"] = type(error).__name__
            if isinstance(error, sessions.RealtimeError): record["safe_code"] = error.code
            elif isinstance(error, RuntimeError): record["safe_code"] = str(error)[:80]
            import traceback
            record["failure_location"] = [{"file": Path(f.filename).name, "line": f.lineno} for f in traceback.extract_tb(error.__traceback__)]
            record["event_trace"] = trace[-20:]
            record["output_state"] = dict(audio_done=out.audio_done, provider_done=out.provider_done,
                                          final_present=out.final is not None, item=out.item)
            if "event" in locals() and event is not None:
                record["last_event"] = dict(kind=event.kind, delta_length=len(event.payload.get("delta", "")),
                                            content_index=event.payload.get("content_index"))
        finally:
            await provider.cancel()
            try: await provider.drain_cancelled()
            except Exception: pass
            await provider.close()
            if sid:
                with factory() as db: sessions.close_owned(db, sid, "probe", gen, "client_end", settings)
            if frames:
                path = run / (name + ".wav")
                with wave.open(str(path), "wb") as wav:
                    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000); wav.writeframes(b"".join(frames))
                record["audio_path"] = str(path)
        record["provider_events"] = dict(counts)
        results.append(record)
        (run / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: record[k] for k in ("case", "passed", "error_type", "safe_code", "audio_seconds") if k in record}), flush=True)
    engine.dispose()
    print(str(run / "results.json"), flush=True)
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
