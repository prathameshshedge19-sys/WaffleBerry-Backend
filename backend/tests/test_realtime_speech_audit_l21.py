"""Independent L21.5 seam review: real controller, renderer and race boundaries."""
import asyncio
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.main import app
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.realtime_session import RealtimeSession
from app.models.voice_profile import VoiceAsset, VoiceJob, VoiceProfile
from app.models.turn import ConversationTurn
from app.services.realtime_provider import RealOpenAIRealtimeProvider
from app.services import realtime_responses as responses
from app.services.realtime_speech import (get_live_speech_renderer, LiveSpeechRenderer,
    RenderedSpeech, PreservedSpeechUnavailable, SpeechRenderCancelled)
from app.services import realtime_sessions
from app.services.media_storage import LocalSourceStorage
from app.services.voice_profiles import VoiceProfileService, utcnow
from app.services.voice_providers import FakeClonedSpeechProvider
from app.services.voice_synthesis_worker import VoiceSynthesisWorker
from app.services.voice_synthesis_manifest import test_manifest as fake_manifest
from app.services.voice_storage import VoiceStorage
from tests.test_conversation_turns_l14 import canonical_snapshot
from tests.test_realtime_brain_l15 import admitted
from tests.voice_l21_helpers import reserve, prepare, activate
from tests.test_realtime_l15 import live, authenticate, connected, create
from tests.test_realtime_responses_l15 import receive
from tests.test_realtime_speech_l21 import ExactStandard, IsolatedRenderer, SocketSpeechRenderer
from tests.test_realtime_transcripts_l15 import emit, TEXT


def answer_for(output):
    text = "मी पुण्यात राहिलो. This is the exact answer."
    return responses.AuthoritativeAnswer(text, hashlib.sha256(text.encode()).hexdigest(),
        output.turn_id, output.claim, "openai_realtime", "gpt-realtime-2.1")


def start_answer(live, ws, item="audit", previous=None):
    emit(ws, live[2], "input_committed", item_id=item, previous_item_id=previous)
    emit(ws, live[2], "input_transcript_done", item_id=item, transcript=TEXT)
    receipt = receive(ws, "transcript_final")
    thinking = receive(ws, "assistant_thinking")
    async def requested():
        for _ in range(200):
            if any(claim == thinking["active_generation_id"] for _, claim in live[2].requests):
                return
            await asyncio.sleep(.01)
        raise AssertionError("No final answer request")
    ws.portal.call(requested)
    return receipt, thinking


def freeze(live, ws, thinking, text="One final answer."):
    rid = "answer_" + thinking["active_generation_id"]
    emit(ws, live[2], "response_created", response={"id": rid,
        "metadata": {"generation_id": thinking["active_generation_id"], "phase": "answer"}})
    emit(ws, live[2], "output_text_done", response_id=rid, item_id="item_" + rid,
        content_index=0, output_index=0, text=text)
    emit(ws, live[2], "response_done", response={"id": rid, "status": "completed"})
    return rid


def end_call(ws):
    ws.send_json({"type": "end_call"})
    for _ in range(10):
        event = ws.receive_json()
        assert event["type"] != "error", event
        if event["type"] == "ended": return
    raise AssertionError("Call did not end")


def test_answer_request_preserves_all_existing_legacy_instructions_and_context():
    class Socket:
        def __init__(self): self.sent = []
        async def send(self, event): self.sent.append(json.loads(event)["response"])
    async def run():
        provider = RealOpenAIRealtimeProvider(); provider.socket = Socket()
        prepared = SimpleNamespace(actor=SimpleNamespace(mode="legacy"), turns=[
            SimpleNamespace(role="system", content="Authoritative first-person Legacy policy"),
            SimpleNamespace(role="user", content="Where did I live?"),
        ])
        continuation = [{"type": "function_call_output", "call_id": "read",
            "output": '{"ok": true, "data": "Pune"}'}]
        await provider.create_response(prepared, "claim", continuation=continuation)
        await provider.generate_authoritative_answer(prepared, "claim", continuation=continuation)
        old, new = provider.socket.sent
        old["output_modalities"] = ["text"]
        old["metadata"]["phase"] = "answer"
        assert new == old
    asyncio.run(run())


