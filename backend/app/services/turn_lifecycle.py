"""Durable admission and one-shot claims, independent of message transactions.

No lease stealing: a process crash leaves a visible pending/streaming row for
operator reconciliation. Reusing an active or failed key never calls a provider.
The route guard also covers preparation failures and iterator cancellation.
"""
import asyncio
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.services import turn_observability as obs, usage_accounting as usage
from app.models.conversation import Conversation, Message, MessageRole
from app.models.turn import ConversationTurn


def _now():
    return datetime.now(timezone.utc)


def control_commit(db):
    """Mark infrastructure-only commits for transaction-boundary regression tests."""
    db.info["turn_control_commit"] = True
    try:
        db.commit()
    finally:
        db.info.pop("turn_control_commit", None)


def digest_request(conversation, payload, timezone_name="UTC"):
    # Conversation IDs are globally unique; Legacy is checked by the authorized
    # lookup below. Omitting the mutable historical NULL-to-Legacy association
    # keeps retries stable when a pre-L2 conversation is first initialized.
    values = dict(conversation_id=conversation.id,
                  actor_user_id=conversation.user_id, mode=conversation.mode,
                  content=payload.content.strip(), input_mode=payload.input_mode,
                  timezone=timezone_name)
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def conflict(code):
    obs.failed("turn_already_processing" if code == "turn_in_progress" else "turn_conflict")
    raise HTTPException(409, detail={"code": code, "message": "This turn key cannot start another response."})


def accept_turn(db, conversation, payload, *, streaming=False, timezone_name="UTC", atomic_admission=False):
    """Call only after route scope authorization, before staging any message.

    Returns the old JSON message pair on a completed duplicate; SSE duplicates
    receive an explicit conflict (no historical delta replay in Phase C).
    """
    # Internal realtime admission joins binding/message persistence in one
    # transaction. Ordinary HTTP boundaries and replay responses stay intact.
    if atomic_admission and payload.input_mode != "realtime_voice":
        raise ValueError("Atomic admission is internal to realtime voice")
    from app.config import get_settings
    if atomic_admission or get_settings().realtime_enabled:
        db.execute(update(Conversation).where(Conversation.id == conversation.id)
                   .values(id=Conversation.id, updated_at=Conversation.updated_at))
    digest = digest_request(conversation, payload, timezone_name)
    scope = (ConversationTurn.actor_user_id == conversation.user_id,
             ConversationTurn.conversation_id == conversation.id,
             ConversationTurn.legacy_id == conversation.legacy_id,
             ConversationTurn.mode == conversation.mode,
             ConversationTurn.client_turn_id == payload.client_turn_id)

    def existing_result(turn):
        if turn.request_digest != digest:
            conflict("turn_key_conflict")
        if atomic_admission and turn.user_message_id is not None:
            return {"turn_id": turn.id, "user_message": db.get(Message, turn.user_message_id)}
        if turn.state == "completed":
            if streaming:
                conflict("turn_already_completed")
            obs.replayed(turn.id, conversation.mode, payload.input_mode)
            return {"user_message": db.get(Message, turn.user_message_id),
                    "rya_message": db.get(Message, turn.assistant_message_id)}
        conflict("turn_in_progress" if turn.state in {"pending", "streaming"} else "turn_terminal")

    if payload.client_turn_id is not None:
        existing = db.scalar(select(ConversationTurn).where(*scope))
        if existing is not None:
            return existing_result(existing)
    active = select(ConversationTurn.id).where(ConversationTurn.conversation_id == conversation.id,
                                               ConversationTurn.state.in_(["pending", "streaming"]))
    if not atomic_admission:
        active = active.where(ConversationTurn.input_mode == "realtime_voice")
    if db.scalar(active.limit(1)) is not None:
        conflict("turn_in_progress")
    turn = ConversationTurn(conversation_id=conversation.id, legacy_id=conversation.legacy_id,
                            actor_user_id=conversation.user_id, mode=conversation.mode,
                            client_turn_id=payload.client_turn_id, request_digest=digest,
                            input_mode=payload.input_mode, state="pending")
    db.add(turn)
    try:
        db.flush() if atomic_admission else control_commit(db)
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(ConversationTurn).where(*scope)) if payload.client_turn_id else None
        if existing is None:
            raise
        return existing_result(existing)
    obs.accepted(turn.id, conversation.mode, payload.input_mode)
    db.info["active_turn_id"] = turn.id
    token = str(uuid4())
    db.info["turn_claim_token"] = token
    claimed = claim_turn(db, turn.id, token, commit=False) if atomic_admission else claim_turn(db, turn.id, token)
    if not claimed:
        conflict("turn_in_progress")
    return None


