"""Authenticated REST and WebSocket foundation for Live Call."""

import asyncio
import base64
import binascii
import json
import logging
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.crud.memory import LegacyCRUD
from app.crud.user import UserCRUD
from app.db import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.live_call import (
    LiveCallSessionCreate,
    LiveCallSessionEndResponse,
    LiveCallSessionResponse,
    RealtimeBootstrapResponse,
    RealtimeSpeechRequest,
    RealtimeSpeechResponse,
    RealtimeToolRequest,
    RealtimeToolResponse,
)
from app.services.live_call import (
    LIVE_CALL_EVENT_VERSION,
    LIVE_CALL_TURN_TIMEOUT_SECONDS,
    MAX_LIVE_CALL_AUDIO_CHUNK_BYTES,
    live_call_sessions,
)
from app.services.voice_profile_resolver import StandardVoiceResolver
from app.services.persona_profile import PersonaProfileService
from app.config import get_settings
from app.dependencies.ai import (
    get_live_call_turn_service,
    get_realtime_bootstrap_provider,
    get_realtime_tool_service,
)
from app.services.live_call import LiveCallTurnService
from app.services.ai.exceptions import AITimeoutError
from app.services.ai.transcription_service import AudioValidationError
from app.services.realtime_live_call import (
    OpenAIRealtimeBootstrapProvider,
    RealtimeBootstrapError,
    RealtimeToolService,
    choose_live_call_delivery,
)


router = APIRouter()
logger = logging.getLogger(__name__)
console_logger = logging.getLogger("uvicorn.error")
STREAMING_STT_START_TIMEOUT_SECONDS = 10
STREAMING_STT_CHUNK_TIMEOUT_SECONDS = 2
STREAMING_STT_FINAL_TIMEOUT_SECONDS = 30


def _log_turn_latency(
    _session_id: str, turn_id: int, metrics: dict[str, int], diagnostics: dict | None = None,
) -> None:
    """Emit one DEBUG-only, privacy-safe turn summary through Uvicorn's console logger."""
    if not get_settings().debug:
        return

    def metric(name: str) -> int | str:
        value = metrics.get(name)
        return value if isinstance(value, int) else "na"

    diagnostics = diagnostics or {}
    console_logger.info(
        "LIVE_CALL_SERVER_LATENCY turn_id=%s vad_ms=%s stt_first_partial_ms=%s "
        "stt_final_ms=%s retrieval_ms=%s generation_first_phrase_ms=%s "
        "generation_provider_first_delta_ms=%s phrase_assembly_delay_ms=%s "
        "system_prompt_chars=%s grounding_chars=%s identity_context_chars=%s "
        "recent_history_chars=%s total_input_estimated_tokens=%s retrieved_memory_count=%s "
        "tts_first_chunk_ms=%s frontend_first_playable_chunk_ms=%s "
        "first_phrase_ready_to_tts_start_ms=%s tts_synthesis_ms=%s "
        "tts_complete_to_ws_send_ms=%s "
        "playback_start_ms=%s end_of_speech_to_playback_ms=%s total_turn_ms=%s "
        "realtime_commit_to_processing_start_ms=%s fallback_commit_to_processing_start_ms=%s "
        "processing_start_to_response_started_sent_ms=%s "
        "processing_start_to_first_text_delta_sent_ms=%s "
        "processing_start_to_first_audio_sent_ms=%s "
        "streaming_stt_configured=%s streaming_stt_backend_capable=%s "
        "streaming_stt_session_created=%s streaming_stt_chunks_received=%s "
        "pcm_chunks_received=%s streaming_stt_fallback_reason=%s "
        "streaming_stt_status_code=%s "
        "voice_capability=%s streaming_tts_selected=%s streaming_tts_active=%s "
        "streaming_tts_fallback_reason=%s",
        turn_id,
        metric("vad_silence_ms"),
        metric("stt_first_partial_ms"),
        metric("stt_final_ms"),
        metric("retrieval_ms"),
        metric("generation_first_phrase_ms"),
        metric("generation_provider_first_delta_ms"),
        metric("phrase_assembly_delay_ms"),
        metric("system_prompt_chars"),
        metric("grounding_chars"),
        metric("identity_context_chars"),
        metric("recent_history_chars"),
        metric("total_input_estimated_tokens"),
        metric("retrieved_memory_count"),
        metric("tts_first_chunk_ms"),
        metric("frontend_first_playable_chunk_ms"),
        metric("first_phrase_ready_to_tts_start_ms"),
        metric("tts_synthesis_ms"),
        metric("tts_complete_to_ws_send_ms"),
        metric("playback_start_ms"),
        metric("end_of_speech_to_playback_ms"),
        metric("total_turn_ms"),
        metric("realtime_commit_to_processing_start_ms"),
        metric("fallback_commit_to_processing_start_ms"),
        metric("processing_start_to_response_started_sent_ms"),
        metric("processing_start_to_first_text_delta_sent_ms"),
        metric("processing_start_to_first_audio_sent_ms"),
        diagnostics.get("streaming_stt_configured", "na"),
        diagnostics.get("streaming_stt_backend_capable", "na"),
        diagnostics.get("streaming_stt_session_created", "na"),
        diagnostics.get("streaming_stt_chunks_received", "na"),
        diagnostics.get("pcm_chunks_received", "na"),
        diagnostics.get("streaming_stt_fallback_reason", "na"),
        diagnostics.get("streaming_stt_status_code", "na"),
        diagnostics.get("voice_capability", "na"),
        diagnostics.get("streaming_tts_selected", "na"),
        diagnostics.get("streaming_tts_active", "na"),
        diagnostics.get("streaming_tts_fallback_reason", "na"),
    )


