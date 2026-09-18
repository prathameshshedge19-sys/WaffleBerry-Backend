import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.main import app
from app.models.conversation import Message
from app.models.turn import ConversationTurn
from app.services import realtime_responses as responses
from app.services.realtime_provider import ProviderEvent, RealOpenAIRealtimeProvider
from app.services.realtime_speech import (
    LiveSpeechRenderer, PreservedSpeechUnavailable, RenderedSpeech,
    SpeechRenderFailure, get_live_speech_renderer,
)
from app.services.voice import VoiceProviderError
from tests.test_realtime_l15 import authenticate, connected, create, live
from tests.test_realtime_responses_l15 import receive
from tests.test_realtime_transcripts_l15 import TEXT, emit


def provider_event(output, kind, **payload):
    if kind == "response_created":
        payload = {"response": {"id": "answer_response", "metadata": {
            "generation_id": output.claim, "phase": "answer"}}, **payload}
    elif kind == "response_done":
        payload = {"response": {"id": "answer_response", "status": "completed"}, **payload}
    else:
        payload = {"response_id": "answer_response", "item_id": "answer_item",
            "content_index": 0, "output_index": 0, **payload}
    return output.provider(ProviderEvent(kind, output.connection, payload))


def test_authoritative_text_freezes_once_and_drives_playback_proof():
    output = responses.Output("session", 3, 7, "claim", text_first=True,
        answer_model="gpt-realtime-2.1")
    assert provider_event(output, "response_created") == []
    assert provider_event(output, "output_text_delta", delta="Exact frozen ") == []
    assert provider_event(output, "output_text_done", text="Exact frozen answer.") == []
    assert provider_event(output, "response_done") == [{"type": "authoritative_answer_ready"}]
    answer = output.authoritative_answer
    assert answer.text == "Exact frozen answer."
    assert answer.text_digest == hashlib.sha256(answer.text.encode()).hexdigest()
    assert answer.turn_id == 7 and answer.response_generation == "claim"
    started = output.start_speech("standard")
    assert started["authoritative_text_digest"] == answer.text_digest
    frame = output.speech_frame(bytes(2400))
    end = output.finish_speech()
    proof = output.acknowledge({"type": "playback_drained", **output.binding(),
        "sequence": frame["sequence"], "samples": end["samples"], "seal": end["seal"]})
    assert proof.transcript == answer.text
    assert provider_event(output, "response_done") == []


def test_same_realtime_model_generates_authoritative_text_without_second_brain():
    class Socket:
        def __init__(self): self.sent = []
        async def send(self, value): self.sent.append(json.loads(value))
    prepared = SimpleNamespace(actor=SimpleNamespace(mode="legacy"), turns=[
        SimpleNamespace(role="system", content="Existing Legacy brain policy"),
        SimpleNamespace(role="user", content="Where did I move?"),
    ])
    async def run():
        provider = RealOpenAIRealtimeProvider(); provider.socket = Socket()
        await provider.generate_authoritative_answer(prepared, "one-generation",
            turn_id=4, session_id="session", continuation=())
        response = provider.socket.sent[0]["response"]
        assert response["metadata"] == {"generation_id": "one-generation", "phase": "answer"}
        assert response["output_modalities"] == ["text"]
        assert response["reasoning"] == {"effort": "high"}
        assert response["input"][0]["content"][0]["text"] == "Where did I move?"
        assert "Existing Legacy brain policy" in response["instructions"]
        assert len(provider.socket.sent) == 1
    asyncio.run(run())


class ExactStandard:
    def __init__(self): self.calls = []
    async def synthesize_pcm(self, text, voice):
        self.calls.append((text, voice))
        return bytes(2400)


class IsolatedRenderer(LiveSpeechRenderer):
    def __init__(self, standard, *, clone_state=None):
        self.standard_provider = standard
        self.clone_state = clone_state
        self.cancelled = []
        self.jobs = SimpleNamespace()
    def _guard(self, output, owner, settings): return (1, 1, 1, "cedar")
    def _admit(self, output, answer, owner, settings):
        return ("clone-job" if self.clone_state else None), "cedar"
    def _snapshot(self, *args):
        return self.clone_state if isinstance(self.clone_state, tuple) else (self.clone_state, None)
    def _read_preserved(self, output, answer, owner, settings, job_id, asset_id):
        assert job_id == "clone-job" and asset_id == "clone-asset"
        return bytes(2400)
    def _cancel(self, job_id, output): self.cancelled.append(job_id)


