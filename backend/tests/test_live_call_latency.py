"""Phase 10.8.1 privacy-safe latency and phrase streaming tests."""

import asyncio
from datetime import datetime, timedelta, timezone
import inspect
import logging
from types import SimpleNamespace

from app.services.ai.context_builder import ContextBuilder
from app.services.ai.provider import SpeechResult
from app.services.live_call import (
    LIVE_CALL_RESPONSE_STYLE,
    LiveCallLatencyTrace,
    LiveCallSession,
    LiveCallTurnService,
    split_speakable_phrases,
)


class FakeTranscription:
    async def transcribe(self, _audio):
        return "How are you?"


class StreamingAI:
    def __init__(self, fallback=False):
        self.fallback = fallback

    async def stream_response(self, _messages):
        if self.fallback:
            raise NotImplementedError
        yield "I am well. "
        await asyncio.sleep(0)
        yield "How are you?"

    async def generate_response(self, _messages):
        return "I am well."


class FakeSpeech:
    def __init__(self):
        self.calls = []

    async def synthesize(self, **kwargs):
        self.calls.append(kwargs)
        return SpeechResult(kwargs["text"].encode(), "audio/mpeg", "mp3")


def session():
    now = datetime.now(timezone.utc)
    return LiveCallSession(
        "safe-session", "token", 1, 1, "Aaji", "grandmother",
        "standard_female", "connected", now, now + timedelta(minutes=15),
    )


def test_latency_trace_calculates_stage_and_first_audio_durations():
    trace = LiveCallLatencyTrace("safe-session", 7, vad_silence_ms=850)
    trace.marks = {
        "realtime_commit_received": 10.0,
        "turn_processing_started": 10.0,
        "speech_end_detected": 9.15,
        "transcription_started": 10.1,
        "transcription_completed": 10.7,
        "context_retrieval_started": 10.7,
        "context_retrieval_completed": 10.9,
        "generation_started": 10.9,
        "first_text_available": 11.3,
        "generation_completed": 11.8,
        "tts_started": 11.3,
        "first_audio_ready": 11.9,
        "tts_completed": 12.4,
        "playback_started": 12.0,
        "response_completed": 12.5,
    }
    metrics = trace.metrics()
    assert metrics["vad_silence_ms"] == 850
    assert metrics["transcription_ms"] == 600
    assert metrics["retrieval_ms"] == 200
    assert metrics["generation_first_text_ms"] == 400
    assert metrics["processing_start_to_first_audio_ms"] == 1900
    assert metrics["processing_start_to_playback_ms"] == 2000
    assert metrics["end_of_speech_to_first_audio_ms"] == 2750
    assert metrics["end_of_speech_to_playback_ms"] == 2850
    assert metrics["total_turn_ms"] == 2500


def test_startup_latency_measures_ready_to_tts_audio_and_playback():
    trace = LiveCallLatencyTrace("safe-session", 0)
    trace.marks = {
        "session_ready": 20.0,
        "greeting_tts_started": 20.01,
        "greeting_first_audio": 20.41,
        "greeting_playback_started": 20.46,
    }
    metrics = trace.metrics()
    assert metrics["session_ready_to_greeting_tts_ms"] == 10
    assert metrics["session_ready_to_greeting_audio_ms"] == 410
    assert metrics["session_ready_to_greeting_playback_ms"] == 460


def test_phrase_buffer_never_splits_mid_word_and_preserves_order():
    phrases, remainder = split_speakable_phrases("Meenakshi is my sister. We walked to")
    assert phrases == ["Meenakshi is my sister."]
    assert remainder == "We walked to"
    final, remainder = split_speakable_phrases(remainder + " school together.", final=True)
    assert final == ["We walked to school together."]
    assert remainder == ""


def test_first_long_clause_can_be_spoken_before_sentence_completion():
    phrases, remainder = split_speakable_phrases(
        "My husband's name is Madhav Kulkarni, and we often",
        allow_first_clause=True,
    )
    assert phrases == ["My husband's name is Madhav Kulkarni."]
    assert remainder == "and we often"
    short, unchanged = split_speakable_phrases(
        "Yes, and there is more", allow_first_clause=True,
    )
    assert short == []
    assert unchanged == "Yes, and there is more"


