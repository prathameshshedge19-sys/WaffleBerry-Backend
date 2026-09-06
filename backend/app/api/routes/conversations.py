import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.services import turn_observability as obs, usage_accounting as usage
from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.models.progress import DailyPrompt, PromptStatus
from app.models.user import User
from app.schemas.chat import ConversationCreate, ConversationRename, ConversationResponse, DailyPromptStart, DailyPromptStartResponse, MessageCreate, MessagePairResponse, MessageResponse
from app.services.rya import RyaProvider, RyaProviderError, get_rya_provider
from app.services.authorization import accessible_legacy, legacy_role, require_legacy
from app.services.legacy_setup import active_or_new_legacy, apply_setup_message
from app.services.memory import MemoryProvider, get_memory_provider
from app.services.conversation_turns import TurnActorContext, TurnCompletionContext, prepare_turn, complete_turn


from app.services.turn_lifecycle import accept_turn, link_user, finish_turn, fail_turn, lifecycle_guard


router = APIRouter(prefix="/conversations", tags=["Rya chat"])
logger = logging.getLogger(__name__)


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def derive_conversation_title(content: str) -> str:
    """Create a stable, concise title from the first user message without an AI call."""
    text = re.sub(r"\s+", " ", content).strip()
    text = re.split(r"[.!?\n]", text, maxsplit=1)[0].strip(" \"'`-:;,.")
    lowered = text.casefold()
    if "changing my job" in lowered or "change my job" in lowered:
        return "Changing careers"

    prefixes = (
        "could you please ", "would you please ", "can you please ",
        "could you ", "would you ", "can you ", "please ",
        "i'm confused about ", "i am confused about ",
        "i want to talk about ", "i'd like to talk about ",
        "tell me about ", "help me understand ", "help me with ",
    )
    for prefix in prefixes:
        if lowered.startswith(prefix):
            text = text[len(prefix):].strip()
            break

    words = text.split()
    title = " ".join(words[:7]).strip(" \"'`-:;,.") or "New chat"
    if len(title) > 60:
        title = title[:60].rsplit(" ", 1)[0].rstrip(" \"'`-:;,.")
    return title[:1].upper() + title[1:] if title else "New chat"


def _owned_conversation(
    db: Session,
    conversation_id: int,
    user_id: int,
    legacy_id: int | None = None,
) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.mode == "rya",
        )
    )
    if not conversation:
        raise HTTPException(
            status_code=404,
            detail={"code": "conversation_not_found", "message": "Conversation not found."},
        )
    if legacy_id is not None and conversation.legacy_id != legacy_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "legacy_mismatch",
                "message": "Conversation does not belong to the selected Legacy.",
            },
        )
    return conversation


def _legacy_for_conversation(db: Session, conversation: Conversation, user: User) -> Legacy:
    legacy = accessible_legacy(db, user.id, conversation.legacy_id)
    if legacy is None and conversation.legacy_id is None:
        legacy = active_or_new_legacy(db, user)
        conversation.legacy_id = legacy.id
    if legacy is None:
        raise HTTPException(status_code=404, detail="Legacy not found.")
    return legacy


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(payload: ConversationCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.legacy_id is not None:
        legacy = require_legacy(db, user.id, payload.legacy_id)
    else:
        legacy = accessible_legacy(db, user.id, user.active_legacy_id) or active_or_new_legacy(db, user)
    conversation = Conversation(user_id=user.id, legacy_id=legacy.id, title=payload.title.strip(), mode="rya")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("", response_model=list[ConversationResponse])
def list_conversations(
    legacy_id: int = Query(..., ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_legacy(db, user.id, legacy_id)
    return db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.legacy_id == legacy_id, Conversation.mode == "rya")
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    ).all()


def _daily_prompt_start_response(db: Session, conversation: Conversation) -> dict:
    rya_message = db.scalar(
        select(Message).where(
            Message.conversation_id == conversation.id,
            Message.role == MessageRole.ASSISTANT,
        ).order_by(Message.id)
    )
    if rya_message is None:
        raise HTTPException(status_code=409, detail="The daily question conversation is incomplete.")
    return {"conversation": conversation, "rya_message": rya_message}


@router.post("/from-daily-prompt", response_model=DailyPromptStartResponse, status_code=status.HTTP_201_CREATED)
def start_from_daily_prompt(
    payload: DailyPromptStart,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    legacy = require_legacy(db, user.id, payload.legacy_id)
    prompt = db.scalar(select(DailyPrompt).where(
        DailyPrompt.id == payload.prompt_id,
        DailyPrompt.legacy_id == legacy.id,
    ))
    if prompt is None:
        raise HTTPException(status_code=409, detail="This daily question is no longer available.")

    existing = db.scalar(select(Conversation).where(
        Conversation.user_id == user.id,
        Conversation.source_daily_prompt_id == prompt.id,
        Conversation.mode == "rya",
    ))
    if existing is not None:
        return _daily_prompt_start_response(db, existing)
    if prompt.status != PromptStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="This daily question is no longer available.")

    conversation = Conversation(
        user_id=user.id,
        legacy_id=legacy.id,
        title=derive_conversation_title(prompt.prompt_text),
        mode="rya",
        source_daily_prompt_id=prompt.id,
    )
    db.add(conversation)
    db.flush()
    rya_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=prompt.prompt_text,
    )
    db.add(rya_message)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Conversation).where(
            Conversation.user_id == user.id,
            Conversation.source_daily_prompt_id == payload.prompt_id,
            Conversation.mode == "rya",
        ))
        if existing is None:
            raise
        return _daily_prompt_start_response(db, existing)
    db.refresh(conversation)
    db.refresh(rya_message)
    return {"conversation": conversation, "rya_message": rya_message}