def test_no_profile_and_clone_failure_use_identical_frozen_text_without_regeneration():
    answer = responses.AuthoritativeAnswer("मी पुण्यात राहिलो.",
        hashlib.sha256("मी पुण्यात राहिलो.".encode()).hexdigest(), 1, "claim",
        "openai_realtime", "gpt-realtime-2.1")
    output = responses.Output("session", 1, 1, "claim", text_first=True)
    settings = SimpleNamespace(voice_live_synthesis_timeout_seconds=10)
    for clone_state in (None, "failed"):
        standard = ExactStandard(); renderer = IsolatedRenderer(standard, clone_state=clone_state)
        rendered = asyncio.run(renderer.render(output, answer, "owner", settings))
        assert rendered.delivery == "standard" and rendered.text_digest == answer.text_digest
        assert standard.calls == [(answer.text, "cedar")]
        assert renderer.cancelled == (["clone-job"] if clone_state else [])

    standard = ExactStandard(); renderer = IsolatedRenderer(standard,
        clone_state=("succeeded", "clone-asset"))
    rendered = asyncio.run(renderer.render(output, answer, "owner", settings))
    assert rendered.delivery == "preserved" and rendered.text_digest == answer.text_digest
    assert rendered.preserved_job_id == "clone-job" and standard.calls == []

    class RevokedAfterSnapshot(IsolatedRenderer):
        def _read_preserved(self, *args):
            raise PreservedSpeechUnavailable("live_result_unavailable")
    standard = ExactStandard(); renderer = RevokedAfterSnapshot(standard,
        clone_state=("succeeded", "clone-asset"))
    rendered = asyncio.run(renderer.render(output, answer, "owner", settings))
    assert rendered.delivery == "standard" and rendered.text_digest == answer.text_digest
    assert standard.calls == [(answer.text, "cedar")] and renderer.cancelled == ["clone-job"]


def test_standard_speech_failure_does_not_fabricate_audio_or_retry_answer():
    class FailedStandard:
        async def synthesize_pcm(self, text, voice):
            raise VoiceProviderError("voice_provider_unavailable")
    text = "One frozen answer."
    answer = responses.AuthoritativeAnswer(text, hashlib.sha256(text.encode()).hexdigest(),
        1, "claim", "openai_realtime", "gpt-realtime-2.1")
    renderer = IsolatedRenderer(FailedStandard())
    with pytest.raises(SpeechRenderFailure, match="voice_provider_unavailable"):
        asyncio.run(renderer.render(responses.Output("session", 1, 1, "claim", text_first=True),
            answer, "owner", SimpleNamespace(voice_live_synthesis_timeout_seconds=10)))


class SocketSpeechRenderer:
    def __init__(self): self.answers = []; self.checks = 0; self.cancelled = []
    async def render(self, output, answer, owner, settings):
        self.answers.append(answer)
        return RenderedSpeech(bytes(2400), 24000, 1, answer.text_digest,
            "standard", 1)
    async def ensure_current(self, output, answer, rendered, owner, settings):
        assert rendered.text_digest == answer.text_digest
        self.checks += 1
    async def cancel_current(self, output): self.cancelled.append(output.turn_id)


