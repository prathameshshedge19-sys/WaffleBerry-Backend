"""Ephemeral, provider-neutral Live Call session lifecycle."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import asyncio
import logging
import inspect
import re
import secrets
from threading import RLock
from time import monotonic
from collections.abc import AsyncIterator, Callable
from uuid import uuid4

from app.services.ai.ai_service import AIService
from app.services.ai.context_builder import ContextBuilder
from app.services.ai.provider import AIMessage, SpeechResult, GenerationOptions
from app.services.ai.transcription_service import TranscriptionService, validate_audio_upload
from app.services.personal_voice_speech_service import PersonalVoiceSpeechService
from app.services.persona_profile import PersonaProfile
from app.services.ai.sarvam_speech_service import SarvamSpeechService
from app.services.voice_catalogue import get_voice
from app.services.voice_profile_resolver import StandardVoiceProfile
from app.services.live_call_emotion import (
    LiveCallTone, LiveCallToneResolver, TONE_GENERATION_GUIDANCE,
)
from app.services.chat_service import ChatService
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)
LIVE_CALL_EVENT_VERSION = 1
LIVE_CALL_TRANSPORT = "websocket"
LIVE_CALL_CLIENT_EVENTS = frozenset({
    "session.start",
    "heartbeat.ping",
    "audio.chunk",
    "audio.commit",
    "audio.cancel",
    "transcription.audio",
    "interrupt",
    "session.end",
    "latency.playback_started",
    "latency.frontend_first_playable_chunk",
    "latency.client_turn",
    "transcription.commit",
    "latency.greeting_playback_started",
})
LIVE_CALL_SERVER_EVENTS = frozenset({
    "session.ready",
    "heartbeat.pong",
    "greeting.started",
    "greeting.audio",
    "greeting.completed",
    "greeting.failed",
    "transcription.partial",
    "transcription.final",
    "response.started",
    "response.text.delta",
    "audio.chunk",
    "response.completed",
    "response.interrupted",
    "session.ended",
    "error",
    "latency.commit_received",
})
SESSION_TTL = timedelta(minutes=15)
MAX_LIVE_CALL_AUDIO_BYTES = 10 * 1024 * 1024
MAX_LIVE_CALL_AUDIO_CHUNK_BYTES = 64 * 1024
MAX_LIVE_CALL_HISTORY_MESSAGES = 8
LIVE_CALL_TURN_TIMEOUT_SECONDS = 90
LIVE_CALL_PHRASE_BOUNDARY = re.compile(r"(?<=[.!?।॥])\s+")
LIVE_CALL_CLAUSE_BOUNDARY = re.compile(r"[,;:]\s+")
MIN_FIRST_PHRASE_CHARS = 26
MIN_FIRST_PHRASE_WORDS = 5
LIVE_CALL_STYLES = frozenset({"natural", "gentle", "expressive"})
LIVE_CALL_RESPONSE_LENGTHS = frozenset({"short", "balanced", "detailed"})
LIVE_CALL_RESPONSE_STYLE = (
    "Live Call: speak naturally in the user's current language, without Markdown. "
    "Start immediately with a useful, self-contained 6–18 word sentence that directly "
    "answers or reacts; avoid preambles, restating the question, and generic filler."
)
LIVE_CALL_LENGTH_GUIDANCE = {
    "short": "Use one concise spoken sentence whenever the supported answer permits it.",
    "balanced": "Use 1–2 sentences for a fact and 2–3 for a broad memory question.",
    "detailed": "Continue with supported detail after the short direct first sentence; do not pad.",
}
LIVE_CALL_OUTPUT_TOKEN_BUDGETS = {"short": 120, "balanced": 240, "detailed": 420}
LIVE_CALL_STYLE_GUIDANCE = {
    "natural": "Use balanced, natural conversational delivery.",
    "gentle": "Prefer slightly calmer, softer delivery unless the current turn calls for stronger energy.",
    "expressive": "Allow slightly more energetic phrasing only when appropriate to the current turn.",
}


@dataclass(slots=True)
class LiveCallLatencyTrace:
    """Privacy-safe monotonic timing metadata for one ephemeral turn."""
    session_id: str
    turn_id: int
    started_at: float = field(default_factory=monotonic)
    marks: dict[str, float] = field(default_factory=dict)
    vad_silence_ms: int | None = None
    context_metrics: dict[str, int] = field(default_factory=dict)

    def mark(self, stage: str) -> None:
        self.marks.setdefault(stage, monotonic())

    def add_context_metrics(self, values: dict[str, int]) -> None:
        self.context_metrics.update(values)

    def metrics(self) -> dict[str, int]:
        def elapsed(start: str, end: str) -> int | None:
            if start not in self.marks or end not in self.marks:
                return None
            return max(0, round((self.marks[end] - self.marks[start]) * 1000))
        values = {
            "vad_silence_ms": self.vad_silence_ms,
            "transcription_ms": elapsed("transcription_started", "transcription_completed"),
            "stt_first_partial_ms": elapsed("transcription_started", "transcription_first_partial"),
            "stt_final_ms": elapsed("transcription_started", "transcription_completed"),
            "retrieval_ms": elapsed("context_retrieval_started", "context_retrieval_completed"),
            "generation_first_text_ms": elapsed("generation_started", "first_text_available"),
            "generation_provider_first_delta_ms": elapsed("generation_started", "provider_first_delta"),
            "phrase_assembly_delay_ms": elapsed("provider_first_delta", "phrase_buffer_first_emit"),
            "generation_first_phrase_ms": elapsed("generation_started", "first_phrase_available"),
            "generation_total_ms": elapsed("generation_started", "generation_completed"),
            "tts_first_audio_ms": elapsed("tts_started", "first_audio_ready"),
            "first_phrase_ready_to_tts_start_ms": elapsed("phrase_buffer_first_emit", "tts_started"),
            "tts_synthesis_ms": elapsed("tts_started", "first_audio_ready"),
            "tts_complete_to_ws_send_ms": elapsed("first_audio_ready", "first_audio_chunk_sent"),
            "tts_first_chunk_ms": elapsed("tts_started", "tts_first_chunk"),
            "tts_total_ms": elapsed("tts_started", "tts_completed"),
            "processing_start_to_first_audio_ms": elapsed("turn_processing_started", "first_audio_ready"),
            "processing_start_to_playback_ms": elapsed("turn_processing_started", "playback_started"),
            "end_of_speech_to_first_audio_ms": elapsed("speech_end_detected", "first_audio_ready"),
            "end_of_speech_to_playback_ms": elapsed("speech_end_detected", "playback_started"),
            "total_turn_ms": elapsed("turn_processing_started", "response_completed"),
            "realtime_commit_to_processing_start_ms": elapsed("realtime_commit_received", "turn_processing_started"),
            "fallback_commit_to_processing_start_ms": elapsed("fallback_commit_received", "turn_processing_started"),
            "processing_start_to_response_started_sent_ms": elapsed("turn_processing_started", "response_started_sent"),
            "processing_start_to_first_text_delta_sent_ms": elapsed("turn_processing_started", "first_text_delta_sent"),
            "processing_start_to_first_audio_sent_ms": elapsed("turn_processing_started", "first_audio_chunk_sent"),
            "playback_delay_ms": elapsed("first_audio_ready", "playback_started"),
            "frontend_first_playable_chunk_ms": elapsed("first_audio_ready", "frontend_first_playable_chunk"),
            "playback_start_ms": elapsed("turn_processing_started", "playback_started"),
            "session_ready_to_greeting_tts_ms": elapsed("session_ready", "greeting_tts_started"),
            "session_ready_to_greeting_audio_ms": elapsed("session_ready", "greeting_first_audio"),
            "session_ready_to_greeting_playback_ms": elapsed("session_ready", "greeting_playback_started"),
        }
        return {
            **{key: value for key, value in values.items() if value is not None},
            **self.context_metrics,
        }


def split_speakable_phrases(
    buffer: str, *, final: bool = False, allow_first_clause: bool = False,
) -> tuple[list[str], str]:
    """Split only at natural sentence boundaries; never split a word."""
    parts = LIVE_CALL_PHRASE_BOUNDARY.split(buffer)
    if final:
        return [part.strip() for part in parts if part.strip()], ""
    if len(parts) < 2 and allow_first_clause:
        match = LIVE_CALL_CLAUSE_BOUNDARY.search(buffer)
        if match:
            candidate = buffer[:match.start()].strip()
            incomplete_endings = re.compile(
                r"\b(?:and|but|or|because|when|while|with|to|in|at|of|my|your|our)$",
                re.IGNORECASE,
            )
            if (len(candidate) >= MIN_FIRST_PHRASE_CHARS
                    and len(candidate.split()) >= MIN_FIRST_PHRASE_WORDS
                    and not incomplete_endings.search(candidate)):
                return [f"{candidate}."], buffer[match.end():]
    if len(parts) < 2:
        return [], buffer
    return [part.strip() for part in parts[:-1] if part.strip()], parts[-1]


def build_live_call_greeting(_session: "LiveCallSession") -> str:
    """Build a trusted neutral opening without generation or memory access."""
    return "Hello?"


@dataclass(slots=True)
class LiveCallRuntime:
    next_turn_id: int = 1
    active_turn_id: int | None = None
    audio: bytearray = field(default_factory=bytearray)
    content_type: str | None = None
    history: list[AIMessage] = field(default_factory=list)
    last_completed_turn_id: int | None = None
    interrupted_turn_ids: set[int] = field(default_factory=set)
    committed_turn_ids: set[int] = field(default_factory=set)
    turn_stage: str | None = None
    greeting_claimed: bool = False
    greeting_completed: bool = False
    memory_learning_turn_ids: set[int] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class LiveCallSession:
    session_id: str
    transport_token: str
    user_id: int
    legacy_id: int
    legacy_name: str
    relationship: str
    effective_voice: str
    state: str
    created_at: datetime
    expires_at: datetime
    ended_at: datetime | None = None
    conversation_style: str = "natural"
    response_length: str = "balanced"
    base_delivery_profile: str = "identity_neutral_v1"
    engine: str = "cascade"
    speech_renderer: str = "cascade_legacy"
    realtime_capable: bool = False
    persona_profile: PersonaProfile = field(default_factory=PersonaProfile)


class LiveCallSessionStore:
    """Keep short-lived call authorization in process memory; never audio."""

    def __init__(self) -> None:
        self._sessions: dict[str, LiveCallSession] = {}
        self._runtime: dict[str, LiveCallRuntime] = {}
        self._lock = RLock()
        self._latency: dict[tuple[str, int], LiveCallLatencyTrace] = {}

    def _discard_runtime_locked(self, session_id: str) -> None:
        """Release ephemeral audio, transcript history, and latency state."""
        self._runtime.pop(session_id, None)
        for key in [key for key in self._latency if key[0] == session_id]:
            self._latency.pop(key, None)

    def create(
        self,
        *,
        user_id: int,
        legacy_id: int,
        legacy_name: str,
        relationship: str,
        effective_voice: str,
        conversation_style: str = "natural",
        response_length: str = "balanced",
        engine: str = "cascade",
        speech_renderer: str = "cascade_legacy",
        realtime_capable: bool = False,
        persona_profile: PersonaProfile | None = None,
    ) -> LiveCallSession:
        now = datetime.now(timezone.utc)
        with self._lock:
            for session_id, session in tuple(self._sessions.items()):
                if session.user_id == user_id and session.state != "ended":
                    self._sessions[session_id] = replace(
                        session, state="ended", ended_at=now, transport_token=""
                    )
                    self._discard_runtime_locked(session_id)
            session = LiveCallSession(
                session_id=uuid4().hex,
                transport_token=secrets.token_urlsafe(32),
                user_id=user_id,
                legacy_id=legacy_id,
                legacy_name=legacy_name,
                relationship=relationship,
                effective_voice=effective_voice,
                state="connecting",
                created_at=now,
                expires_at=now + SESSION_TTL,
                conversation_style=conversation_style,
                response_length=response_length,
                engine=engine,
                speech_renderer=speech_renderer,
                realtime_capable=realtime_capable,
                persona_profile=persona_profile or PersonaProfile(),
            )
            self._sessions[session.session_id] = session
            self._runtime[session.session_id] = LiveCallRuntime()
        logger.info(
            "live_call_session_started user_id=%s legacy_id=%s transport=%s",
            user_id,
            legacy_id,
            LIVE_CALL_TRANSPORT,
        )
        return session

    def authorize_transport(
        self, session_id: str, transport_token: str
    ) -> LiveCallSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None and datetime.now(timezone.utc) >= session.expires_at:
                self._discard_runtime_locked(session_id)
                return None
            if (
                session is None
                or session.state == "ended"
                or not secrets.compare_digest(
                    session.transport_token, transport_token
                )
            ):
                return None
            return session

    def authorize_user(self, session_id: str, user_id: int) -> LiveCallSession | None:
        """Authorize an authenticated HTTP operation against an active call."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None and datetime.now(timezone.utc) >= session.expires_at:
                self._discard_runtime_locked(session_id)
                return None
            if (
                session is None
                or session.user_id != user_id
                or session.state == "ended"
            ):
                return None
            return session

    def transport_status(self, session_id: str, transport_token: str) -> str:
        """Classify transport authorization without exposing session details."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return "missing"
            if session.state == "ended":
                return "ended"
            if datetime.now(timezone.utc) >= session.expires_at:
                self._discard_runtime_locked(session_id)
                return "expired"
            if not secrets.compare_digest(session.transport_token, transport_token):
                return "unauthorized"
            return "active"

    def mark_connected(self, session_id: str) -> LiveCallSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.state == "ended":
                return None
            connected = replace(session, state="connected")
            self._sessions[session_id] = connected
            return connected

    def end(self, session_id: str, *, user_id: int) -> LiveCallSession | None:
        now = datetime.now(timezone.utc)
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.user_id != user_id:
                return None
            if session.state != "ended":
                session = replace(
                    session, state="ended", ended_at=now, transport_token=""
                )
                self._sessions[session_id] = session
            self._discard_runtime_locked(session_id)
        logger.info(
            "live_call_session_ended user_id=%s legacy_id=%s transport=%s",
            session.user_id,
            session.legacy_id,
            LIVE_CALL_TRANSPORT,
        )
        return session

    def end_transport(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is not None:
            self.end(session_id, user_id=session.user_id)

    def clear(self) -> None:
        """Test-only reset without exposing stored session data."""
        with self._lock:
            self._sessions.clear()
            self._runtime.clear()
            self._latency.clear()

    def begin_turn(self, session_id: str, turn_id: int, content_type: str) -> str | None:
        with self._lock:
            runtime = self._runtime.get(session_id)
            session = self._sessions.get(session_id)
            if (runtime is None or session is None or session.state == "ended"
                    or datetime.now(timezone.utc) >= session.expires_at):
                return "session_ended"
            if runtime.active_turn_id is not None:
                return "turn_in_progress"
            if turn_id != runtime.next_turn_id:
                return "stale_turn"
            runtime.active_turn_id = turn_id
            runtime.turn_stage = "recording"
            runtime.audio.clear()
            runtime.content_type = content_type
            return None

    def append_audio(self, session_id: str, turn_id: int, chunk: bytes) -> str | None:
        with self._lock:
            runtime = self._runtime.get(session_id)
            if runtime is None:
                return "session_ended"
            if runtime.active_turn_id != turn_id:
                return "stale_turn"
            if len(runtime.audio) + len(chunk) > MAX_LIVE_CALL_AUDIO_BYTES:
                return "audio_too_large"
            runtime.audio.extend(chunk)
            return None

    def commit_audio(self, session_id: str, turn_id: int) -> tuple[bytes, str] | str:
        with self._lock:
            runtime = self._runtime.get(session_id)
            if runtime is None:
                return "session_ended"
            if turn_id in runtime.committed_turn_ids:
                return "already_committed"
            if runtime.active_turn_id != turn_id:
                return "stale_turn"
            if not runtime.audio:
                return "audio_empty"
            runtime.committed_turn_ids.add(turn_id)
            runtime.turn_stage = "processing"
            return bytes(runtime.audio), runtime.content_type or ""

    def commit_streaming_transcript(
        self, session_id: str, turn_id: int,
    ) -> tuple[bytes, str] | str:
        """Commit an authoritative realtime transcript without waiting for Blob finalization."""
        with self._lock:
            runtime = self._runtime.get(session_id)
            if runtime is None:
                return "session_ended"
            if turn_id in runtime.committed_turn_ids:
                return "already_committed"
            if runtime.active_turn_id != turn_id:
                return "stale_turn"
            runtime.committed_turn_ids.add(turn_id)
            runtime.turn_stage = "processing"
            return bytes(runtime.audio), runtime.content_type or "audio/L16"

    def complete_turn(self, session_id: str, turn_id: int, user: str, assistant: str) -> bool:
        with self._lock:
            runtime = self._runtime.get(session_id)
            if runtime is None or runtime.active_turn_id != turn_id:
                return False
            runtime.history.extend((AIMessage(role="user", content=user), AIMessage(role="assistant", content=assistant)))
            runtime.history[:] = runtime.history[-MAX_LIVE_CALL_HISTORY_MESSAGES:]
            runtime.last_completed_turn_id = turn_id
            runtime.active_turn_id = None
            runtime.turn_stage = None
            runtime.next_turn_id += 1
            runtime.audio.clear()
            runtime.content_type = None
            return True

    def interrupt_turn(self, session_id: str, turn_id: int) -> str | None:
        """Cancel active work or retract an unheard completed assistant reply."""
        with self._lock:
            runtime = self._runtime.get(session_id)
            if runtime is None:
                return "session_ended"
            if turn_id in runtime.interrupted_turn_ids:
                return None
            if runtime.active_turn_id == turn_id:
                runtime.interrupted_turn_ids.add(turn_id)
                runtime.active_turn_id = None
                runtime.turn_stage = None
                runtime.next_turn_id = max(runtime.next_turn_id, turn_id + 1)
                runtime.audio.clear()
                runtime.content_type = None
                return None
            if runtime.last_completed_turn_id == turn_id:
                runtime.interrupted_turn_ids.add(turn_id)
                runtime.last_completed_turn_id = None
                if runtime.history and runtime.history[-1].role == "assistant":
                    runtime.history.pop()
                return None
            return "stale_turn"

    def is_interrupted(self, session_id: str, turn_id: int) -> bool:
        with self._lock:
            runtime = self._runtime.get(session_id)
            return runtime is None or turn_id in runtime.interrupted_turn_ids

    def fail_turn(self, session_id: str, turn_id: int) -> None:
        with self._lock:
            runtime = self._runtime.get(session_id)
            if runtime is not None and runtime.active_turn_id == turn_id:
                runtime.active_turn_id = None
                runtime.turn_stage = None
                runtime.next_turn_id = max(runtime.next_turn_id, turn_id + 1)
                runtime.audio.clear()
                runtime.content_type = None

    def history(self, session_id: str) -> tuple[AIMessage, ...]:
        with self._lock:
            runtime = self._runtime.get(session_id)
            return tuple(runtime.history) if runtime else ()

    def claim_memory_learning_turn(self, session_id: str, turn_id: int) -> bool:
        """Idempotently claim one bounded final user turn for background learning."""
        with self._lock:
            runtime = self._runtime.get(session_id)
            if runtime is None or turn_id in runtime.memory_learning_turn_ids:
                return False
            runtime.memory_learning_turn_ids.add(turn_id)
            return True

    def recovery_state(self, session_id: str) -> dict[str, object] | None:
        """Return privacy-safe, ephemeral turn reconciliation metadata."""
        with self._lock:
            runtime = self._runtime.get(session_id)
            session = self._sessions.get(session_id)
            if runtime is None or session is None or session.state == "ended":
                return None
            return {
                "active_turn_id": runtime.active_turn_id,
                "active_turn_stage": runtime.turn_stage,
                "last_completed_turn_id": runtime.last_completed_turn_id,
                "next_turn_id": runtime.next_turn_id,
                "interrupted_turn_ids": sorted(runtime.interrupted_turn_ids),
                "greeting_claimed": runtime.greeting_claimed,
                "greeting_completed": runtime.greeting_completed,
            }

    def start_latency(
        self, session_id: str, turn_id: int, vad_silence_ms: int | None,
        *, commit_kind: str = "fallback",
    ) -> LiveCallLatencyTrace:
        with self._lock:
            trace = self._latency.get((session_id, turn_id))
            if trace is None:
                trace = LiveCallLatencyTrace(session_id, turn_id, vad_silence_ms=vad_silence_ms)
                self._latency[(session_id, turn_id)] = trace
            commit_stage = (
                "realtime_commit_received" if commit_kind == "realtime"
                else "fallback_commit_received"
            )
            trace.mark(commit_stage)
            if vad_silence_ms is not None:
                trace.marks.setdefault(
                    "speech_end_detected",
                    trace.marks[commit_stage] - (vad_silence_ms / 1000),
                )
            return trace

    def latency_trace(self, session_id: str, turn_id: int) -> LiveCallLatencyTrace | None:
        with self._lock:
            return self._latency.get((session_id, turn_id))

    def claim_greeting(self, session_id: str) -> bool:
        """Claim the one greeting allowed for a logical call session."""
        with self._lock:
            runtime = self._runtime.get(session_id)
            if runtime is None or runtime.greeting_claimed:
                return False
            runtime.greeting_claimed = True
            return True

    def complete_greeting(self, session_id: str) -> None:
        with self._lock:
            runtime = self._runtime.get(session_id)
            if runtime is not None:
                runtime.greeting_completed = True


class LiveCallTurnService:
    """Provider-neutral, transient orchestration for one spoken call turn."""

    def __init__(self, transcription: TranscriptionService, ai: AIService,
                 context: ContextBuilder, standard_speech: SarvamSpeechService,
                 personal_speech: PersonalVoiceSpeechService,
                 tone_resolver: LiveCallToneResolver | None = None,
                 companion_context: ChatService | None = None) -> None:
        self._transcription = transcription
        self._ai = ai
        self._context = context
        self._standard_speech = standard_speech
        self._personal_speech = personal_speech
        self._tone_resolver = tone_resolver or LiveCallToneResolver()
        self._companion_context = companion_context

    @property
    def supports_streaming_transcription(self) -> bool:
        return self._transcription.supports_streaming

    async def start_transcription_stream(self, content_type: str):
        return await self._transcription.start_stream(content_type)

    def streaming_speech_capability(self, session: LiveCallSession) -> tuple[bool, str]:
        voice = get_voice(session.effective_voice)
        if voice is None:
            return False, "legacy_standard_profile"
        if not self._personal_speech.supports_streaming(voice):
            return False, "selected_voice_non_streaming"
        return True, "none"

    @staticmethod
    def _apply_generation_overlay(
        messages: list[AIMessage], session: LiveCallSession, tone: LiveCallTone,
    ) -> None:
        overlay = (
            f"{LIVE_CALL_RESPONSE_STYLE}\n"
            f"Length: {LIVE_CALL_LENGTH_GUIDANCE[session.response_length]}\n"
            f"Style: {LIVE_CALL_STYLE_GUIDANCE[session.conversation_style]}\n"
            f"Delivery: {TONE_GENERATION_GUIDANCE[tone]} Emotional safety overrides style."
        )
        messages[0] = AIMessage(
            role="system", content=f"{messages[0].content}\n\n{overlay}",
        )

    @staticmethod
    def _prompt_metrics(messages: list[AIMessage], prepared=None) -> dict[str, int]:
        system_chars = len(messages[0].content)
        history_chars = sum(len(item.content) for item in messages[1:-1])
        total_chars = sum(len(item.content) for item in messages)
        return {
            "system_prompt_chars": system_chars,
            "grounding_chars": getattr(prepared, "grounding_chars", 0),
            "identity_context_chars": getattr(prepared, "identity_context_chars", 0),
            "recent_history_chars": history_chars,
            "total_input_estimated_tokens": (total_chars + 3) // 4,
            "retrieved_memory_count": len(getattr(prepared, "memory_ids", ())),
        }

    @staticmethod
    def _supports_generation_options(method: Callable) -> bool:
        try:
            return "generation_options" in inspect.signature(method).parameters
        except (TypeError, ValueError):
            return False

    async def process(self, *, session: LiveCallSession, audio: bytes,
                      content_type: str, history: tuple[AIMessage, ...],
                      db: Session | None = None,
                      turn_id: int | None = None) -> tuple[str, str, SpeechResult]:
        validated = validate_audio_upload(audio, content_type)
        transcript = await self._transcription.transcribe(validated)
        tone = self._tone_resolver.resolve(transcript, history)
        if self._companion_context is not None and db is not None:
            prepared = self._companion_context.prepare_live_call_input(
                db, user_id=session.user_id, legacy_id=session.legacy_id,
                legacy_name=session.legacy_name, relationship=session.relationship,
                user_message=transcript, history=history,
            )
            messages = prepared.messages
            # Release the read-only transaction before provider work.
            db.rollback()
        else:
            messages = self._context.build_chat_messages(
                history, transcript, persona_display_name=session.legacy_name,
                persona_relationship=session.relationship, retrieval_available=False,
            )
        self._apply_generation_overlay(messages, session, tone)
        generation_options = GenerationOptions(
            max_output_tokens=LIVE_CALL_OUTPUT_TOKEN_BUDGETS[session.response_length]
        )
        if self._supports_generation_options(self._ai.generate_response):
            response = await self._ai.generate_response(
                messages, generation_options=generation_options,
            )
        else:
            response = await self._ai.generate_response(messages)
        speech = await self._synthesize_live_call_phrase(
            session, response, tone, turn_id=turn_id, kind="response",
        )
        return transcript, response, speech

    async def greeting(
        self, *, session: LiveCallSession, turn_id: int | None = 0,
    ) -> tuple[str, SpeechResult]:
        """Speak one deterministic neutral greeting without AI or memory access."""
        response = build_live_call_greeting(session)
        speech = await self._synthesize_live_call_phrase(
            session, response, LiveCallTone.NEUTRAL,
            turn_id=turn_id, kind="greeting",
        )
        return response, speech

    async def process_streaming(
        self, *, session: LiveCallSession, audio: bytes, content_type: str,
        history: tuple[AIMessage, ...], db: Session | None = None,
        mark: Callable[[str], None] | None = None,
        record_metrics: Callable[[dict[str, int]], None] | None = None,
        final_transcript: str | None = None,
        turn_id: int | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        """Stream grounded text into ordered, phrase-level speech chunks."""
        note = mark or (lambda _stage: None)
        if final_transcript is None:
            validated = validate_audio_upload(audio, content_type)
            note("transcription_started")
            transcript = await self._transcription.transcribe(validated)
            note("transcription_completed")
        else:
            transcript = final_transcript.strip()
            if not transcript:
                raise ValueError("Streaming transcription returned no final text.")
        yield {"type": "transcription", "text": transcript}
        tone = self._tone_resolver.resolve(transcript, history)

        note("context_retrieval_started")
        prepared = None
        if self._companion_context is not None and db is not None:
            prepared = self._companion_context.prepare_live_call_input(
                db, user_id=session.user_id, legacy_id=session.legacy_id,
                legacy_name=session.legacy_name, relationship=session.relationship,
                user_message=transcript, history=history,
            )
            messages = prepared.messages
            db.rollback()
        else:
            messages = self._context.build_chat_messages(
                history, transcript, persona_display_name=session.legacy_name,
                persona_relationship=session.relationship, retrieval_available=False,
            )
        note("context_retrieval_completed")
        self._apply_generation_overlay(messages, session, tone)
        if record_metrics is not None:
            record_metrics(self._prompt_metrics(messages, prepared))
        generation_options = GenerationOptions(
            max_output_tokens=LIVE_CALL_OUTPUT_TOKEN_BUDGETS[session.response_length]
        )

        queue: asyncio.Queue[str | None] = asyncio.Queue()
        response_parts: list[str] = []

        async def produce() -> None:
            buffer = ""
            received = False
            phrase_emitted = False
            generation_failed = False
            note("generation_started")
            try:
                stream_options = (
                    {"generation_options": generation_options}
                    if self._supports_generation_options(self._ai.stream_response)
                    else {}
                )
                async for delta in self._ai.stream_response(messages, **stream_options):
                    if not received:
                        note("provider_first_delta")
                        note("first_text_available")
                    received = True
                    response_parts.append(delta)
                    buffer += delta
                    phrases, buffer = split_speakable_phrases(
                        buffer, allow_first_clause=not phrase_emitted
                    )
                    for phrase in phrases:
                        if not phrase_emitted:
                            note("phrase_buffer_first_emit")
                            note("first_phrase_available")
                        await queue.put(phrase)
                        phrase_emitted = True
            except (NotImplementedError, AttributeError):
                if received:
                    raise
                response_options = (
                    {"generation_options": generation_options}
                    if self._supports_generation_options(self._ai.generate_response)
                    else {}
                )
                response = await self._ai.generate_response(messages, **response_options)
                note("provider_first_delta")
                note("first_text_available")
                response_parts.append(response)
                buffer = response
            except Exception:
                generation_failed = True
                raise
            finally:
                if buffer.strip():
                    phrases, _ = split_speakable_phrases(buffer, final=True)
                    for phrase in phrases:
                        if not phrase_emitted:
                            note("phrase_buffer_first_emit")
                            note("first_phrase_available")
                        await queue.put(phrase)
                        phrase_emitted = True
                note("generation_failed" if generation_failed else "generation_completed")
                await queue.put(None)

        producer = asyncio.create_task(produce())
        first_audio = True
        try:
            while True:
                phrase = await queue.get()
                if phrase is None:
                    break
                if first_audio:
                    note("tts_started")
                streamed = False
                try:
                    async for chunk in self._stream_live_call_phrase(
                        session, phrase, tone, turn_id=turn_id,
                    ):
                        streamed = True
                        if first_audio:
                            note("tts_first_chunk")
                            note("first_audio_ready")
                            first_audio = False
                        yield {"type": "audio_stream", "chunk": chunk}
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if streamed:
                        raise
                if not streamed:
                    speech = await self._synthesize_live_call_phrase(
                        session, phrase, tone, turn_id=turn_id,
                    )
                    if first_audio:
                        note("first_audio_ready")
                        first_audio = False
                    yield {"type": "audio", "speech": speech}
            await producer
            note("tts_completed")
            yield {
                "type": "completed", "transcript": transcript,
                "response": "".join(response_parts).strip(),
            }
        finally:
            if not producer.done():
                producer.cancel()
                try:
                    await producer
                except (asyncio.CancelledError, Exception):
                    pass
            else:
                # Always retrieve a completed producer exception, including when
                # the async consumer disappeared before reaching `await producer`.
                try:
                    producer.exception()
                except asyncio.CancelledError:
                    pass

    async def _stream_live_call_phrase(
        self, session: LiveCallSession, text: str, tone: LiveCallTone,
        *, turn_id: int | None = None,
    ):
        voice = get_voice(session.effective_voice)
        if voice is None or not self._personal_speech.supports_streaming(voice):
            raise NotImplementedError
        speech_tone = LiveCallTone.NEUTRAL
        self._log_voice_route(session, turn_id, "response", voice, speech_tone, 0)
        async for chunk in self._personal_speech.stream(
            text=text, voice=voice, conversational_tone=speech_tone,
        ):
            yield chunk

    async def _synthesize_live_call_phrase(
        self, session: LiveCallSession, text: str, tone: LiveCallTone,
        *, turn_id: int | None = None, kind: str = "response",
    ) -> SpeechResult:
        voice = get_voice(session.effective_voice)
        profile = StandardVoiceProfile.MALE if session.effective_voice == "standard_male" else StandardVoiceProfile.FEMALE
        speech_tone = LiveCallTone.NEUTRAL
        async def synthesize(delivery_tone: LiveCallTone) -> SpeechResult:
            if voice is not None:
                return await self._personal_speech.synthesize(
                    text=text, voice=voice, response_format="mp3",
                    conversational_tone=delivery_tone,
                )
            return await self._standard_speech.synthesize(
                text=text, standard_voice_profile=profile, response_format="mp3",
                preserve_text=True, conversational_tone=delivery_tone,
            )
        try:
            self._log_voice_route(session, turn_id, kind, voice, speech_tone, 0)
            return await synthesize(speech_tone)
        except Exception:
            self._log_voice_route(session, turn_id, kind, voice, speech_tone, 1)
            return await synthesize(speech_tone)

    async def render_external_phrase(
        self, session: LiveCallSession, text: str, *, generation_id: str,
    ) -> SpeechResult:
        """Render one bounded Realtime phrase through the call's frozen voice."""
        if session.speech_renderer not in {"external_streaming_tts", "external_nonstreaming_tts"}:
            raise ValueError("External speech is not enabled for this call.")
        return await self._synthesize_live_call_phrase(
            session, text, LiveCallTone.NEUTRAL, kind=f"external:{generation_id[-8:]}",
        )

    @staticmethod
    def _log_voice_route(
        session: LiveCallSession, turn_id: int | None, kind: str,
        voice, tone: LiveCallTone, retry: int,
    ) -> None:
        provider_voice = voice.provider_voice if voice is not None else session.effective_voice
        logger.debug(
            "LIVE_CALL_VOICE session_id=%s turn_id=%s kind=%s effective_voice=%s "
            "provider_voice_id_safe=%s delivery_profile_id=%s tone=%s retry=%s voice_match=True",
            session.session_id, turn_id, kind, session.effective_voice,
            provider_voice, session.base_delivery_profile, tone.value, retry,
        )


live_call_sessions = LiveCallSessionStore()