def test_cancel_during_database_admission_waits_and_cancels_the_committed_live_job():
    entered, release = threading.Event(), threading.Event()
    class DelayedAdmission(IsolatedRenderer):
        def _admit(self, *args):
            entered.set()
            assert release.wait(3)
            return "committed-live-job", "marin"
    async def run():
        renderer = DelayedAdmission(ExactStandard())
        output = responses.Output("session", 1, 1, "claim", text_first=True)
        task = asyncio.create_task(renderer.render(output, answer_for(output), "owner",
            SimpleNamespace(voice_live_synthesis_timeout_seconds=10)))
        assert await asyncio.to_thread(entered.wait, 2)
        output.retired = True
        task.cancel()
        await asyncio.sleep(.02)
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert renderer.cancelled == ["committed-live-job"]
        assert renderer.standard_provider.calls == []
    asyncio.run(run())


@pytest.mark.parametrize("mode,enabled,actor", [("rya", False, 1), ("legacy", True, 3)])
def test_silent_planner_text_remains_non_answer_output(live, monkeypatch, mode, enabled, actor):
    monkeypatch.setattr(live[3], "voice_live_enabled", enabled)
    original = live[2].plan_response
    async def plan(*args, **kwargs):
        await original(*args, **kwargs)
        created, done = live[2].events.get_nowait(), live[2].events.get_nowait()
        live[2].events.put_nowait(created)
        rid = created.payload["response"]["id"]
        live[2].emit("output_text_delta", {"response_id": rid, "delta": "Silent planning"})
        live[2].emit("output_text_done", {"response_id": rid, "text": "Silent planning"})
        live[2].events.put_nowait(done)
    monkeypatch.setattr(live[2], "plan_response", plan)
    grant = create(live, actor, legacy_id=1, mode=mode).json()
    with authenticate(live[0], grant) as ws:
        connected(ws, grant)
        start_answer(live, ws)
        assert len(live[2].requests) == 1
        end_call(ws)


def test_completed_speech_longer_than_playback_window_applies_queue_backpressure(live, monkeypatch):
    class LongRenderer(SocketSpeechRenderer):
        async def render(self, output, answer, owner, settings):
            from app.services.realtime_speech import RenderedSpeech
            self.answers.append(answer)
            return RenderedSpeech(bytes(48000 * 24), 24000, 1, answer.text_digest, "standard", 1)
        async def ensure_current(self, *args):
            # Deliberately give the producer a head start over controller I/O.
            if asyncio.current_task().get_coro().__name__ == "controller":
                await asyncio.sleep(.003)
    renderer = LongRenderer()
    app.dependency_overrides[get_live_speech_renderer] = lambda: renderer
    monkeypatch.setattr(live[3], "voice_live_enabled", True)
    grant = create(live, 3, legacy_id=1, mode="legacy").json()
    with authenticate(live[0], grant) as ws:
        connected(ws, grant)
        receipt, thinking = start_answer(live, ws)
        freeze(live, ws, thinking)
        started = receive(ws, "assistant_started")
        binding = {k: started[k] for k in ("session_id", "generation", "turn_id", "active_generation_id", "response_id")}
        for sequence in range(24):
            frame = receive(ws, "assistant_audio")
            assert frame["sequence"] == sequence
            # No credit until the full 20-second window is delivered.
            if sequence >= 19:
                ws.send_json({"type": "playback_progress", **binding,
                    "sequence": sequence, "samples": (sequence + 1) * 24000})
        end = receive(ws, "assistant_audio_end")
        with live[1]() as db:
            assert db.get(ConversationTurn, receipt["turn_id"]).assistant_message_id is None
        drain = {"type": "playback_drained", **binding, "sequence": 23,
            "samples": end["samples"], "seal": end["seal"]}
        ws.send_json(drain); receive(ws, "assistant_completed")
        ws.send_json(drain); ws.send_json({"type": "ping"}); receive(ws, "pong")
        with live[1]() as db:
            assert db.scalar(select(func.count()).select_from(Message).where(
                Message.conversation_id == receipt["conversation_id"],
                Message.role == MessageRole.ASSISTANT)) == 1
        assert len(live[2].answer_requests) == len(renderer.answers) == 1
        end_call(ws)