def test_text_first_socket_persists_exact_answer_only_after_playback(live, monkeypatch):
    renderer = SocketSpeechRenderer()
    app.dependency_overrides[get_live_speech_renderer] = lambda: renderer
    monkeypatch.setattr(live[3], "voice_live_enabled", True)
    grant = create(live, 3, legacy_id=1, mode="legacy").json()
    with authenticate(live[0], grant) as ws:
        connected(ws, grant)
        emit(ws, live[2], "input_committed", item_id="A")
        emit(ws, live[2], "input_transcript_done", item_id="A", transcript=TEXT)
        receipt = receive(ws, "transcript_final")
        thinking = receive(ws, "assistant_thinking")
        async def wait_answer_request():
            for _ in range(200):
                if live[2].answer_requests: return
                await asyncio.sleep(.01)
            raise AssertionError("Authoritative answer request was not made")
        ws.portal.call(wait_answer_request)
        claim = thinking["active_generation_id"]
        emit(ws, live[2], "response_created", response={"id": "answer-A",
            "metadata": {"generation_id": claim, "phase": "answer"}})
        fields = {"response_id": "answer-A", "item_id": "answer-item-A",
            "content_index": 0, "output_index": 0}
        emit(ws, live[2], "output_text_delta", **fields, delta="Exact ")
        emit(ws, live[2], "output_text_done", **fields, text="Exact authoritative answer.")
        emit(ws, live[2], "response_done", response={"id": "answer-A", "status": "completed"})
        started = receive(ws, "assistant_started")
        assert started["voice_delivery"] == "standard"
        assert started["authoritative_text_digest"] == hashlib.sha256(
            b"Exact authoritative answer.").hexdigest()
        frame = receive(ws, "assistant_audio")
        end = receive(ws, "assistant_audio_end")
        with live[1]() as db:
            assert db.get(ConversationTurn, receipt["turn_id"]).assistant_message_id is None
        ws.send_json({"type": "playback_drained", **{k: started[k] for k in
            ("session_id", "generation", "turn_id", "active_generation_id", "response_id")},
            "sequence": frame["sequence"], "samples": end["samples"], "seal": end["seal"]})
        completed = receive(ws, "assistant_completed")
        with live[1]() as db:
            assert db.get(Message, completed["message_id"]).content == "Exact authoritative answer."
        assert len(renderer.answers) == 1 and renderer.answers[0].text == "Exact authoritative answer."
        assert len(live[2].answer_requests) == 1
        assert renderer.checks >= 2
        ws.send_json({"type": "end_call"})
        assert ws.receive_json()["type"] == "ended"


class BlockingSpeechRenderer(SocketSpeechRenderer):
    def __init__(self):
        super().__init__(); self.started = False; self.release = False
    async def render(self, output, answer, owner, settings):
        self.answers.append(answer); self.started = True
        while not self.release:
            await asyncio.sleep(.01)
        return RenderedSpeech(bytes(2400), 24000, 1, answer.text_digest, "standard", 1)


def test_barge_in_during_authoritative_text_generation_never_starts_speech(live, monkeypatch):
    renderer = SocketSpeechRenderer()
    app.dependency_overrides[get_live_speech_renderer] = lambda: renderer
    monkeypatch.setattr(live[3], "voice_live_enabled", True)
    grant = create(live, 3, legacy_id=1, mode="legacy").json()
    with authenticate(live[0], grant) as ws:
        connected(ws, grant)
        emit(ws, live[2], "input_committed", item_id="A")
        emit(ws, live[2], "input_transcript_done", item_id="A", transcript=TEXT)
        receipt = receive(ws, "transcript_final"); thinking = receive(ws, "assistant_thinking")
        async def answer_requested():
            for _ in range(200):
                if live[2].answer_requests: return
                await asyncio.sleep(.01)
            raise AssertionError("Authoritative answer request was not made")
        ws.portal.call(answer_requested)
        binding = {k: thinking[k] for k in
            ("session_id", "generation", "turn_id", "active_generation_id", "response_id")}
        ws.send_json({"type": "interrupt", **binding})
        receive(ws, "assistant_interrupted")
        emit(ws, live[2], "response_created", response={"id": "late-answer",
            "metadata": {"generation_id": thinking["active_generation_id"], "phase": "answer"}})
        ws.send_json({"type": "ping"}); receive(ws, "pong")
        assert renderer.answers == [] and "late-answer" in live[2].cancelled_responses
        with live[1]() as db:
            assert db.get(ConversationTurn, receipt["turn_id"]).assistant_message_id is None
        ws.send_json({"type": "end_call"}); assert ws.receive_json()["type"] == "ended"