CLIENT_LATENCY_METRICS = (
    "speech_end_to_realtime_commit_ms",
    "speech_end_to_fallback_commit_ms",
    "realtime_commit_to_server_receive_ms",
    "fallback_commit_to_server_receive_ms",
    "realtime_commit_to_first_server_response_ms",
    "client_first_audio_receive_ms",
    "audio_decode_ms",
    "audio_queue_wait_ms",
    "audio_context_resume_ms",
    "client_end_of_speech_to_audible_ms",
    "response_audio_completed_ms",
    "silence_to_recorder_stop_ms",
    "recorder_stop_to_final_data_ms",
    "final_data_to_fallback_commit_ms",
)


def _log_client_latency(turn_id: int, event: dict) -> None:
    """Log durations computed solely from the browser's monotonic clock."""
    if not get_settings().debug:
        return

    def value(name: str) -> int | str:
        candidate = event.get(name)
        return candidate if isinstance(candidate, int) and candidate >= 0 else "na"

    def capability(name: str) -> bool | str:
        candidate = event.get(name)
        return candidate if isinstance(candidate, bool) else "na"

    def reason(name: str) -> str:
        candidate = event.get(name)
        return candidate if candidate in {
            "none", "unsupported", "provider_fallback", "speaker_off", "no_audio",
            "browser_pcm_unsupported", "backend_not_capable", "session_initialization_failed",
            "session_connection_failed", "stream_append_failed", "stream_finalize_failed",
            "no_pcm_chunks", "file_transcription", "selected_voice_non_streaming",
            "legacy_standard_profile", "streaming_synthesis_failed",
            "auth_failed", "endpoint_not_found", "model_not_supported",
            "handshake_rejected", "session_config_rejected", "protocol_error",
            "timeout", "sdk_incompatible", "unknown_connection_error",
        } else "na"

    console_logger.info(
        "LIVE_CALL_CLIENT_LATENCY turn_id=%s speech_end_to_realtime_commit_ms=%s "
        "speech_end_to_fallback_commit_ms=%s realtime_commit_to_server_receive_ms=%s "
        "fallback_commit_to_server_receive_ms=%s "
        "realtime_commit_to_first_server_response_ms=%s client_first_audio_receive_ms=%s "
        "audio_decode_ms=%s audio_queue_wait_ms=%s audio_context_resume_ms=%s "
        "client_end_of_speech_to_audible_ms=%s response_audio_completed_ms=%s "
        "silence_to_recorder_stop_ms=%s recorder_stop_to_final_data_ms=%s "
        "final_data_to_fallback_commit_ms=%s "
        "streaming_stt_active=%s streaming_stt_fallback_reason=%s "
        "streaming_tts_active=%s streaming_tts_fallback_reason=%s "
        "pcm_capture_started=%s pcm_input_sample_rate=%s pcm_output_sample_rate=%s "
        "pcm_chunks_sent=%s pcm_bytes_sent=%s pcm_chunks_received=%s",
        turn_id,
        *(value(name) for name in CLIENT_LATENCY_METRICS),
        capability("streaming_stt_active"), reason("streaming_stt_fallback_reason"),
        capability("streaming_tts_active"), reason("streaming_tts_fallback_reason"),
        capability("pcm_capture_started"), value("pcm_input_sample_rate"),
        value("pcm_output_sample_rate"), value("pcm_chunks_sent"),
        value("pcm_bytes_sent"), value("pcm_chunks_received"),
    )


def _effective_voice(db: Session, user_id: int, relationship: str) -> str:
    user_settings = UserCRUD.get_settings(db, user_id)
    if user_settings and user_settings.preferred_voice:
        return user_settings.preferred_voice
    return StandardVoiceResolver(
        get_settings().default_standard_voice_profile
    ).resolve(relationship).value