@pytest.fixture
def speech_world(live, monkeypatch, tmp_path):
    for key, value in {"voice_live_enabled": True, "voice_cloning_enabled": True,
            "voice_synthesis_provider": "fake"}.items():
        monkeypatch.setattr(live[3], key, value)
    source = b"synthetic-reference-fixture"
    # Generated object keys are long; keep Windows fixture paths below MAX_PATH.
    raw = LocalSourceStorage(str(tmp_path / "s"))
    with live[1]() as db:
        version = prepare(db, reserve(db), source)
        activate(db, version)
        reference = db.get(VoiceAsset, version.reference_asset_id)
        raw.put(reference.object_key, source, content_type="audio/wav")
    out, _ = admitted(live, 3, "legacy")
    out.text_first = True
    out.authoritative_answer = answer_for(out)
    out.final = out.authoritative_answer.text
    standard = ExactStandard()
    renderer = LiveSpeechRenderer(live[1], VoiceStorage(raw), standard)
    worker = VoiceSynthesisWorker(live[1], VoiceStorage(raw), settings=live[3],
        manifest=fake_manifest(), provider=FakeClonedSpeechProvider())
    worker.warmup()
    try:
        yield SimpleNamespace(live=live, output=out, answer=out.authoritative_answer,
            renderer=renderer, worker=worker, standard=standard, version=version)
    finally:
        worker.close()


def rendered_job(world):
    out, answer, renderer, live = world.output, world.answer, world.renderer, world.live
    job_id, _ = renderer._admit(out, answer, "worker", live[3])
    out.preserved_job_id = job_id
    assert world.worker.run_once() == "ready"
    state, asset_id = renderer._snapshot(out, answer, "worker", live[3], job_id)
    assert state == "succeeded"
    pcm = renderer._read_preserved(out, answer, "worker", live[3], job_id, asset_id)
    return RenderedSpeech(pcm, 24000, 1, answer.text_digest, "preserved", 1, job_id)


def test_real_renderer_worker_storage_digest_and_zero_canonical_effects(speech_world):
    w = speech_world
    before = canonical_snapshot(w.live[1])
    calls = []
    original = w.worker.provider.synthesize
    async def capture(request):
        calls.append(request)
        return await original(request)
    w.worker.provider.synthesize = capture
    async def run():
        task = asyncio.create_task(w.renderer.render(w.output, w.answer, "worker", w.live[3]))
        for _ in range(200):
            if w.output.preserved_job_id: break
            await asyncio.sleep(.01)
        assert w.output.preserved_job_id
        assert await asyncio.to_thread(w.worker.run_once) == "ready"
        return await task
    result = asyncio.run(run())
    assert result.delivery == "preserved" and result.text_digest == w.answer.text_digest
    assert len(calls) == 1
    assert calls[0].authoritative_text == w.answer.text
    assert calls[0].authoritative_text_digest == w.answer.text_digest
    assert calls[0].purpose == "live" and w.standard.calls == []
    assert canonical_snapshot(w.live[1]) == before
    with w.live[1]() as db:
        assert db.get(ConversationTurn, w.output.turn_id).assistant_message_id is None
    asyncio.run(w.renderer.cancel_current(w.output))
    with w.live[1]() as db:
        assert db.get(VoiceJob, w.output.preserved_job_id).state == "cancelled"
        assert db.scalar(select(VoiceAsset).where(VoiceAsset.job_id == w.output.preserved_job_id,
            VoiceAsset.kind == "generated")).state == "purge_pending"


