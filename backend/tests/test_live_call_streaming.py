"""Phase 10.8.3 provider-neutral streaming speech fast-path tests."""

import asyncio
import inspect
from datetime import datetime, timedelta, timezone

from app.services.ai.context_builder import ContextBuilder
from app.services.ai.provider import SpeechChunk, SpeechResult
from app.services.ai.transcription_service import TranscriptionService
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.realtime_transcription_provider import RealtimeTranscriptionSession
from app.services.ai.realtime_transcription_provider import (
    RealtimeTranscriptionConnectionError,
    classify_realtime_connection_error,
)
import json
from app.services.live_call import LiveCallSession, LiveCallTurnService
from app.services.live_call import LiveCallSessionStore
from unittest.mock import AsyncMock, patch


class FileTranscription:
    supports_streaming = False

    async def transcribe(self, _audio):
        return "How are you?"


class StreamingAI:
    async def stream_response(self, _messages):
        yield "I am doing well."


class FullSpeech:
    async def synthesize(self, **_kwargs):
        return SpeechResult(b"fallback", "audio/mpeg", "mp3")


class StreamingPersonalSpeech:
    def __init__(self, fail_before_first=False):
        self.fail_before_first = fail_before_first
        self.stream_calls = []
        self.synthesis_calls = []

    def supports_streaming(self, voice):
        return voice.id in {"cedar", "marin"}

    async def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        if self.fail_before_first:
            raise RuntimeError("stream unavailable")
        yield SpeechChunk(b"one", "audio/L16", "pcm", 24000)
        await asyncio.sleep(0)
        yield SpeechChunk(b"two", "audio/L16", "pcm", 24000)

    async def synthesize(self, **kwargs):
        self.synthesis_calls.append(kwargs)
        return SpeechResult(b"fallback", "audio/mpeg", "mp3")


def call_session(voice="cedar"):
    now = datetime.now(timezone.utc)
    return LiveCallSession(
        "stream-session", "token", 1, 1, "Aaji", "grandmother", voice,
        "connected", now, now + timedelta(minutes=15),
    )


async def collect(service):
    return [item async for item in service.process_streaming(
        session=call_session(), audio=b"webm", content_type="audio/webm",
        history=(),
    )]


def test_streaming_tts_yields_ordered_pcm_before_completion_and_preserves_voice_tone():
    personal = StreamingPersonalSpeech()
    service = LiveCallTurnService(
        FileTranscription(), StreamingAI(), ContextBuilder(10), FullSpeech(), personal,
    )
    events = asyncio.run(collect(service))
    chunks = [item["chunk"] for item in events if item["type"] == "audio_stream"]
    assert [chunk.content for chunk in chunks] == [b"one", b"two"]
    assert all(chunk.sample_rate == 24000 for chunk in chunks)
    assert events[-1]["type"] == "completed"
    assert personal.stream_calls[0]["voice"].id == "cedar"
    assert personal.stream_calls[0]["conversational_tone"] is not None
    assert personal.synthesis_calls == []


def test_streaming_tts_failure_before_first_chunk_falls_back_to_full_synthesis():
    personal = StreamingPersonalSpeech(fail_before_first=True)
    service = LiveCallTurnService(
        FileTranscription(), StreamingAI(), ContextBuilder(10), FullSpeech(), personal,
    )
    events = asyncio.run(collect(service))
    audio = [item for item in events if item["type"] == "audio"]
    assert len(audio) == 1
    assert audio[0]["speech"].content == b"fallback"
    assert len(personal.synthesis_calls) == 1


def test_committed_file_transcript_remains_the_only_grounding_input():
    import inspect

    source = inspect.getsource(LiveCallTurnService.process_streaming)
    assert "transcript = await self._transcription.transcribe(validated)" in source
    assert 'yield {"type": "transcription", "text": transcript}' in source
    assert "transcription.partial" not in source


def test_generation_producer_failure_is_consumed_and_next_turn_remains_usable():
    class FailingAI:
        async def stream_response(self, _messages):
            if False:
                yield ""
            raise RuntimeError("provider failed")

    failing = LiveCallTurnService(
        FileTranscription(), FailingAI(), ContextBuilder(10), FullSpeech(),
        StreamingPersonalSpeech(),
    )
    try:
        asyncio.run(collect(failing))
    except RuntimeError as exc:
        assert str(exc) == "provider failed"
    else:
        raise AssertionError("provider failure should remain recoverable by the transport")

    healthy = LiveCallTurnService(
        FileTranscription(), StreamingAI(), ContextBuilder(10), FullSpeech(),
        StreamingPersonalSpeech(),
    )
    events = asyncio.run(collect(healthy))
    assert events[-1]["type"] == "completed"
    source = inspect.getsource(LiveCallTurnService.process_streaming)
    assert "producer.exception()" in source