@router.patch("/{conversation_id}", response_model=ConversationResponse)
def rename_conversation(payload: ConversationRename, conversation_id: int, legacy_id: int | None = Query(default=None, ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conversation = _owned_conversation(db, conversation_id, user.id, legacy_id)
    conversation.title = payload.title
    db.commit()
    db.refresh(conversation)
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, legacy_id: int | None = Query(default=None, ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conversation = _owned_conversation(db, conversation_id, user.id, legacy_id)
    db.delete(conversation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
def list_messages(conversation_id: int, legacy_id: int | None = Query(default=None, ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _owned_conversation(db, conversation_id, user.id, legacy_id)
    return db.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)).all()


@router.post("/{conversation_id}/messages", response_model=MessagePairResponse, status_code=status.HTTP_201_CREATED)
@lifecycle_guard
async def send_message(payload: MessageCreate, conversation_id: int, legacy_id: int | None = Query(default=None, ge=1), timezone_name: str = Query(default="UTC", alias="timezone", max_length=64), user: User = Depends(get_current_user), db: Session = Depends(get_db), provider: RyaProvider = Depends(get_rya_provider), memory_provider: MemoryProvider = Depends(get_memory_provider)):
    conversation = _owned_conversation(db, conversation_id, user.id, legacy_id)
    legacy = _legacy_for_conversation(db, conversation, user) if conversation.legacy_id is not None else None
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Message content must not be blank.")
    replay = accept_turn(db, conversation, payload, streaming=False, timezone_name=timezone_name)
    if replay is not None:
        return replay
    if legacy is None:
        legacy = _legacy_for_conversation(db, conversation, user)
    user_message = Message(conversation_id=conversation.id, role=MessageRole.USER, content=content)
    db.add(user_message)
    link_user(db, user_message)
    db.flush()
    setup_update = apply_setup_message(legacy, content, user.full_name)
    actor = TurnActorContext.from_authorized(conversation, legacy, user_message, actor_id=user.id,
        role=legacy_role(db, user.id, legacy) or "owner", input_mode=payload.input_mode, timezone_name=timezone_name)
    prepared = await prepare_turn(db, actor, conversation, legacy, user_message, memory_provider, activated_now=setup_update.activated)
    turns = prepared.turns
    try:
        answer = await usage.respond(provider, turns)
        with obs.stage("assistant_persistence"):
            rya_message = Message(conversation_id=conversation.id, role=MessageRole.ASSISTANT, content=answer)
            db.add(rya_message)
            finish_turn(db, rya_message)
            conversation.updated_at = datetime.now(timezone.utc)
            if conversation.title in {"New conversation", "New chat"}:
                conversation.title = derive_conversation_title(content)
            db.commit()
            db.refresh(user_message)
            db.refresh(rya_message)
        obs.durable()
    except RyaProviderError as exc:
        db.rollback()
        obs.degraded("provider_failed")
        raise HTTPException(
            status_code=503,
            detail={"code": exc.kind, "message": "Rya is temporarily unavailable."},
        ) from None
    except RuntimeError:
        db.rollback()
        obs.degraded("provider_failed")
        raise HTTPException(
            status_code=503,
            detail={"code": "rya_provider_error", "message": "Rya is temporarily unavailable."},
        ) from None
    await complete_turn(db, prepared, TurnCompletionContext(conversation, legacy, user_message, rya_message))
    return {"user_message": user_message, "rya_message": rya_message}


@router.post("/{conversation_id}/messages/stream")
@lifecycle_guard
async def stream_message(payload: MessageCreate, conversation_id: int, legacy_id: int | None = Query(default=None, ge=1), timezone_name: str = Query(default="UTC", alias="timezone", max_length=64), user: User = Depends(get_current_user), db: Session = Depends(get_db), provider: RyaProvider = Depends(get_rya_provider), memory_provider: MemoryProvider = Depends(get_memory_provider)):
    conversation = _owned_conversation(db, conversation_id, user.id, legacy_id)
    legacy = _legacy_for_conversation(db, conversation, user) if conversation.legacy_id is not None else None
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Message content must not be blank.")

    replay = accept_turn(db, conversation, payload, streaming=True, timezone_name=timezone_name)
    if replay is not None:
        return replay
    if legacy is None:
        legacy = _legacy_for_conversation(db, conversation, user)
    user_message = Message(conversation_id=conversation.id, role=MessageRole.USER, content=content)
    db.add(user_message)
    link_user(db, user_message)
    setup_update = apply_setup_message(legacy, content, user.full_name)
    conversation.updated_at = datetime.now(timezone.utc)
    if conversation.title in {"New conversation", "New chat"}:
        conversation.title = derive_conversation_title(content)
    db.commit()
    db.refresh(user_message)

    actor = TurnActorContext.from_authorized(conversation, legacy, user_message, actor_id=user.id,
        role=legacy_role(db, user.id, legacy) or "owner", input_mode=payload.input_mode, timezone_name=timezone_name)
    prepared = await prepare_turn(db, actor, conversation, legacy, user_message, memory_provider, activated_now=setup_update.activated)
    turns = prepared.turns

    async def event_stream():
        chunks: list[str] = []
        yield _sse("start", {"conversation_id": conversation.id, "user_message_id": user_message.id, "legacy_id": legacy.id, "input_mode": payload.input_mode})
        try:
            async for delta in usage.stream(provider, turns):
                chunks.append(delta)
                yield _sse("delta", {"delta": delta})
        except asyncio.CancelledError:
            obs.failed("cancelled")
            raise
        except RyaProviderError as exc:
            obs.degraded("provider_failed")
            yield _sse("error", {"code": exc.kind, "message": "Rya couldn't finish that response. Try again."})
            return
        except RuntimeError:
            obs.degraded("provider_failed")
            yield _sse("error", {"code": "rya_provider_error", "message": "Rya couldn't finish that response. Try again."})
            return

        answer = "".join(chunks).strip()
        if not answer:
            yield _sse("error", {"code": "rya_provider_empty_response", "message": "Rya couldn't finish that response. Try again."})
            return

        try:
            with obs.stage("assistant_persistence"):
                rya_message = Message(conversation_id=conversation.id, role=MessageRole.ASSISTANT, content=answer)
                db.add(rya_message)
                finish_turn(db, rya_message)
                conversation.updated_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(rya_message)
            obs.durable()
        except SQLAlchemyError:
            db.rollback()
            obs.degraded("persistence_failed")
            fail_turn(db, error_code="persistence_failed")
            yield _sse("error", {"code": "message_persistence_failed", "message": "Rya responded, but the conversation could not be saved."})
            return

        completion = await complete_turn(db, prepared, TurnCompletionContext(conversation, legacy, user_message, rya_message), include_progress=True)
        yield _sse("done", {
            "message_id": rya_message.id,
            "conversation_id": conversation.id,
            "input_mode": payload.input_mode,
            "memories_saved": completion.memories_saved,
            "progress": completion.progress,
            "streak": completion.streak,
            "today_just_completed": completion.today_just_completed,
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