@pytest.mark.parametrize("condition", ["no_profile", "cloning_disabled", "worker_failed", "storage_read"])
def test_real_renderer_same_answer_standard_paths(speech_world, monkeypatch, condition):
    w = speech_world
    if condition == "no_profile":
        with w.live[1]() as db:
            p = db.get(VoiceProfile, w.version.voice_profile_id)
            VoiceProfileService().revoke(db, 1, 1, p.revision); db.commit()
    elif condition == "cloning_disabled": monkeypatch.setattr(w.live[3], "voice_cloning_enabled", False)
    async def run():
        task = asyncio.create_task(w.renderer.render(w.output, w.answer, "worker", w.live[3]))
        if condition in {"worker_failed", "storage_read"}:
            for _ in range(200):
                if w.output.preserved_job_id: break
                await asyncio.sleep(.01)
            assert w.output.preserved_job_id
            if condition == "worker_failed":
                with w.live[1].begin() as db:
                    db.get(VoiceJob, w.output.preserved_job_id).state = "failed"
            else:
                assert await asyncio.to_thread(w.worker.run_once) == "ready"
                from app.services.media_storage import StorageError
                def fail_read(*args): raise StorageError("voice_storage_unavailable")
                monkeypatch.setattr(w.renderer.storage, "read_private", fail_read)
        return await task
    result = asyncio.run(run())
    assert result.delivery == "standard" and result.text_digest == w.answer.text_digest
    assert w.standard.calls == [(w.answer.text, "marin")]


def test_populated_live_downgrade_refuses_before_schema_change(speech_world):
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.test_voice_models_l21 import live_synthesis_migration
    w = speech_world
    job_id, _ = w.renderer._admit(w.output, w.answer, "worker", w.live[3])
    with w.live[1].kw["bind"].begin() as conn:
        context = MigrationContext.configure(conn)
        columns = sa.inspect(conn).get_columns("voice_jobs")
        with Operations.context(context), pytest.raises(RuntimeError, match="live voice jobs exist"):
            live_synthesis_migration.downgrade()
        assert [c["name"] for c in columns] == [c["name"] for c in sa.inspect(conn).get_columns("voice_jobs")]
        assert conn.execute(sa.select(VoiceJob.id).where(VoiceJob.id == job_id)).scalar_one() == job_id


def test_postgresql_offline_downgrade_guards_live_data_before_ddl():
    import io
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.test_voice_models_l21 import live_synthesis_migration
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context): live_synthesis_migration.downgrade()
    sql = output.getvalue()
    assert sql.index("RAISE EXCEPTION") < sql.index("ALTER TABLE")


def test_frozen_answer_is_immutable_and_delivery_cannot_switch():
    from dataclasses import FrozenInstanceError
    out = responses.Output("session", 1, 1, "claim", text_first=True)
    answer = answer_for(out)
    with pytest.raises(FrozenInstanceError): answer.text = "Another answer"
    out.authoritative_answer = answer
    out.start_speech("preserved")
    with pytest.raises(realtime_sessions.RealtimeError): out.start_speech("standard")


@pytest.mark.parametrize("change", ["revoke", "delete", "replace", "legacy_delete", "logout",
    "legacy_switch", "lease_takeover", "turn_retired"])
def test_real_renderer_rechecks_lifecycle_after_synthesis(speech_world, change):
    w = speech_world
    rendered = rendered_job(w)
    with w.live[1]() as db:
        profile = db.get(VoiceProfile, w.version.voice_profile_id)
        if change == "revoke": VoiceProfileService().revoke(db, 1, 1, profile.revision)
        elif change == "delete": VoiceProfileService().delete(db, 1, 1, profile.revision)
        elif change == "replace":
            version = prepare(db, reserve(db, revision=profile.revision))
            activate(db, version)
        elif change == "legacy_delete": db.get(Legacy, 1).deletion_requested_at = utcnow()
        elif change == "logout": realtime_sessions.revoke(db, 3)
        elif change == "legacy_switch":
            db.get(Conversation, w.output.brain.prepared.actor.conversation_id).legacy_id = 2
        elif change == "lease_takeover":
            db.get(RealtimeSession, w.output.session_id).connection_generation += 1
        else:
            turn = db.get(ConversationTurn, w.output.turn_id)
            turn.state, turn.finished_at, turn.safe_error_code = "interrupted", utcnow(), "cancelled"
        db.commit()
    with pytest.raises((PreservedSpeechUnavailable, SpeechRenderCancelled, realtime_sessions.RealtimeError)):
        asyncio.run(w.renderer.ensure_current(w.output, w.answer, rendered, "worker", w.live[3]))
    assert w.standard.calls == []  # validation cannot start another provider
    with w.live[1]() as db:
        assert db.get(ConversationTurn, w.output.turn_id).assistant_message_id is None


