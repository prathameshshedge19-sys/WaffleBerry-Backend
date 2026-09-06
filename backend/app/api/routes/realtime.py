"""Fenced live input: bounded PCM and provider-final L14 user admission only."""
import asyncio
import base64
import binascii
import json
import logging
import time
import anyio
from collections import deque
from contextlib import suppress
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.schemas.realtime import AudioFrame, Authenticate, Control, PlaybackControl, SessionCreate
from app.models.turn import ConversationTurn
from app.services import realtime_responses as responses
from app.services import realtime_brain as brain
from app.services.web_search import get_web_search_provider
from app.services.memory import get_memory_provider
from app.services import realtime_transcripts as transcripts
from app.services import realtime_sessions as sessions, turn_observability as obs
from app.services.realtime_provider import get_realtime_provider
from app.services.security import decode_token

router = APIRouter(prefix="/realtime", tags=["realtime foundation"])


def available(settings):
    if not settings.realtime_enabled:
        raise sessions.RealtimeError("realtime_not_available", 503)


def public_error(error):
    return {"code": error.code, "message": "Live connection unavailable." if error.code != "realtime_setup_incomplete" else "Complete Legacy setup before starting live voice."}


@router.get("/capabilities")
def capabilities(user: User = Depends(get_current_user)):
    """Read-only product entry gate; session creation still authorizes scope."""
    return {"enabled": get_settings().realtime_enabled}