def test_successful_realtime_final_bypasses_fallback_transcription_once():
    class CountingTranscription(FileTranscription):
        def __init__(self): self.calls = 0
        async def transcribe(self, audio):
            self.calls += 1
            return await super().transcribe(audio)

    transcription = CountingTranscription()
    service = LiveCallTurnService(
        transcription, StreamingAI(), ContextBuilder(10), FullSpeech(),
        StreamingPersonalSpeech(),
    )

    async def exercise():
        return [item async for item in service.process_streaming(
            session=call_session(), audio=b"fallback", content_type="audio/webm",
            history=(), final_transcript="Authoritative realtime final",
        )]

    events = asyncio.run(exercise())
    assert transcription.calls == 0
    assert [item for item in events if item["type"] == "transcription"][0]["text"] == (
        "Authoritative realtime final"
    )
    assert len([item for item in events if item["type"] == "completed"]) == 1


def test_failed_realtime_path_uses_file_transcription_exactly_once():
    class CountingTranscription(FileTranscription):
        def __init__(self): self.calls = 0
        async def transcribe(self, audio):
            self.calls += 1
            return await super().transcribe(audio)

    transcription = CountingTranscription()
    service = LiveCallTurnService(
        transcription, StreamingAI(), ContextBuilder(10), FullSpeech(),
        StreamingPersonalSpeech(),
    )
    asyncio.run(collect(service))
    assert transcription.calls == 1


class FakeStreamingSTTSession:
    def __init__(self):
        self.chunks = []
        self.closed = False

    async def append_audio(self, chunk):
        self.chunks.append(chunk)
        return "माझं नाव" if len(self.chunks) == 1 else "Madhav आहे"

    async def finalize(self):
        return "माझं नाव Madhav आहे"

    async def close(self):
        self.closed = True


class FakeStreamingSTTProvider:
    supports_streaming_transcription = True

    def __init__(self):
        self.session = FakeStreamingSTTSession()

    async def start_transcription_stream(self, **_kwargs):
        return self.session


def test_streaming_stt_capability_exposes_multilingual_partial_and_final_text():
    provider = FakeStreamingSTTProvider()
    service = TranscriptionService(provider, model="fake-live-transcribe")

    async def exercise():
        stream = await service.start_stream("audio/pcm")
        partial_one = await stream.append_audio(b"one")
        partial_two = await stream.append_audio(b"two")
        final = await stream.finalize()
        await stream.close()
        return partial_one, partial_two, final

    partial_one, partial_two, final = asyncio.run(exercise())
    assert service.supports_streaming is True
    assert partial_one == "माझं नाव"
    assert partial_two == "Madhav आहे"
    assert final == "माझं नाव Madhav आहे"
    assert provider.session.closed is True


def test_configured_openai_adapter_capabilities_are_truthful():
    provider = OpenAIProvider.__new__(OpenAIProvider)
    assert provider.supports_streaming_speech is True
    assert provider.supports_streaming_transcription is True


def test_openai_realtime_stt_accepts_canonical_pcm_media_type():
    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider._settings = type("Settings", (), {"openai_api_key": "configured"})()
    expected = object()
    with patch(
        "app.services.ai.openai_provider.RealtimeTranscriptionSession.create",
        new=AsyncMock(return_value=expected),
    ) as create:
        result = asyncio.run(provider.start_transcription_stream(
            model="gpt-live-transcribe", content_type="audio/L16",
        ))
    assert result is expected
    create.assert_awaited_once()


def test_realtime_transcript_commit_does_not_require_final_blob_audio():
    store = LiveCallSessionStore()
    session = store.create(
        user_id=1, legacy_id=1, legacy_name="Private", relationship="relative",
        effective_voice="cedar",
    )
    store.mark_connected(session.session_id)
    assert store.begin_turn(session.session_id, 1, "audio/webm") is None
    committed = store.commit_streaming_transcript(session.session_id, 1)
    assert committed == (b"", "audio/webm")
    assert store.commit_audio(session.session_id, 1) == "already_committed"


def test_known_cedar_voice_resolves_to_streaming_fast_path():
    personal = StreamingPersonalSpeech()
    service = LiveCallTurnService(
        FileTranscription(), StreamingAI(), ContextBuilder(10), FullSpeech(), personal,
    )
    assert service.streaming_speech_capability(call_session("cedar")) == (True, "none")
    capable, reason = service.streaming_speech_capability(call_session("simran"))
    assert capable is False
    assert reason == "selected_voice_non_streaming"