@pytest.mark.parametrize("change", ["revoke", "delete", "replace"])
def test_profile_changes_during_worker_inference_use_same_text_fallback(speech_world, change):
    w = speech_world
    original = w.worker.provider.synthesize
    async def mutate(request):
        result = await original(request)
        with w.live[1]() as db:
            profile = db.get(VoiceProfile, w.version.voice_profile_id)
            if change == "revoke": VoiceProfileService().revoke(db, 1, 1, profile.revision)
            elif change == "delete": VoiceProfileService().delete(db, 1, 1, profile.revision)
            else:
                # Existing READY replacement is activated while this job runs.
                VoiceProfileService().activate(db, 1, 1, replacement_payload)
            db.commit()
        return result
    if change == "replace":
        from app.schemas.voice_profile import VoiceActivation
        with w.live[1]() as db:
            p = db.get(VoiceProfile, w.version.voice_profile_id)
            replacement = prepare(db, reserve(db, revision=p.revision))
            p = db.get(VoiceProfile, p.id)
            replacement_payload = VoiceActivation(version_id=replacement.id,
                expected_revision=p.revision, binding_digest=replacement.binding_digest, approved=True)
    w.worker.provider.synthesize = mutate
    async def run():
        task = asyncio.create_task(w.renderer.render(w.output, w.answer, "worker", w.live[3]))
        for _ in range(200):
            if w.output.preserved_job_id: break
            await asyncio.sleep(.01)
        assert w.output.preserved_job_id
        assert await asyncio.to_thread(w.worker.run_once) == "stale"
        return await task
    result = asyncio.run(run())
    assert result.delivery == "standard" and result.text_digest == w.answer.text_digest
    assert w.standard.calls == [(w.answer.text, "marin")]
    with w.live[1]() as db:
        assert db.scalar(select(VoiceAsset).where(VoiceAsset.job_id == w.output.preserved_job_id,
            VoiceAsset.kind == "generated")) is None


@pytest.mark.parametrize("when", ["before_start", "after_start", "after_audio_end"])
def test_controller_revocation_boundary_and_receipts(live, monkeypatch, when):
    class RevokingRenderer(SocketSpeechRenderer):
        def __init__(self): super().__init__(); self.revoked = False; self.fallbacks = 0; self.controller_checks = 0
        async def render(self, output, answer, owner, settings):
            self.answers.append(answer)
            return RenderedSpeech(bytes(2400), 24000, 1, answer.text_digest, "preserved", 1, "job")
        async def ensure_current(self, output, answer, rendered, owner, settings):
            if asyncio.current_task().get_coro().__name__ == "controller":
                self.controller_checks += 1
                if when == "before_start" and self.controller_checks == 1: self.revoked = True
                if when == "after_start" and output.voice_delivery == "preserved": self.revoked = True
            if self.revoked and rendered.delivery == "preserved":
                raise PreservedSpeechUnavailable("revoked")
        async def fallback_before_start(self, output, answer, rendered, owner, settings):
            assert not output.samples and output.voice_delivery is None
            self.fallbacks += 1
            return RenderedSpeech(bytes(2400), 24000, 1, answer.text_digest, "standard", 1)
    renderer = RevokingRenderer()
    app.dependency_overrides[get_live_speech_renderer] = lambda: renderer
    monkeypatch.setattr(live[3], "voice_live_enabled", True)
    grant = create(live, 3, legacy_id=1, mode="legacy").json()
    with authenticate(live[0], grant) as ws:
        connected(ws, grant)
        receipt, thinking = start_answer(live, ws)
        freeze(live, ws, thinking)
        started = receive(ws, "assistant_started")
        if when == "before_start":
            assert started["voice_delivery"] == "standard" and renderer.fallbacks == 1
            receive(ws, "assistant_audio"); receive(ws, "assistant_audio_end")
        elif when == "after_audio_end":
            receive(ws, "assistant_audio"); receive(ws, "assistant_audio_end")
            renderer.revoked = True
            # No more PCM/provider events: idle polling must retire queued playback.
            receive(ws, "assistant_interrupted")
        else:
            receive(ws, "assistant_interrupted")
        assert renderer.fallbacks == (1 if when == "before_start" else 0)
        assert len(live[2].answer_requests) == len(renderer.answers) == 1
        with live[1]() as db:
            assert db.get(ConversationTurn, receipt["turn_id"]).assistant_message_id is None
        end_call(ws)