def test_barge_in_during_frozen_text_synthesis_fences_late_audio(live, monkeypatch):
    renderer = BlockingSpeechRenderer()
    app.dependency_overrides[get_live_speech_renderer] = lambda: renderer
    monkeypatch.setattr(live[3], "voice_live_enabled", True)
    grant = create(live, 3, legacy_id=1, mode="legacy").json()
    with authenticate(live[0], grant) as ws:
        connected(ws, grant)
        emit(ws, live[2], "input_committed", item_id="A")
        emit(ws, live[2], "input_transcript_done", item_id="A", transcript=TEXT)
        receipt = receive(ws, "transcript_final")
        thinking = receive(ws, "assistant_thinking")
        async def answer_requested():
            for _ in range(200):
                if live[2].answer_requests: return
                await asyncio.sleep(.01)
            raise AssertionError("Authoritative answer request was not made")
        ws.portal.call(answer_requested)
        claim = thinking["active_generation_id"]
        emit(ws, live[2], "response_created", response={"id": "answer-A",
            "metadata": {"generation_id": claim, "phase": "answer"}})
        fields = {"response_id": "answer-A", "item_id": "answer-item-A",
            "content_index": 0, "output_index": 0}
        emit(ws, live[2], "output_text_done", **fields, text="Frozen before cancellation.")
        emit(ws, live[2], "response_done", response={"id": "answer-A", "status": "completed"})
        async def rendering_started():
            for _ in range(200):
                if renderer.started: return
                await asyncio.sleep(.01)
            raise AssertionError("Speech rendering did not start")
        ws.portal.call(rendering_started)
        binding = {k: thinking[k] for k in
            ("session_id", "generation", "turn_id", "active_generation_id", "response_id")}
        binding["response_id"] = "answer-A"
        ws.send_json({"type": "interrupt", **binding})
        receive(ws, "assistant_interrupted")
        renderer.release = True
        ws.send_json({"type": "ping"})
        assert receive(ws, "pong")["type"] == "pong"
        assert renderer.cancelled == [receipt["turn_id"]]
        with live[1]() as db:
            turn = db.get(ConversationTurn, receipt["turn_id"])
            assert turn.state == "interrupted" and turn.assistant_message_id is None
        ws.send_json({"type": "end_call"})
        assert ws.receive_json()["type"] == "ended"


def test_barge_in_during_text_first_playback_uses_existing_receipt_fence(live, monkeypatch):
    renderer = SocketSpeechRenderer()
    app.dependency_overrides[get_live_speech_renderer] = lambda: renderer
    monkeypatch.setattr(live[3], "voice_live_enabled", True)
    grant = create(live, 3, legacy_id=1, mode="legacy").json()
    with authenticate(live[0], grant) as ws:
        connected(ws, grant)
        emit(ws, live[2], "input_committed", item_id="A")
        emit(ws, live[2], "input_transcript_done", item_id="A", transcript=TEXT)
        receipt = receive(ws, "transcript_final"); thinking = receive(ws, "assistant_thinking")
        async def answer_requested():
            for _ in range(200):
                if live[2].answer_requests: return
                await asyncio.sleep(.01)
            raise AssertionError("Authoritative answer request was not made")
        ws.portal.call(answer_requested)
        claim = thinking["active_generation_id"]
        emit(ws, live[2], "response_created", response={"id": "answer-playback",
            "metadata": {"generation_id": claim, "phase": "answer"}})
        fields = {"response_id": "answer-playback", "item_id": "answer-item",
            "content_index": 0, "output_index": 0}
        emit(ws, live[2], "output_text_done", **fields, text="Playback is still receipt bound.")
        emit(ws, live[2], "response_done", response={"id": "answer-playback", "status": "completed"})
        started = receive(ws, "assistant_started"); receive(ws, "assistant_audio")
        binding = {k: started[k] for k in
            ("session_id", "generation", "turn_id", "active_generation_id", "response_id")}
        ws.send_json({"type": "interrupt", **binding})
        receive(ws, "assistant_interrupted")
        assert renderer.cancelled == [receipt["turn_id"]]
        with live[1]() as db:
            turn = db.get(ConversationTurn, receipt["turn_id"])
            assert turn.state == "interrupted" and turn.assistant_message_id is None
        ws.send_json({"type": "end_call"})
        for _ in range(5):
            if ws.receive_json()["type"] == "ended": break
        else: raise AssertionError("No clean end")