def test_openai_streaming_adapter_yields_pcm_bytes_without_collecting_response():
    class Response:
        async def iter_bytes(self, chunk_size):
            assert chunk_size == 4096
            yield b"first"
            await asyncio.sleep(0)
            yield b"second"

    class Manager:
        async def __aenter__(self): return Response()
        async def __aexit__(self, *_args): return False

    class Create:
        def __init__(self): self.request = None
        def __call__(self, **kwargs):
            self.request = kwargs
            return Manager()

    create = Create()
    speech = type("Speech", (), {
        "with_streaming_response": type("Streaming", (), {"create": create})()
    })()
    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider._client = type("Client", (), {
        "audio": type("Audio", (), {"speech": speech})()
    })()

    async def collect_chunks():
        return [chunk async for chunk in provider.stream_speech(
            text="Hello", model="gpt-4o-mini-tts", voice="cedar",
            response_format="pcm", timeout_seconds=30,
            instructions="Speak warmly.",
        )]

    chunks = asyncio.run(collect_chunks())
    assert [chunk.content for chunk in chunks] == [b"first", b"second"]
    assert all(chunk.media_type == "audio/L16" for chunk in chunks)
    assert all(chunk.sample_rate == 24000 for chunk in chunks)
    assert create.request["voice"] == "cedar"
    assert create.request["instructions"] == "Speak warmly."


def test_transport_keeps_partial_ephemeral_and_bounds_each_chunk():
    import inspect
    from app.api.v1 import live_call as transport

    source = inspect.getsource(transport.live_call_transport)
    assert '"type": "transcription.partial"' in source
    assert "stream.finalize(), STREAMING_STT_FINAL_TIMEOUT_SECONDS" in source
    assert "final_transcript=final_transcript" in source
    assert "MAX_LIVE_CALL_AUDIO_CHUNK_BYTES" in source
    assert "transcription_streams.clear()" in source


def test_realtime_stt_uses_pcm_append_delta_and_explicit_commit():
    class Connection:
        def __init__(self):
            self.events = asyncio.Queue()
            self.sent = []
            self.closed = False

        async def send(self, payload): self.sent.append(json.loads(payload))
        async def recv(self): return json.dumps(await self.events.get())
        async def close(self): self.closed = True

    connection = Connection()

    async def exercise():
        await connection.events.put({"type": "session.created"})
        await connection.events.put({"type": "session.updated"})
        session = await RealtimeTranscriptionSession.create(
            api_key="test", model="gpt-live-transcribe",
            connector=lambda **_kwargs: asyncio.sleep(0, result=connection),
        )
        await connection.events.put({
            "type": "conversation.item.input_audio_transcription.delta",
            "delta": "नमस्कार",
        })
        partial = await session.append_audio(b"\x00\x00" * 100)
        await connection.events.put({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "नमस्कार Aaji",
        })
        final = await session.finalize()
        await session.close()
        return partial, final

    partial, final = asyncio.run(exercise())
    assert partial == "नमस्कार"
    assert final == "नमस्कार Aaji"
    assert connection.sent[0]["session"]["audio"]["input"]["format"] == {
        "type": "audio/pcm", "rate": 24000,
    }
    assert connection.sent[-1]["type"] == "input_audio_buffer.commit"
    assert connection.closed is True


def test_realtime_connection_configuration_waits_for_session_ready():
    class Connection:
        def __init__(self):
            self.events = asyncio.Queue()
            self.sent = []
        async def send(self, payload): self.sent.append(json.loads(payload))
        async def recv(self): return json.dumps(await self.events.get())
        async def close(self): pass

    connection = Connection()
    request = {}

    async def connector(**kwargs):
        request.update(kwargs)
        return connection

    async def exercise():
        await connection.events.put({"type": "session.created"})
        await connection.events.put({"type": "session.updated"})
        return await RealtimeTranscriptionSession.create(
            api_key="secret", model="gpt-live-transcribe",
            connector=connector,
        )

    session = asyncio.run(exercise())
    assert connection.sent[0]["session"]["type"] == "transcription"
    assert request["url"].endswith("?intent=transcription")
    assert "model=" not in request["url"]
    assert connection.sent[0]["session"]["audio"]["input"]["format"] == {
        "type": "audio/pcm", "rate": 24000,
    }
    asyncio.run(session.close())


def test_realtime_connection_errors_are_safely_classified():
    response = type("Response", (), {"status_code": 401})()
    rejected = type("Rejected", (Exception,), {})("private provider body")
    rejected.response = response
    assert classify_realtime_connection_error(rejected) == ("auth_failed", 401)
    assert classify_realtime_connection_error(TimeoutError()) == ("timeout", None)
    safe = RealtimeTranscriptionConnectionError("session_config_rejected")
    assert str(safe) == "Realtime transcription could not be started."
    assert "private provider body" not in str(safe)