@pytest.mark.parametrize("mode,live_enabled,clone_enabled,actor", [
    ("rya", True, True, 1), ("legacy", False, True, 3), ("legacy", False, False, 3)])
def test_rya_and_feature_off_never_enter_speech_renderer(live, monkeypatch, mode, live_enabled, clone_enabled, actor):
    renderer = SocketSpeechRenderer()
    app.dependency_overrides[get_live_speech_renderer] = lambda: renderer
    monkeypatch.setattr(live[3], "voice_live_enabled", live_enabled)
    monkeypatch.setattr(live[3], "voice_cloning_enabled", clone_enabled)
    grant = create(live, actor, legacy_id=1, mode=mode).json()
    with authenticate(live[0], grant) as ws:
        connected(ws, grant); start_answer(live, ws)
        assert len(live[2].requests) == 1 and live[2].answer_requests == []
        assert renderer.answers == []
        end_call(ws)


def test_thirty_new_synthesis_cancellations_reject_provider_late_completion(live, monkeypatch):
    class UncooperativeRenderer(SocketSpeechRenderer):
        async def render(self, output, answer, owner, settings):
            self.answers.append(answer)
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                # Model work can finish physically even after Task.cancel().
                return RenderedSpeech(bytes(2400), 24000, 1, answer.text_digest, "preserved", 1)
    renderer = UncooperativeRenderer()
    app.dependency_overrides[get_live_speech_renderer] = lambda: renderer
    monkeypatch.setattr(live[3], "voice_live_enabled", True)
    monkeypatch.setattr(live[3], "realtime_messages_per_second", 120)
    grant = create(live, 3, legacy_id=1, mode="legacy").json()
    with authenticate(live[0], grant) as ws:
        connected(ws, grant)
        previous = None
        for index in range(30):
            item = f"cancel_{index}"
            receipt, thinking = start_answer(live, ws, item, previous)
            previous = item
            freeze(live, ws, thinking)
            async def rendering():
                for _ in range(200):
                    if len(renderer.answers) == index + 1: return
                    await asyncio.sleep(.005)
                raise AssertionError("Synthesis did not start")
            ws.portal.call(rendering)
            ws.send_json({"type": "interrupt", **{k: thinking[k] for k in (
                "session_id", "generation", "turn_id", "active_generation_id", "response_id")}})
            while True:
                value = ws.receive_json()
                assert value["type"] not in {"assistant_started", "assistant_audio", "error", "ended"}, value
                if value["type"] == "assistant_interrupted": break
            with live[1]() as db:
                turn = db.get(ConversationTurn, receipt["turn_id"])
                assert turn.state == "interrupted" and turn.assistant_message_id is None
        assert len(renderer.answers) == len(renderer.cancelled) == len(live[2].answer_requests) == 30
        end_call(ws)


@pytest.mark.parametrize("action", ["logout", "legacy_switch", "reconnect"])
def test_socket_identity_change_during_synthesis_never_publishes(live, monkeypatch, action):
    from tests.test_realtime_speech_l21 import BlockingSpeechRenderer
    from tests.test_realtime_l15 import ORIGIN, headers
    renderer = BlockingSpeechRenderer()
    app.dependency_overrides[get_live_speech_renderer] = lambda: renderer
    monkeypatch.setattr(live[3], "voice_live_enabled", True)
    grant = create(live, 3, legacy_id=1, mode="legacy").json()
    with authenticate(live[0], grant) as ws:
        connected(ws, grant)
        receipt, thinking = start_answer(live, ws)
        freeze(live, ws, thinking)
        async def wait_started():
            for _ in range(200):
                if renderer.started: return
                await asyncio.sleep(.005)
            raise AssertionError("No synthesis")
        ws.portal.call(wait_started)
        if action == "reconnect":
            # Existing provider-disconnect recovery must retire the renderer.
            ws.portal.call(lambda: live[2].events.put_nowait(
                realtime_sessions.RealtimeError("realtime_provider_connection", 502)))
        else:
            with live[1]() as db:
                if action == "logout": realtime_sessions.revoke(db, 3)
                else: db.get(Conversation, receipt["conversation_id"]).legacy_id = 2
                db.commit()
        for _ in range(10):
            value = ws.receive_json()
            assert value["type"] not in {"assistant_audio", "assistant_started", "assistant_completed"}, value
            if value["type"] == "ended": break
        else: raise AssertionError("Identity change did not close the call")
    assert renderer.cancelled == [receipt["turn_id"]]
    renderer.release = True
    with live[1]() as db:
        assert db.get(ConversationTurn, receipt["turn_id"]).assistant_message_id is None
    if action == "reconnect":
        reply = live[0].post(f"/api/v1/realtime/sessions/{grant['session_id']}/reconnect", headers=headers(3))
        assert reply.status_code == 200, reply.text
        replacement = reply.json()
        with authenticate(live[0], replacement) as ws:
            connected(ws, replacement)
            ws.send_json({"type": "ping"})
            for _ in range(5):
                event = ws.receive_json()
                assert not event["type"].startswith("assistant_"), event
                if event["type"] == "pong": break
            assert len(live[2].answer_requests) == 1
            end_call(ws)