@router.post("/sessions", status_code=201)
def create_session(payload: SessionCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = get_settings()
    try:
        available(settings)
        origin = sessions.validate_origin(request.headers.get("origin"), settings)
        claims = decode_token(request.headers["authorization"].split(" ", 1)[1], "access")
        result = sessions.authorize(db, user.id, payload, origin, claims, settings)
        with obs.bound(obs.TurnObservation(session_id=result["session_id"])):
            obs.emit("realtime_session_authorized", level=logging.INFO)
        return result
    except sessions.RealtimeError as error:
        db.rollback()
        raise HTTPException(error.status, public_error(error)) from None


@router.post("/sessions/{session_id}/reconnect")
def reconnect(session_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = get_settings()
    try:
        available(settings)
        origin = sessions.validate_origin(request.headers.get("origin"), settings)
        result = sessions.reconnect(db, session_id, user.id, origin, settings)
        obs.emit("realtime_reconnect")
        return result
    except sessions.RealtimeError as error:
        db.rollback()
        raise HTTPException(error.status, public_error(error)) from None


async def receive_json(websocket, limit, timeout):
    message = await asyncio.wait_for(websocket.receive(), timeout)
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    text = message.get("text")
    if not isinstance(text, str) or len(text.encode("utf-8")) > limit:
        raise sessions.RealtimeError("realtime_protocol_error", 400)
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        raise sessions.RealtimeError("realtime_protocol_error", 400) from None


async def send(websocket, settings, event):
    await asyncio.wait_for(websocket.send_json(event), settings.realtime_io_timeout_seconds)


async def database_call(function, *args, **kwargs):
    # asyncio Task cancellation can detach a threadpool call. Wait for the DB
    # operation to finish before teardown reuses/closes this SQLAlchemy session.
    with anyio.CancelScope(shield=True):
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Direct Task.cancel and AnyIO level cancellation are distinct.
            while not task.done():
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(task)
            with suppress(Exception):
                task.result()
            raise


async def bridge(websocket, provider, db, session_id, owner, generation, settings, memory_provider, web_provider):
    """One bounded mailbox, one DB owner. Readers never mutate session state."""
    inbox = asyncio.Queue(maxsize=settings.realtime_queue_depth)
    preparation = None
    active = None
    retired = {}  # bounded by the existing 256 admitted provider input identities

    async def interrupt():
        nonlocal active, preparation
        output, active = active, None  # fence before cancellation or database I/O
        if output is None:
            return
        output.retired = True
        retired[output.claim] = output.response_id
        if preparation:
            preparation.cancel()
            await asyncio.gather(preparation, return_exceptions=True)
            preparation = None
        result = await database_call(responses.terminate, db, session_id, owner, generation,
                                     output.turn_id, output.claim, settings)
        if result:
            await send(websocket, settings, {**result, **output.binding()})
        start = time.monotonic()
        if output.brain and output.brain.planner_id and not output.brain.planner_done:
            await asyncio.wait_for(provider.cancel(output.brain.planner_id), settings.realtime_io_timeout_seconds)
        if output.response_id and not output.provider_done:
            await asyncio.wait_for(provider.cancel(output.response_id), settings.realtime_io_timeout_seconds)
        output.telemetry("realtime_cancel_dispatched", duration_ms=(time.monotonic() - start) * 1000)
        output.telemetry("realtime_response_interrupted")

    async def prepare(output):
        # Independent session, never the controller's session across an await.
        with Session(bind=db.get_bind(), expire_on_commit=False, autoflush=False) as preparation_db:
            try:
                result = await asyncio.wait_for(transcripts.prepare_admitted(
                    preparation_db, session_id, owner, generation, output.turn_id, settings, memory_provider), 30)
                output.brain = brain.bind(preparation_db, output, owner, settings, result)
                await enqueue_wait(("prepared", (output, result)))
            except asyncio.CancelledError:
                raise
            except Exception:
                await enqueue_wait(("preparation_failed", output))

    async def tools(output):
        try:
            await asyncio.wait_for(brain.execute_tools(db.get_bind(), output, owner, settings, memory_provider, web_provider), 30)
            await enqueue_wait(("tools_ready", output))
        except asyncio.CancelledError:
            raise
        except sessions.RealtimeError as error:
            await enqueue_wait(("tool_error", (output, error)))
        except Exception:
            await enqueue_wait(("preparation_failed", output))

    def enqueue(value):
        try:
            inbox.put_nowait(value)
        except asyncio.QueueFull:
            raise sessions.RealtimeError("realtime_queue_overrun", 429) from None

    async def enqueue_wait(value):
        try:
            await asyncio.wait_for(inbox.put(value), settings.realtime_io_timeout_seconds)
        except TimeoutError:
            raise sessions.RealtimeError("realtime_queue_overrun", 429) from None

    async def browser_reader():
        times = deque(maxlen=settings.realtime_messages_per_second)
        sequence = 0
        while True:
            try:
                value = await receive_json(websocket, settings.realtime_message_bytes, settings.realtime_idle_seconds)
            except TimeoutError:
                raise sessions.RealtimeError("realtime_idle_timeout", 408) from None
            tick = time.monotonic()
            if len(times) == times.maxlen and tick - times[0] < 1:
                raise sessions.RealtimeError("realtime_rate_limit", 429)
            times.append(tick)
            try:
                if isinstance(value, dict) and value.get("type") == "audio_frame":
                    frame = AudioFrame.model_validate(value)
                    pcm = base64.b64decode(frame.pcm, validate=True)
                    if frame.sequence != sequence or not pcm or len(pcm) % 2 or len(pcm) > min(settings.realtime_frame_bytes, 4800):
                        raise ValueError("Invalid frame")
                    sequence += 1
                    await enqueue_wait(("audio", pcm))
                    continue
                if isinstance(value, dict) and value.get("type") in {"interrupt", "playback_started", "playback_progress", "playback_drained"}:
                    enqueue(("playback", PlaybackControl.model_validate(value).model_dump()))
                    continue
                control = Control.model_validate(value)
            except (ValidationError, ValueError, binascii.Error):
                raise sessions.RealtimeError("realtime_protocol_error", 400) from None
            enqueue(("browser", control.type))

    async def provider_reader():
        while True:
            event = await provider.receive()
            if event is not None:
                # Provider bursts apply bounded transport backpressure rather
                # than failing a healthy audio stream at the first full slot.
                await enqueue_wait(("provider", event))

    async def controller():
        nonlocal active, preparation
        order = transcripts.TranscriptOrder(generation, settings.realtime_queue_depth)
        ending = None
        checked_at = 0
        while True:
            if preparation and preparation.done() and not preparation.cancelled():
                preparation.result()  # surface a failed bounded handoff promptly
            if ending is not None and time.monotonic() >= ending:
                if order.unfinished:
                    await send(websocket, settings, {"type": "utterance_failed", "code": "realtime_unfinished_speech",
                                                     "message": "Unfinished speech was not saved. Please repeat it."})
                return "client_end"
            # At most one second to observe remote revocation/expiry while idle.
            if time.monotonic() - checked_at >= 1:
                await database_call(sessions.owned, db, session_id, owner, generation, settings)
                checked_at = time.monotonic()
            try:
                source, event = await asyncio.wait_for(inbox.get(), 1)
            except TimeoutError:
                continue
            if source in {"browser", "prepared", "tools_ready"} or (source == "playback" and event["type"] in {"interrupt", "playback_drained"}):
                await database_call(sessions.owned, db, session_id, owner, generation, settings)
                checked_at = time.monotonic()
            if active and time.monotonic() - active.created_at > 150:
                await interrupt()
            if source == "prepared":
                output, prepared = event
                if active is output and not output.retired and ending is None:
                    await asyncio.wait_for(provider.plan_response(output.brain, output.claim,
                        turn_id=output.turn_id, session_id=session_id), settings.realtime_io_timeout_seconds)
                continue
            if source == "tools_ready":
                output = event
                if active is output and not output.retired and ending is None:
                    await asyncio.wait_for(provider.create_response(output.brain.prepared, output.claim,
                        turn_id=output.turn_id, session_id=session_id, continuation=output.brain.continuation),
                        settings.realtime_io_timeout_seconds)
                continue
            if source == "tool_error":
                output, error = event
                if active is output and not output.retired:
                    raise error
                obs.emit("realtime_stale_discard")
                continue
            if source == "preparation_failed":
                if active is event:
                    await interrupt()
                continue
            if source == "playback":
                if active is None:
                    obs.emit("realtime_stale_discard")
                    continue
                if event["type"] == "interrupt":
                    # A locally detected interruption can precede response.created.
                    expected = active.binding()
                    if all(event.get(k) == v for k, v in expected.items() if k != "response_id"):
                        await interrupt()
                else:
                    proof = active.acknowledge(event)
                    if proof:
                        output = active
                        result = await database_call(responses.terminate, db, session_id, owner, generation,
                                                     output.turn_id, output.claim, settings, proof,
                                                     current=output.brain.current)
                        output.retired = True
                        retired[output.claim] = output.response_id
                        active = None
                        if result:
                            await database_call(brain.finalize, db.get_bind(), output, owner, settings)
                            output.telemetry("realtime_response_completed")
                            await send(websocket, settings, {**result, **output.binding()})
                continue
            if source == "browser":
                if event == "end_call":
                    await interrupt()
                    if not order.unfinished:
                        return "client_end"
                    ending = ending or time.monotonic() + 1
                    continue
                await send(websocket, settings, {"type": "pong"})
            elif source == "audio":
                if ending is None:
                    await asyncio.wait_for(provider.append_audio(event), settings.realtime_io_timeout_seconds)
            elif event.generation == generation:
                if event.kind == "error":
                    raise sessions.RealtimeError("realtime_provider_failed", 502)
                if event.kind == "speech_started" and active:
                    await interrupt()
                if event.kind in {"response_created", "response_done", "audio", "audio_done",
                                  "output_transcript_delta", "output_transcript_done", "function_delta", "function_done"}:
                    if event.kind == "response_created":
                        response = event.payload.get("response", {})
                        claim = response.get("metadata", {}).get("generation_id")
                        if claim in retired:
                            # Cancellation while response.create was still in flight.
                            await asyncio.wait_for(provider.cancel(response.get("id")), settings.realtime_io_timeout_seconds)
                            obs.emit("realtime_stale_discard")
                            continue
                        if active is None or claim != active.claim:
                            raise sessions.RealtimeError("realtime_provider_failed", 502)
                        if response.get("metadata", {}).get("phase") == "tools":
                            if active.brain is None or active.brain.planner_id is not None:
                                raise sessions.RealtimeError("realtime_provider_failed", 502)
                            active.brain.planner_id = transcripts.item_identity(response.get("id"))
                            continue
                        if active.brain is None or not active.brain.planner_done:
                            raise sessions.RealtimeError("realtime_provider_failed", 502)
                    rid = event.payload.get("response", {}).get("id") if event.kind == "response_done" else event.payload.get("response_id")
                    if active and active.brain and rid == active.brain.planner_id:
                        if active.brain.planner_done:
                            obs.emit("realtime_stale_discard")
                            continue
                        if event.kind == "response_done":
                            active.brain.accept_calls(event.payload["response"])
                            preparation = asyncio.create_task(tools(active))
                        elif event.kind not in {"function_delta", "function_done"}:
                            raise sessions.RealtimeError("realtime_provider_failed", 502)
                        continue
                    if active:
                        for outgoing in active.provider(event):
                            if outgoing["type"] == "generation_failed":
                                await interrupt()
                            else:
                                await send(websocket, settings, outgoing)
                    else:
                        if not retired and event.kind in {"audio", "function_delta", "function_done"}:
                            raise sessions.RealtimeError("realtime_provider_failed", 502)
                        obs.emit("realtime_stale_discard")
                    continue
                for outgoing in order.observe(event):
                    await send(websocket, settings, outgoing)
                for identity, final, failed in order.ready():
                    code = "realtime_transcription_failed" if failed else "realtime_transcript_empty" if not final else None
                    result = None
                    if code is None:
                        try:
                            result = await database_call(transcripts.admit, db, session_id, owner, generation,
                                                         identity, final, settings)
                        except sessions.RealtimeError as error:
                            if error.code != "realtime_turn_busy":
                                raise
                            code = error.code
                    if result is not None:
                        await send(websocket, settings, {**result, "item_id": identity})
                        if not result["replayed"] and ending is None:
                            turn = db.get(ConversationTurn, result["turn_id"])
                            active = responses.Output(session_id, generation, turn.id, turn.claim_token)
                            db.rollback()
                            await send(websocket, settings, dict(type="assistant_thinking", **active.binding()))
                            preparation = asyncio.create_task(prepare(active))
                    else:
                        await send(websocket, settings, {"type": "utterance_failed", "item_id": identity, "code": code,
                            "message": "Speech was not saved. Finish the active turn or end this call, then repeat it."
                                       if code == "realtime_turn_busy" else "Speech could not be transcribed. Please repeat it."})

    tasks = [asyncio.create_task(fn()) for fn in (browser_reader, provider_reader, controller)]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        results = [task.result() for task in done]
        return next((value for value in results if value), "browser_disconnect")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if preparation:
            preparation.cancel()
            await asyncio.gather(preparation, return_exceptions=True)


@router.websocket("/connect")
async def connect(websocket: WebSocket, db: Session = Depends(get_db), provider=Depends(get_realtime_provider),
                  memory_provider=Depends(get_memory_provider), web_provider=Depends(get_web_search_provider)):
    settings = get_settings()
    session_id = None
    generation = None
    owner = str(uuid4())
    reason = "backend_error"
    observation = obs.TurnObservation()
    accepted = False
    try:
        available(settings)
        origin = sessions.validate_origin(websocket.headers.get("origin"), settings)
        # Query strings and alternate credential subprotocols have no role here.
        if websocket.url.query or websocket.headers.get("sec-websocket-protocol"):
            raise sessions.RealtimeError("realtime_protocol_error", 400)
        await websocket.accept()
        accepted = True
        try:
            auth = Authenticate.model_validate(await receive_json(websocket, 512, settings.realtime_auth_timeout_seconds))
        except (ValidationError, TimeoutError):
            raise sessions.RealtimeError("realtime_protocol_error", 400) from None
        session_id, generation, voice = await database_call(sessions.consume, db, auth.ticket, origin, owner, settings)
        observation.session_id = session_id
        with obs.bound(observation):
            obs.emit("realtime_websocket_connect")
            await send(websocket, settings, {"type": "connecting", "session_id": session_id})
            with obs.stage("realtime_provider_connect"):
                await asyncio.wait_for(provider.connect(voice, generation), settings.realtime_io_timeout_seconds + 1)
            await database_call(sessions.owned, db, session_id, owner, generation, settings, ready=True)
            await send(websocket, settings, {"type": "ready", "session_id": session_id, "generation": generation})
            for result in await database_call(transcripts.reconcile, db, session_id, owner, generation, settings):
                await send(websocket, settings, result)
            obs.emit("realtime_session_ready", level=logging.INFO)
            reason = await bridge(websocket, provider, db, session_id, owner, generation, settings, memory_provider, web_provider)
    except WebSocketDisconnect:
        reason = "browser_disconnect"
    except sessions.RealtimeError as error:
        reason = {"realtime_provider_connection": "provider_disconnect", "realtime_provider_failed": "provider_failed",
                  "realtime_access_changed": "access_changed", "realtime_session_expired": "session_expired",
                  "realtime_rate_limit": "rate_limit", "realtime_queue_overrun": "queue_overrun",
                  "realtime_idle_timeout": "idle_timeout"}.get(error.code, "protocol_error")
        db.rollback()
        if error.code == "realtime_ticket_used":
            obs.emit("realtime_ticket_replay")
        if reason == "queue_overrun":
            obs.emit("realtime_queue_overrun")
        if reason == "provider_disconnect":
            obs.emit("realtime_provider_disconnect")
        if accepted:
            with suppress(Exception):
                await send(websocket, settings, {"type": "error", **public_error(error)})
    except asyncio.CancelledError:
        reason = "backend_error"
        raise
    except Exception:
        db.rollback()
        if accepted:
            with suppress(Exception):
                await send(websocket, settings, {"type": "error", "code": "realtime_not_available", "message": "Live connection unavailable."})
    finally:
        # Every exit cancels I/O and fences owned metadata. A stale worker cannot
        # terminate a replacement generation. The sweeper handles process death.
        with anyio.CancelScope(shield=True):
            with suppress(Exception):
                await asyncio.wait_for(provider.cancel(), 2)
            with suppress(Exception):
                # Readers have stopped. Drain terminal usage without forwarding
                # any cancelled output; never delay teardown indefinitely.
                await asyncio.wait_for(provider.drain_cancelled(), 1.5)
            with suppress(Exception):
                await asyncio.wait_for(provider.close(), 3)
            if session_id is not None:
                try:
                    await database_call(sessions.close_owned, db, session_id, owner, generation, reason, settings)
                except Exception:
                    db.rollback()  # persistent lease expiry remains the recovery path
            with obs.bound(observation):
                obs.emit("realtime_session_ended", dimensions={"realtime_end_reason": reason})
            if accepted:
                with suppress(Exception):
                    await send(websocket, settings, {"type": "ended", "reason": reason})
            with suppress(Exception):
                await websocket.close(code=1000 if reason == "client_end" else 1008)