@router.post(
    "/live-call/session",
    response_model=LiveCallSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_live_call_session(
    request: LiveCallSessionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    legacy = LegacyCRUD.get_user_legacy(
        db, request.legacy_id, current_user.user_id
    )
    if legacy is None or getattr(legacy.status, "value", legacy.status) != "active":
        raise HTTPException(status_code=404, detail="Legacy was not found.")
    # Construct cached provider clients before the WebSocket greeting path.
    # This performs no transcription, generation, embedding, or synthesis call.
    get_live_call_turn_service()
    preferences = UserCRUD.get_settings(db, current_user.user_id)
    effective_voice = _effective_voice(db, current_user.user_id, legacy.relationship)
    settings = get_settings()
    delivery = choose_live_call_delivery(
        settings, effective_voice, request.engine
    )
    session = live_call_sessions.create(
        user_id=current_user.user_id,
        legacy_id=legacy.legacy_id,
        legacy_name=legacy.display_name,
        relationship=legacy.relationship,
        effective_voice=effective_voice,
        conversation_style=(preferences.conversation_style if preferences else "natural"),
        response_length=(preferences.response_length if preferences else "balanced"),
        engine=delivery.conversation_engine,
        speech_renderer=delivery.speech_renderer,
        realtime_capable=delivery.realtime_capable,
        persona_profile=PersonaProfileService().build(
            db, legacy_id=legacy.legacy_id,
        ),
    )
    logger.info(
        "LIVE_CALL_ENGINE session_id=%s effective_voice=%s feature_enabled=%s "
        "voice_realtime_capable=%s selected_engine=%s speech_renderer=%s fallback_reason=%s",
        session.session_id, session.effective_voice, settings.live_call_realtime_enabled,
        session.realtime_capable, session.engine, session.speech_renderer, delivery.reason,
    )
    return LiveCallSessionResponse(
        session_id=session.session_id,
        transport_token=session.transport_token,
        legacy_name=session.legacy_name,
        relationship=session.relationship,
        effective_voice=session.effective_voice,
        base_delivery_profile=session.base_delivery_profile,
        state=session.state,
        conversation_style=session.conversation_style,
        response_length=session.response_length,
        expires_at=session.expires_at,
        engine=session.engine,
        engine_reason=delivery.reason,
        realtime_strict=getattr(settings, "live_call_realtime_strict", False),
        realtime_capable=session.realtime_capable,
        speech_renderer=session.speech_renderer,
        transport="webrtc" if session.engine == "realtime" else "websocket",
    )


@router.post(
    "/live-call/realtime/{session_id}/bootstrap",
    response_model=RealtimeBootstrapResponse,
)
async def bootstrap_realtime_call(
    session_id: str,
    current_user: User = Depends(get_current_user),
    provider: OpenAIRealtimeBootstrapProvider = Depends(get_realtime_bootstrap_provider),
):
    logger.debug(
        "REALTIME_BOOTSTRAP request_received=True provider_request_started=False "
        "provider_status=na client_secret_received=False model=%s voice=na "
        "success=False failure_category=none",
        get_settings().openai_realtime_model,
    )
    session = live_call_sessions.authorize_user(session_id, current_user.user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Call session was not found.")
    if session.engine != "realtime" or not get_settings().live_call_realtime_enabled:
        raise HTTPException(status_code=409, detail="Realtime is not enabled for this call.")
    try:
        credential = await provider.create(session)
    except Exception as exc:
        status_code = exc.status_code if isinstance(exc, RealtimeBootstrapError) else None
        category = exc.category if isinstance(exc, RealtimeBootstrapError) else "unknown"
        logger.exception(
            "REALTIME_BOOTSTRAP request_received=True provider_request_started=True "
            "provider_status=%s client_secret_received=False model=%s voice=%s "
            "success=False failure_category=%s",
            status_code if status_code is not None else "na", get_settings().openai_realtime_model,
            session.effective_voice, category,
        )
        headers = None
        retry_after = exc.retry_after if isinstance(exc, RealtimeBootstrapError) else None
        if retry_after is not None:
            headers = {"Retry-After": str(retry_after)}
        raise HTTPException(
            status_code=502,
            detail={"message": "Realtime call startup failed.", "code": category},
            headers=headers,
        ) from None
    return RealtimeBootstrapResponse(
        client_secret=credential["client_secret"],
        expires_at=credential.get("expires_at"),
        model=get_settings().openai_realtime_model,
        voice=session.effective_voice,
    )


@router.post(
    "/live-call/realtime/{session_id}/tool",
    response_model=RealtimeToolResponse,
)
async def execute_realtime_tool(
    session_id: str,
    request: RealtimeToolRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tools: RealtimeToolService = Depends(get_realtime_tool_service),
):
    session = live_call_sessions.authorize_user(session_id, current_user.user_id)
    if session is None or session.engine != "realtime":
        raise HTTPException(status_code=404, detail="Call session was not found.")
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                tools.execute, db, session, request.name, request.arguments,
                request.call_id,
            ),
            timeout=get_settings().live_call_realtime_tool_timeout_seconds,
        )
    except (ValueError, asyncio.TimeoutError):
        result = {"status": "error", "uncertain": True}
    except Exception:
        logger.exception(
            "live_call_realtime_tool_failed session_id=%s tool=%s",
            session_id,
            request.name,
        )
        result = {"status": "error", "uncertain": True}
    return RealtimeToolResponse(call_id=request.call_id, result=result)


@router.post(
    "/live-call/realtime/{session_id}/speech",
    response_model=RealtimeSpeechResponse,
)
async def render_realtime_external_speech(
    session_id: str,
    request: RealtimeSpeechRequest,
    current_user: User = Depends(get_current_user),
    service: LiveCallTurnService = Depends(get_live_call_turn_service),
):
    session = live_call_sessions.authorize_user(session_id, current_user.user_id)
    if session is None or session.engine != "realtime":
        raise HTTPException(status_code=404, detail="Call session was not found.")
    if session.speech_renderer not in {"external_streaming_tts", "external_nonstreaming_tts"}:
        raise HTTPException(status_code=409, detail="External speech is not enabled for this call.")
    started = monotonic()
    try:
        speech = await service.render_external_phrase(
            session, request.text.strip(), generation_id=request.generation_id,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="Speech phrase is invalid.") from None
    except Exception:
        logger.exception("live_call_external_speech_failed session_id=%s", session_id)
        raise HTTPException(status_code=502, detail="External speech rendering failed.") from None
    logger.debug(
        "LIVE_CALL_EXTERNAL_TTS session_id=%s voice=%s renderer=%s elapsed_ms=%s audio_bytes=%s",
        session_id, session.effective_voice, session.speech_renderer,
        max(0, round((monotonic() - started) * 1000)), len(speech.content),
    )
    return RealtimeSpeechResponse(
        response_id=request.response_id,
        generation_id=request.generation_id,
        audio=base64.b64encode(speech.content).decode("ascii"),
        content_type=speech.media_type,
    )


@router.delete(
    "/live-call/session/{session_id}",
    response_model=LiveCallSessionEndResponse,
)
async def end_live_call_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = live_call_sessions.end(
        session_id, user_id=current_user.user_id
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Call session was not found.")
    return LiveCallSessionEndResponse(session_id=session.session_id)


def _transport_token(websocket: WebSocket) -> str | None:
    for protocol in websocket.scope.get("subprotocols", []):
        if protocol.startswith("auth."):
            return protocol.removeprefix("auth.")
    return None


@router.websocket("/live-call/ws/{session_id}")
async def live_call_transport(
    websocket: WebSocket,
    session_id: str,
    db: Session = Depends(get_db),
):
    token = _transport_token(websocket) or ""
    session = live_call_sessions.authorize_transport(session_id, token)
    if session is None:
        transport_status = live_call_sessions.transport_status(session_id, token)
        close_code = 4404 if transport_status == "missing" else 4401
        await websocket.close(code=close_code, reason="Call session is unavailable.")
        return
    if session.engine != "cascade":
        await websocket.close(code=4409, reason="This call uses the Realtime transport.")
        return
    await websocket.accept(subprotocol="waffleberry.live-call.v1")
    live_call_sessions.mark_connected(session_id)
    greeting_trace = live_call_sessions.start_latency(session_id, 0, None)
    greeting_trace.mark("session_ready")
    recovery = live_call_sessions.recovery_state(session_id) or {}
    await websocket.send_json({
        "version": LIVE_CALL_EVENT_VERSION,
        "type": "session.ready",
        "session_id": session_id,
        **recovery,
    })
    turn_service = get_live_call_turn_service()
    turn_task: asyncio.Task | None = None
    greeting_task: asyncio.Task | None = None
    transcription_streams: dict[int, object] = {}
    transcription_stream_marks: dict[int, tuple[float, float | None]] = {}
    turn_diagnostics: dict[int, dict[str, object]] = {}

    async def safe_send(event: dict) -> bool:
        try:
            await websocket.send_json(event)
            return True
        except (WebSocketDisconnect, RuntimeError):
            return False

    async def send_error(
        code: str, turn_id: int | None = None, failure_stage: str | None = None,
    ) -> None:
        event = {"version": LIVE_CALL_EVENT_VERSION, "type": "error", "code": code}
        if turn_id is not None:
            event["turn_id"] = turn_id
        if failure_stage is not None:
            event["failure_stage"] = failure_stage
        await websocket.send_json(event)

    def failed_stage(turn_id: int) -> str:
        trace = live_call_sessions.latency_trace(session_id, turn_id)
        marks = trace.marks if trace else {}
        if "transcription_started" in marks and "transcription_completed" not in marks:
            return "stt_failed"
        if "generation_failed" in marks or (
            "generation_started" in marks and "generation_completed" not in marks
        ):
            return "generation_failed"
        if "tts_started" in marks and "tts_completed" not in marks:
            return "tts_failed"
        return "turn_processing"

    async def fail_turn_safely(turn_id: int, code: str) -> None:
        stage = failed_stage(turn_id)
        live_call_sessions.fail_turn(session_id, turn_id)
        logger.debug(
            "LIVE_CALL_FAILURE session_id=%s turn_id=%s stage=%s safe_code=%s "
            "retry_count=0 recovered=False",
            session_id, turn_id, stage, code,
        )
        await send_error(code, turn_id, stage)

    async def process_turn(
        turn_id: int, audio: bytes, content_type: str,
        final_transcript: str | None = None,
    ) -> None:
        try:
            trace = live_call_sessions.latency_trace(session_id, turn_id)
            if trace:
                trace.mark("turn_processing_started")
            if trace:
                trace.mark("response_started_sent")
            await safe_send({"version": 1, "type": "response.started", "turn_id": turn_id})
            active_session = live_call_sessions.authorize_transport(
                session_id, session.transport_token
            )
            if active_session is None:
                return
            streaming_tts_active = False
            capability = getattr(turn_service, "streaming_speech_capability", None)
            tts_capable, tts_reason = (
                capability(active_session) if callable(capability)
                else (False, "provider_fallback")
            )
            if hasattr(turn_service, "process_streaming"):
                transcript = ""
                response = ""
                async def consume_stream() -> None:
                    nonlocal transcript, response, streaming_tts_active
                    audio_index = 0
                    async for item in turn_service.process_streaming(
                        session=active_session, audio=audio, content_type=content_type,
                        history=live_call_sessions.history(session_id), db=db,
                        mark=trace.mark if trace else None,
                        record_metrics=trace.add_context_metrics if trace else None,
                        final_transcript=final_transcript,
                        turn_id=turn_id,
                    ):
                        if live_call_sessions.is_interrupted(session_id, turn_id):
                            raise asyncio.CancelledError
                        if item["type"] == "transcription":
                            transcript = str(item["text"])
                            await safe_send({"version": 1, "type": "transcription.final", "turn_id": turn_id, "text": transcript})
                        elif item["type"] == "audio":
                            speech = item["speech"]
                            if trace:
                                trace.mark("first_audio_chunk_sent")
                            await safe_send({
                                "version": 1, "type": "audio.chunk", "turn_id": turn_id,
                                "data": base64.b64encode(speech.content).decode("ascii"),
                                "mime_type": speech.media_type, "final": False,
                                "chunk_index": audio_index,
                            })
                            audio_index += 1
                        elif item["type"] == "audio_stream":
                            streaming_tts_active = True
                            chunk = item["chunk"]
                            if trace:
                                trace.mark("first_audio_chunk_sent")
                            await safe_send({
                                "version": 1, "type": "audio.chunk",
                                "turn_id": turn_id,
                                "data": base64.b64encode(chunk.content).decode("ascii"),
                                "mime_type": chunk.media_type,
                                "sample_rate": chunk.sample_rate,
                                "streaming": True, "final": False,
                                "chunk_index": audio_index,
                            })
                            audio_index += 1
                        elif item["type"] == "completed":
                            transcript = str(item["transcript"])
                            response = str(item["response"])
                await asyncio.wait_for(consume_stream(), timeout=LIVE_CALL_TURN_TIMEOUT_SECONDS)
                speech = None
            else:
                transcript, response, speech = await asyncio.wait_for(
                    turn_service.process(
                        session=active_session, audio=audio, content_type=content_type,
                        history=live_call_sessions.history(session_id), db=db,
                        turn_id=turn_id,
                    ),
                    timeout=LIVE_CALL_TURN_TIMEOUT_SECONDS,
                )
            if (live_call_sessions.authorize_transport(session_id, session.transport_token) is None
                    or live_call_sessions.is_interrupted(session_id, turn_id)):
                return
            if speech is not None:
                await safe_send({"version": 1, "type": "transcription.final", "turn_id": turn_id, "text": transcript})
            if live_call_sessions.is_interrupted(session_id, turn_id):
                return
            if trace:
                trace.mark("first_text_delta_sent")
            await safe_send({"version": 1, "type": "response.text.delta", "turn_id": turn_id, "text": response, "final": True})
            if live_call_sessions.is_interrupted(session_id, turn_id):
                return
            if speech is not None:
                if trace:
                    trace.mark("first_audio_chunk_sent")
                await safe_send({"version": 1, "type": "audio.chunk", "turn_id": turn_id, "data": base64.b64encode(speech.content).decode("ascii"), "mime_type": speech.media_type, "final": True, "chunk_index": 0})
            if not live_call_sessions.complete_turn(session_id, turn_id, transcript, response):
                return
            if trace:
                trace.mark("response_completed")
            await safe_send({
                "version": 1, "type": "response.completed", "turn_id": turn_id,
                "latency": trace.metrics() if trace else {},
                "streaming_stt_active": final_transcript is not None,
                "streaming_stt_fallback_reason": (
                    "none" if final_transcript is not None else turn_diagnostics.get(
                        turn_id, {}
                    ).get("streaming_stt_fallback_reason", "file_transcription")
                ),
                "streaming_tts_active": streaming_tts_active,
                "streaming_tts_fallback_reason": (
                    "none" if streaming_tts_active else tts_reason
                ),
                "pcm_chunks_received": turn_diagnostics.get(turn_id, {}).get(
                    "pcm_chunks_received", 0
                ),
            })
            if trace:
                diagnostics = turn_diagnostics.get(turn_id, {})
                diagnostics.update({
                    "voice_capability": "streaming" if tts_capable else "non_streaming",
                    "streaming_tts_selected": tts_capable,
                    "streaming_tts_active": streaming_tts_active,
                    "streaming_tts_fallback_reason": "none" if streaming_tts_active else tts_reason,
                })
                _log_turn_latency(session_id, turn_id, trace.metrics(), diagnostics)
        except AudioValidationError as exc:
            await fail_turn_safely(turn_id, exc.code)
        except asyncio.CancelledError:
            live_call_sessions.fail_turn(session_id, turn_id)
            raise
        except (AITimeoutError, asyncio.TimeoutError):
            await fail_turn_safely(turn_id, "turn_timeout")
        except Exception:
            await fail_turn_safely(turn_id, "turn_failed")

    async def process_greeting() -> None:
        try:
            await safe_send({"version": 1, "type": "greeting.started"})
            active_session = live_call_sessions.authorize_transport(session_id, token)
            if active_session is None:
                return
            greeting_trace.mark("greeting_tts_started")
            _, speech = await asyncio.wait_for(
                turn_service.greeting(session=active_session, turn_id=0),
                timeout=LIVE_CALL_TURN_TIMEOUT_SECONDS,
            )
            greeting_trace.mark("greeting_first_audio")
            live_call_sessions.complete_greeting(session_id)
            await safe_send({
                "version": 1, "type": "greeting.audio",
                "data": base64.b64encode(speech.content).decode("ascii"),
                "mime_type": speech.media_type,
            })
            await safe_send({"version": 1, "type": "greeting.completed"})
        except Exception:
            live_call_sessions.complete_greeting(session_id)
            await safe_send({"version": 1, "type": "greeting.failed"})

    try:
        while True:
            payload = await websocket.receive_text()
            try:
                event = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                await websocket.send_json({
                    "version": LIVE_CALL_EVENT_VERSION,
                    "type": "error",
                    "code": "malformed_event",
                })
                continue
            if not isinstance(event, dict):
                await websocket.send_json({
                    "version": LIVE_CALL_EVENT_VERSION,
                    "type": "error",
                    "code": "malformed_event",
                })
                continue
            if event.get("version") != LIVE_CALL_EVENT_VERSION:
                await websocket.send_json({
                    "version": LIVE_CALL_EVENT_VERSION,
                    "type": "error",
                    "code": "unsupported_event_version",
                })
                continue
            event_type = event.get("type")
            if event_type == "session.end":
                if turn_task is not None and not turn_task.done():
                    turn_task.cancel()
                if greeting_task is not None and not greeting_task.done():
                    greeting_task.cancel()
                for stream in transcription_streams.values():
                    await stream.close()
                transcription_streams.clear()
                live_call_sessions.end_transport(session_id)
                await websocket.send_json({
                    "version": LIVE_CALL_EVENT_VERSION,
                    "type": "session.ended",
                })
                await websocket.close(code=1000)
                return
            if event_type == "session.start":
                greeting_claimed = live_call_sessions.claim_greeting(session_id)
                recovery = live_call_sessions.recovery_state(session_id) or {}
                await websocket.send_json({
                    "version": LIVE_CALL_EVENT_VERSION,
                    "type": "session.ready",
                    "session_id": session_id,
                    **recovery,
                })
                if greeting_claimed:
                    greeting_task = asyncio.create_task(process_greeting())
                continue
            if event_type == "heartbeat.ping":
                if set(event) - {"version", "type", "heartbeat_id"} or not isinstance(event.get("heartbeat_id"), int):
                    await send_error("malformed_heartbeat")
                    continue
                if live_call_sessions.authorize_transport(session_id, token) is None:
                    await send_error("session_expired")
                    await websocket.close(code=4401, reason="Call session expired.")
                    return
                await websocket.send_json({
                    "version": LIVE_CALL_EVENT_VERSION,
                    "type": "heartbeat.pong",
                    "heartbeat_id": event["heartbeat_id"],
                    **(live_call_sessions.recovery_state(session_id) or {}),
                })
                continue
            if event_type == "latency.playback_started":
                turn_id = event.get("turn_id")
                if not isinstance(turn_id, int) or set(event) - {"version", "type", "turn_id"}:
                    await send_error("malformed_event")
                    continue
                trace = live_call_sessions.latency_trace(session_id, turn_id)
                if trace:
                    trace.mark("playback_started")
                continue
            if event_type == "latency.frontend_first_playable_chunk":
                turn_id = event.get("turn_id")
                if not isinstance(turn_id, int) or set(event) - {"version", "type", "turn_id"}:
                    await send_error("malformed_event")
                    continue
                trace = live_call_sessions.latency_trace(session_id, turn_id)
                if trace:
                    trace.mark("frontend_first_playable_chunk")
                continue
            if event_type == "latency.client_turn":
                allowed = {
                    "version", "type", "turn_id", *CLIENT_LATENCY_METRICS,
                    "streaming_stt_active", "streaming_stt_fallback_reason",
                    "streaming_tts_active", "streaming_tts_fallback_reason",
                    "pcm_capture_started", "pcm_input_sample_rate",
                    "pcm_output_sample_rate", "pcm_chunks_sent",
                    "pcm_bytes_sent", "pcm_chunks_received",
                }
                turn_id = event.get("turn_id")
                if not isinstance(turn_id, int) or set(event) - allowed:
                    await send_error("malformed_event")
                    continue
                _log_client_latency(turn_id, event)
                continue
            if event_type == "latency.greeting_playback_started":
                if set(event) - {"version", "type"}:
                    await send_error("malformed_event")
                    continue
                greeting_trace.mark("greeting_playback_started")
                metrics = greeting_trace.metrics()
                logger.info(
                    "LIVE_CALL_STARTUP_LATENCY session=%s ready_to_tts_ms=%s "
                    "ready_to_first_audio_ms=%s ready_to_playback_ms=%s",
                    session_id,
                    metrics.get("session_ready_to_greeting_tts_ms"),
                    metrics.get("session_ready_to_greeting_audio_ms"),
                    metrics.get("session_ready_to_greeting_playback_ms"),
                )
                continue
            if event_type == "interrupt":
                turn_id = event.get("turn_id")
                if not isinstance(turn_id, int) or turn_id < 1:
                    await send_error("malformed_event")
                    continue
                error = live_call_sessions.interrupt_turn(session_id, turn_id)
                if error:
                    await send_error(error, turn_id)
                    continue
                if turn_task is not None and not turn_task.done():
                    turn_task.cancel()
                await websocket.send_json({
                    "version": LIVE_CALL_EVENT_VERSION,
                    "type": "response.interrupted",
                    "turn_id": turn_id,
                })
                continue
            if event_type == "audio.cancel":
                turn_id = event.get("turn_id")
                if not isinstance(turn_id, int) or set(event) - {"version", "type", "turn_id"}:
                    await send_error("malformed_event")
                    continue
                live_call_sessions.fail_turn(session_id, turn_id)
                stream = transcription_streams.pop(turn_id, None)
                transcription_stream_marks.pop(turn_id, None)
                if stream is not None:
                    await stream.close()
                continue
            if event_type == "transcription.audio":
                turn_id = event.get("turn_id")
                if not isinstance(turn_id, int) or turn_id < 1:
                    await send_error("malformed_event")
                    continue
                recovery = live_call_sessions.recovery_state(session_id) or {}
                if turn_id not in {recovery.get("next_turn_id"), recovery.get("active_turn_id")}:
                    await send_error("stale_turn", turn_id)
                    continue
                try:
                    chunk = base64.b64decode(event.get("data", ""), validate=True)
                except (ValueError, TypeError, binascii.Error):
                    await send_error("malformed_audio", turn_id)
                    continue
                if not chunk or len(chunk) > MAX_LIVE_CALL_AUDIO_CHUNK_BYTES or len(chunk) % 2:
                    await send_error("malformed_audio", turn_id)
                    continue
                diagnostics = turn_diagnostics.setdefault(turn_id, {
                    "streaming_stt_configured": True,
                    "streaming_stt_backend_capable": bool(getattr(
                        turn_service, "supports_streaming_transcription", False
                    )),
                    "streaming_stt_session_created": False,
                    "streaming_stt_chunks_received": False,
                    "pcm_chunks_received": 0,
                })
                diagnostics["pcm_chunks_received"] = int(
                    diagnostics["pcm_chunks_received"]
                ) + 1
                diagnostics["streaming_stt_chunks_received"] = True
                if turn_id not in transcription_streams:
                    try:
                        transcription_streams[turn_id] = await asyncio.wait_for(
                            turn_service.start_transcription_stream("audio/L16"),
                            STREAMING_STT_START_TIMEOUT_SECONDS,
                        )
                        transcription_stream_marks[turn_id] = (monotonic(), None)
                        diagnostics["streaming_stt_session_created"] = True
                    except Exception as exc:
                        diagnostics["streaming_stt_fallback_reason"] = getattr(
                            exc, "safe_category", "unknown_connection_error"
                        )
                        diagnostics["streaming_stt_status_code"] = getattr(
                            exc, "status_code", None
                        )
                        continue
                try:
                    partial = await asyncio.wait_for(
                        transcription_streams[turn_id].append_audio(chunk),
                        STREAMING_STT_CHUNK_TIMEOUT_SECONDS,
                    )
                    if isinstance(partial, str) and partial.strip():
                        started, first = transcription_stream_marks[turn_id]
                        transcription_stream_marks[turn_id] = (started, first or monotonic())
                        await safe_send({
                            "version": 1, "type": "transcription.partial",
                            "turn_id": turn_id, "text": partial.strip(),
                        })
                except Exception:
                    diagnostics["streaming_stt_fallback_reason"] = "stream_append_failed"
                    stream = transcription_streams.pop(turn_id, None)
                    transcription_stream_marks.pop(turn_id, None)
                    if stream is not None:
                        await stream.close()
                continue
            if event_type == "transcription.commit":
                turn_id = event.get("turn_id")
                vad_silence_ms = event.get("vad_silence_ms")
                if (not isinstance(turn_id, int)
                        or set(event) - {"version", "type", "turn_id", "vad_silence_ms"}):
                    await send_error("malformed_event")
                    continue
                stream = transcription_streams.pop(turn_id, None)
                marks = transcription_stream_marks.pop(turn_id, None)
                diagnostics = turn_diagnostics.setdefault(turn_id, {})
                valid_vad = vad_silence_ms if (
                    isinstance(vad_silence_ms, int) and 0 <= vad_silence_ms <= 5000
                ) else None
                trace = live_call_sessions.start_latency(
                    session_id, turn_id, valid_vad, commit_kind="realtime"
                )
                await safe_send({
                    "version": 1, "type": "latency.commit_received",
                    "turn_id": turn_id, "commit_kind": "realtime",
                })
                if stream is None:
                    diagnostics.setdefault("streaming_stt_fallback_reason", "no_pcm_chunks")
                    continue
                try:
                    final_transcript = await asyncio.wait_for(
                        stream.finalize(), STREAMING_STT_FINAL_TIMEOUT_SECONDS,
                    )
                    committed = live_call_sessions.commit_streaming_transcript(
                        session_id, turn_id
                    )
                    if isinstance(committed, str):
                        await send_error(committed, turn_id)
                        continue
                    if marks:
                        trace.marks.setdefault("transcription_started", marks[0])
                        if marks[1] is not None:
                            trace.marks.setdefault("transcription_first_partial", marks[1])
                    trace.mark("transcription_completed")
                    diagnostics["streaming_stt_fallback_reason"] = "none"
                    turn_task = asyncio.create_task(process_turn(
                        turn_id, *committed, final_transcript=final_transcript,
                    ))
                except Exception:
                    diagnostics["streaming_stt_fallback_reason"] = "stream_finalize_failed"
                finally:
                    await stream.close()
                continue
            if event_type in {"audio.chunk", "audio.commit"}:
                turn_id = event.get("turn_id")
                if not isinstance(turn_id, int) or turn_id < 1:
                    await send_error("malformed_event")
                    continue
                if event_type == "audio.chunk":
                    if event.get("start") is True:
                        content_type = event.get("mime_type")
                        if not isinstance(content_type, str):
                            await send_error("malformed_audio", turn_id)
                            continue
                        error = live_call_sessions.begin_turn(session_id, turn_id, content_type)
                        if error:
                            await send_error(error, turn_id)
                            continue
                    try:
                        chunk = base64.b64decode(event.get("data", ""), validate=True)
                    except (ValueError, TypeError, binascii.Error):
                        await send_error("malformed_audio", turn_id)
                        continue
                    if len(chunk) > MAX_LIVE_CALL_AUDIO_CHUNK_BYTES:
                        live_call_sessions.fail_turn(session_id, turn_id)
                        await send_error("audio_chunk_too_large", turn_id)
                        continue
                    error = live_call_sessions.append_audio(session_id, turn_id, chunk)
                    if error:
                        if error == "audio_too_large":
                            live_call_sessions.fail_turn(session_id, turn_id)
                        await send_error(error, turn_id)
                    continue
                vad_silence_ms = event.get("vad_silence_ms")
                if not isinstance(vad_silence_ms, int) or not 0 <= vad_silence_ms <= 5000:
                    vad_silence_ms = None
                committed = live_call_sessions.commit_audio(session_id, turn_id)
                if isinstance(committed, str):
                    if committed == "already_committed":
                        live_call_sessions.start_latency(
                            session_id, turn_id, vad_silence_ms, commit_kind="fallback"
                        )
                        await safe_send({
                            "version": 1, "type": "latency.commit_received",
                            "turn_id": turn_id, "commit_kind": "fallback",
                        })
                        continue
                    if committed == "audio_empty":
                        live_call_sessions.fail_turn(session_id, turn_id)
                    await send_error(committed, turn_id)
                    continue
                live_call_sessions.start_latency(
                    session_id, turn_id, vad_silence_ms, commit_kind="fallback"
                )
                await safe_send({
                    "version": 1, "type": "latency.commit_received",
                    "turn_id": turn_id, "commit_kind": "fallback",
                })
                final_transcript = None
                stream = transcription_streams.pop(turn_id, None)
                marks = transcription_stream_marks.pop(turn_id, None)
                if stream is not None:
                    try:
                        final_transcript = await asyncio.wait_for(
                            stream.finalize(), STREAMING_STT_FINAL_TIMEOUT_SECONDS,
                        )
                        trace = live_call_sessions.latency_trace(session_id, turn_id)
                        if trace and marks:
                            trace.marks.setdefault("transcription_started", marks[0])
                            if marks[1] is not None:
                                trace.marks.setdefault("transcription_first_partial", marks[1])
                            trace.mark("transcription_completed")
                    except Exception:
                        final_transcript = None
                    finally:
                        await stream.close()
                turn_task = asyncio.create_task(process_turn(
                    turn_id, *committed, final_transcript=final_transcript,
                ))
                continue
            await websocket.send_json({
                "version": LIVE_CALL_EVENT_VERSION,
                "type": "error",
                "code": "unsupported_event_type",
            })
    except (WebSocketDisconnect, ValueError, TypeError):
        # A transport can disappear temporarily. The process-local session remains
        # authoritative and resumable until explicit end or expiry. Provider work
        # already started is allowed to settle without turning a retry into a
        # duplicate generation.
        if turn_task is not None and not turn_task.done():
            try:
                await turn_task
            except (asyncio.CancelledError, RuntimeError):
                pass
        if greeting_task is not None and not greeting_task.done():
            try:
                await greeting_task
            except (asyncio.CancelledError, RuntimeError):
                pass
        for stream in transcription_streams.values():
            try:
                await stream.close()
            except Exception:
                pass
        transcription_streams.clear()
        recovery = live_call_sessions.recovery_state(session_id) or {}
        active_turn_id = recovery.get("active_turn_id")
        if isinstance(active_turn_id, int) and (turn_task is None or turn_task.done()):
            # Incremental microphone chunks are ephemeral. An uncommitted turn
            # cannot be resumed safely after transport loss.
            live_call_sessions.fail_turn(session_id, active_turn_id)