def test_full_socket_renderer_job_worker_pcm_receipt_persists_one_exact_answer(live, monkeypatch, tmp_path):
    for name, value in {"voice_live_enabled": True, "voice_cloning_enabled": True,
            "voice_synthesis_provider": "fake"}.items():
        monkeypatch.setattr(live[3], name, value)
    raw = LocalSourceStorage(str(tmp_path / "s"))
    with live[1]() as db:
        source = b"synthetic-reference-fixture"
        version = prepare(db, reserve(db), source); activate(db, version)
        reference = db.get(VoiceAsset, version.reference_asset_id)
        raw.put(reference.object_key, source, content_type="audio/wav")
    standard = ExactStandard()
    renderer = LiveSpeechRenderer(live[1], VoiceStorage(raw), standard)
    app.dependency_overrides[get_live_speech_renderer] = lambda: renderer
    worker = VoiceSynthesisWorker(live[1], VoiceStorage(raw), settings=live[3],
        manifest=fake_manifest(), provider=FakeClonedSpeechProvider())
    worker.warmup()
    before = canonical_snapshot(live[1])
    text = "मी पुण्यात राहिलो. This is exactly the same frozen answer."
    grant = create(live, 3, legacy_id=1, mode="legacy").json()
    try:
        with authenticate(live[0], grant) as ws:
            connected(ws, grant)
            receipt, thinking = start_answer(live, ws)
            freeze(live, ws, thinking, text)
            async def wait_job():
                for _ in range(200):
                    with live[1]() as db:
                        job = db.scalar(select(VoiceJob).where(VoiceJob.purpose == "live"))
                        if job is not None: return job.id
                    await asyncio.sleep(.01)
                raise AssertionError("No live job admitted")
            job_id = ws.portal.call(wait_job)
            assert worker.run_once() == "ready"
            started = receive(ws, "assistant_started")
            frame = receive(ws, "assistant_audio")
            end = receive(ws, "assistant_audio_end")
            assert started["voice_delivery"] == "preserved"
            digest = hashlib.sha256(text.encode()).hexdigest()
            assert started["authoritative_text_digest"] == digest
            with live[1]() as db:
                job = db.get(VoiceJob, job_id)
                assert job.authoritative_text == text and job.authoritative_text_digest == digest
                assert db.get(ConversationTurn, receipt["turn_id"]).assistant_message_id is None
            binding = {k: started[k] for k in ("session_id", "generation", "turn_id", "active_generation_id", "response_id")}
            drain = {"type": "playback_drained", **binding,
                "sequence": frame["sequence"], "samples": end["samples"], "seal": end["seal"]}
            ws.send_json(drain)
            completed = receive(ws, "assistant_completed")
            ws.send_json(drain); ws.send_json({"type": "ping"}); receive(ws, "pong")
            with live[1]() as db:
                assert db.get(Message, completed["message_id"]).content == text
                assert db.scalar(select(func.count()).select_from(Message).where(
                    Message.conversation_id == receipt["conversation_id"], Message.role == MessageRole.ASSISTANT)) == 1
            assert len(live[2].answer_requests) == 1 and standard.calls == []
            assert canonical_snapshot(live[1]) == before
            end_call(ws)
    finally:
        worker.close()