def test_streaming_pipeline_emits_ordered_audio_before_full_completion():
    speech = FakeSpeech()
    service = LiveCallTurnService(
        FakeTranscription(), StreamingAI(), ContextBuilder(10), speech, speech,
    )
    marks = []

    async def collect():
        return [item async for item in service.process_streaming(
            session=session(), audio=b"voice", content_type="audio/webm",
            history=(), mark=marks.append,
        )]

    events = asyncio.run(collect())
    assert [item["type"] for item in events] == [
        "transcription", "audio", "audio", "completed"
    ]
    assert [call["text"] for call in speech.calls] == ["I am well.", "How are you?"]
    assert all(call["standard_voice_profile"].value == "standard_female" for call in speech.calls)
    assert marks.index("context_retrieval_completed") < marks.index("generation_started")
    assert marks.index("first_audio_ready") < marks.index("tts_completed")


def test_non_streaming_provider_falls_back_to_full_response_tts():
    speech = FakeSpeech()
    service = LiveCallTurnService(
        FakeTranscription(), StreamingAI(fallback=True), ContextBuilder(10), speech, speech,
    )

    async def collect():
        return [item async for item in service.process_streaming(
            session=session(), audio=b"voice", content_type="audio/webm", history=(),
        )]

    events = asyncio.run(collect())
    assert [item["type"] for item in events] == ["transcription", "audio", "completed"]
    assert speech.calls[0]["text"] == "I am well."


def test_completed_turn_latency_summary_is_console_visible_and_privacy_safe(caplog, monkeypatch):
    from app.api.v1 import live_call as transport

    monkeypatch.setattr(transport, "get_settings", lambda: SimpleNamespace(debug=True))
    private = {
        "transcript": "My private transcript",
        "response": "A private response",
        "audio": "private audio",
        "token": "private token",
    }
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        transport._log_turn_latency("private-session", 7, {
            "vad_silence_ms": 850,
            "stt_final_ms": 600,
            "total_turn_ms": 2500,
            **private,
        })

    summaries = [record.getMessage() for record in caplog.records
                 if "LIVE_CALL_SERVER_LATENCY" in record.getMessage()]
    assert len(summaries) == 1
    summary = summaries[0]
    assert "turn_id=7" in summary
    assert "vad_ms=850" in summary
    assert "stt_first_partial_ms=na" in summary
    assert "playback_start_ms=na" in summary
    assert "total_turn_ms=2500" in summary
    assert "private-session" not in summary
    assert all(value not in summary for value in private.values())


def test_latency_summary_is_quiet_outside_debug(caplog, monkeypatch):
    from app.api.v1 import live_call as transport

    monkeypatch.setattr(transport, "get_settings", lambda: SimpleNamespace(debug=False))
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        transport._log_turn_latency("safe-session", 8, {"total_turn_ms": 100})
    assert not any("LIVE_CALL_SERVER_LATENCY" in record.getMessage() for record in caplog.records)


def test_latency_summary_is_emitted_only_at_turn_completion():
    from app.api.v1 import live_call as transport

    source = inspect.getsource(transport.live_call_transport)
    completion = source.index('trace.mark("response_completed")')
    summary = source.index("_log_turn_latency", completion)
    playback = source.index('if event_type == "latency.playback_started"')
    playback_block = source[playback:source.index("continue", playback)]
    assert completion < summary
    assert "_log_turn_latency" not in playback_block