def claim_turn(db, turn_id, token, *, commit=True):
    changed = db.execute(update(ConversationTurn).where(
        ConversationTurn.id == turn_id, ConversationTurn.state == "pending",
        ConversationTurn.claim_token.is_(None),
    ).values(state="streaming", claim_token=token, started_at=_now(), updated_at=_now())).rowcount
    if changed:
        from app.services.plan_usage import track_turn
        track_turn(db, db.get(ConversationTurn, turn_id))
    if commit:
        control_commit(db)
    return changed == 1


def link_user(db, message):
    db.flush()
    turn = db.get(ConversationTurn, db.info["active_turn_id"])
    if (turn.state != "streaming" or turn.claim_token != db.info["turn_claim_token"]
            or turn.user_message_id is not None
            or message.conversation_id != turn.conversation_id or message.role != MessageRole.USER):
        raise ValueError("Invalid turn user message")
    turn.user_message_id = message.id
    turn.legacy_id = message.conversation.legacy_id
    # Flushed/committed only with the route's existing message boundary.


def finish_turn(db, assistant=None, *, state="completed", error_code=None):
    if state not in {"completed", "interrupted", "failed"}:
        raise ValueError("Invalid terminal transition")
    turn_id = db.info["active_turn_id"]
    db.flush()
    turn = db.get(ConversationTurn, turn_id)
    if assistant is not None and (assistant.conversation_id != turn.conversation_id or assistant.role != MessageRole.ASSISTANT):
        raise ValueError("Invalid turn assistant message")
    if state == "completed" and assistant is None:
        raise ValueError("Completion requires an assistant")
    if error_code not in {None, "generation_failed", "persistence_failed", "processing_failed", "cancelled"}:
        raise ValueError("Unsafe turn error code")
    changed = db.execute(update(ConversationTurn).where(
        ConversationTurn.id == turn_id, ConversationTurn.state == "streaming",
        ConversationTurn.claim_token == db.info["turn_claim_token"],
    ).values(state=state, assistant_message_id=assistant.id if assistant else None,
             safe_error_code=error_code, finished_at=_now(), updated_at=_now())).rowcount
    if changed != 1:
        raise ValueError("Turn is already terminal or claim is not owned")
    from app.services.plan_usage import track_turn
    track_turn(db, db.get(ConversationTurn, turn_id))


def fail_turn(db, *, interrupted=False, error_code="generation_failed"):
    if error_code not in {"generation_failed", "persistence_failed", "processing_failed", "cancelled"}:
        raise ValueError("Unsafe turn error code")
    if "active_turn_id" not in db.info:
        return
    turn_id = db.info["active_turn_id"]
    db.rollback()
    # Never undo a durable assistant, nor change a terminal state after an effect failure.
    changed = db.execute(update(ConversationTurn).where(
        ConversationTurn.id == turn_id, ConversationTurn.state.in_(["pending", "streaming"]),
        ConversationTurn.claim_token == db.info["turn_claim_token"],
    ).values(state="interrupted" if interrupted else "failed", safe_error_code=error_code,
             finished_at=_now(), updated_at=_now())).rowcount
    if changed:
        from app.services.plan_usage import track_turn
        track_turn(db, db.get(ConversationTurn, turn_id))
        obs.failed("cancelled" if interrupted else "persistence_failed" if error_code == "persistence_failed" else "provider_failed")
        control_commit(db)
    else:
        db.rollback()


def lifecycle_guard(function):
    """Preserve endpoint signatures/response contracts; cover all exit paths."""
    @wraps(function)
    async def guarded(*args, **kwargs):
        db = kwargs["db"]
        try:
            response = await function(*args, **kwargs)
        except BaseException as exc:
            fail_turn(db, interrupted=isinstance(exc, asyncio.CancelledError),
                      error_code="cancelled" if isinstance(exc, asyncio.CancelledError) else
                      "persistence_failed" if isinstance(exc, SQLAlchemyError) else "processing_failed")
            raise
        if isinstance(response, StreamingResponse):
            original = response.body_iterator

            async def guarded_stream():
                try:
                    async for item in original:
                        yield item
                except (asyncio.CancelledError, GeneratorExit):
                    fail_turn(db, interrupted=True, error_code="cancelled")
                    raise
                except BaseException as exc:
                    fail_turn(db, error_code="persistence_failed" if isinstance(exc, SQLAlchemyError) else "processing_failed")
                    raise
                finally:
                    await original.aclose()
                    # Existing handled SSE errors return normally without an assistant.
                    fail_turn(db)
            response.body_iterator = guarded_stream()
        return response
    return obs.observe_turn(guarded)