def test_client_latency_summary_handles_missing_metrics_without_private_content(caplog, monkeypatch):
    from app.api.v1 import live_call as transport

    monkeypatch.setattr(transport, "get_settings", lambda: SimpleNamespace(debug=True))
    event = {
        "speech_end_to_realtime_commit_ms": 851,
        "client_end_of_speech_to_audible_ms": 10492,
        "streaming_stt_active": False,
        "streaming_stt_fallback_reason": "provider_fallback",
        "streaming_tts_active": True,
        "streaming_tts_fallback_reason": "none",
        "transcript": "private transcript",
        "response": "private response",
    }
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        transport._log_client_latency(9, event)

    summary = next(record.getMessage() for record in caplog.records
                   if "LIVE_CALL_CLIENT_LATENCY" in record.getMessage())
    assert "speech_end_to_realtime_commit_ms=851" in summary
    assert "audio_decode_ms=na" in summary
    assert "client_end_of_speech_to_audible_ms=10492" in summary
    assert "streaming_stt_active=False" in summary
    assert "streaming_stt_fallback_reason=provider_fallback" in summary
    assert "private transcript" not in summary and "private response" not in summary


def test_client_latency_summary_is_quiet_outside_debug(caplog, monkeypatch):
    from app.api.v1 import live_call as transport

    monkeypatch.setattr(transport, "get_settings", lambda: SimpleNamespace(debug=False))
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        transport._log_client_latency(9, {"speech_end_to_commit_ms": 851})
    assert not any("LIVE_CALL_CLIENT_LATENCY" in record.getMessage()
                   for record in caplog.records)


def test_live_prompt_overlay_is_compact_without_removing_safety():
    assert len(LIVE_CALL_RESPONSE_STYLE) < 260
    assert "current language" in LIVE_CALL_RESPONSE_STYLE
    assert "directly answers or reacts" in LIVE_CALL_RESPONSE_STYLE
    assert "generic filler" in LIVE_CALL_RESPONSE_STYLE
    assert "never invent memories" not in LIVE_CALL_RESPONSE_STYLE


def test_earlier_clause_boundaries_are_safe_and_multilingual():
    phrases, remainder = split_speakable_phrases(
        "My hometown is Kolhapur city, and later moved", allow_first_clause=True,
    )
    assert phrases == ["My hometown is Kolhapur city."]
    assert remainder == "and later moved"
    for fragment in ("My husband, is kind", "When I was, younger", "In Goa, we stayed"):
        assert split_speakable_phrases(fragment, allow_first_clause=True)[0] == []
    phrases, remainder = split_speakable_phrases(
        "माझे गाव कोल्हापूर आहे। पुढे", allow_first_clause=True,
    )
    assert phrases == ["माझे गाव कोल्हापूर आहे।"]
    assert remainder == "पुढे"


def test_trace_separates_provider_wait_from_phrase_assembly_and_prompt_counts():
    trace = LiveCallLatencyTrace("safe", 1)
    trace.marks = {
        "generation_started": 1.0,
        "provider_first_delta": 1.4,
        "phrase_buffer_first_emit": 1.65,
        "first_phrase_available": 1.65,
    }
    trace.add_context_metrics({
        "system_prompt_chars": 500, "grounding_chars": 100,
        "identity_context_chars": 0, "recent_history_chars": 80,
        "total_input_estimated_tokens": 170, "retrieved_memory_count": 2,
    })
    metrics = trace.metrics()
    assert metrics["generation_provider_first_delta_ms"] == 400
    assert metrics["phrase_assembly_delay_ms"] == 250
    assert metrics["system_prompt_chars"] == 500


def test_non_streaming_first_phrase_tts_handoff_is_measured_without_batch_wait():
    trace = LiveCallLatencyTrace("safe", 2)
    trace.marks = {
        "phrase_buffer_first_emit": 10.0,
        "tts_started": 10.0,
        "first_audio_ready": 10.6,
        "first_audio_chunk_sent": 10.61,
    }
    metrics = trace.metrics()
    assert metrics["first_phrase_ready_to_tts_start_ms"] == 0
    assert metrics["tts_synthesis_ms"] == 600
    assert metrics["tts_complete_to_ws_send_ms"] == 10


def test_session_creation_prewarms_cached_clients_without_provider_call():
    from app.api.v1 import live_call as transport

    source = inspect.getsource(transport.create_live_call_session)
    assert "get_live_call_turn_service()" in source
    assert ".synthesize(" not in source
    assert ".transcribe(" not in source
    assert ".generate_response(" not in source
