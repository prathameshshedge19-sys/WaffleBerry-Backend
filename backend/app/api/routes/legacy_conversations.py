from app.services.personality_style import select_personality_style
import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.conversation import Conversation, Message, MessageRole
from app.models.web_source import MessageWebSource
from app.models.user import User
from app.models.visitor import LegacyVisitorProfile
from app.schemas.chat import ConversationRename, ConversationResponse, LegacyConversationCreate, MessageCreate, MessagePairResponse, MessageResponse
from app.schemas.visitor import VisitorProfileResponse, VisitorProfileState, VisitorProfileUpdate
from app.services.authorization import require_persona_legacy
from app.services.legacy_intelligence import analyze_legacy_query
from app.services.legacy_persona import LegacyPersonaProvider, LegacyPersonaProviderError, get_legacy_persona_provider, nickname_cadence_guard, persona_system_context
from app.services.memory import LivingMemoryService, MemoryProvider, MemoryProviderError, get_memory_provider
from app.services.rya import ChatTurn
from app.services.web_search import WebSearchError, WebSearchProvider, WebSearchResult, get_web_search_provider, web_grounding
from app.services.visitor_identity import capture_from_message, current_language, greeting, upsert_profile, visitor_evidence


router = APIRouter(prefix="/legacy-conversations", tags=["Read-only Legacy persona chat"])
logger = logging.getLogger(__name__)


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _title(content: str) -> str:
    text = re.sub(r"\s+", " ", content).strip()
    text = re.split(r"[.!?\n]", text, maxsplit=1)[0].strip(" \"'`-:;,.")
    words = text.split()
    title = " ".join(words[:7]) or "New chat"
    return (title[:60].rsplit(" ", 1)[0] if len(title) > 60 else title)[:255]


def _visitor_conversation(db: Session, conversation_id: int, user_id: int, legacy_id: int | None = None) -> Conversation:
    conversation = db.scalar(select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
        Conversation.mode == "legacy",
    ))
    if conversation is None:
        raise HTTPException(status_code=404, detail={"code": "conversation_not_found", "message": "Legacy conversation not found."})
    if legacy_id is not None and conversation.legacy_id != legacy_id:
        raise HTTPException(status_code=409, detail={"code": "legacy_mismatch", "message": "Conversation does not belong to this Legacy."})
    require_persona_legacy(db, user_id, conversation.legacy_id)
    return conversation


def _profile(db: Session, legacy_id: int, user_id: int) -> LegacyVisitorProfile | None:
    return db.scalar(select(LegacyVisitorProfile).where(LegacyVisitorProfile.legacy_id == legacy_id, LegacyVisitorProfile.viewer_user_id == user_id))


async def _turns(db: Session, conversation: Conversation, content: str, memory_provider: MemoryProvider) -> tuple[list[ChatTurn], object, object]:
    legacy = require_persona_legacy(db, conversation.user_id, conversation.legacy_id)
    memory_service = LivingMemoryService(memory_provider)
    active_memories = memory_service.active_memories(db, legacy.id)
    route = analyze_legacy_query(content, legacy.subject_name, active_memories)
    memories = ()
    if route.needs_memory:
        try:
            memories = await memory_service.retrieve_read_only(db, legacy.id, content, route)
        except MemoryProviderError as exc:
            logger.warning("Read-only persona retrieval skipped kind=%s legacy_id=%s", exc.kind, legacy.id)
    recent = db.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id.desc()).limit(get_settings().ai_max_context_messages)).all()
    visitor = visitor_evidence(active_memories, _profile(db, legacy.id, conversation.user_id))
    visitor["current_turn_language"] = current_language(content)
    turns = [ChatTurn(role="system", content=persona_system_context(legacy, memories, route, active_memories, visitor, personality_style=select_personality_style(db, legacy, content, memories, visitor, recent, history_order="newest_first")))]
    turns.extend(ChatTurn(role=message.role.value, content=message.content) for message in reversed(recent))
    guard = nickname_cadence_guard(visitor, turns)
    if guard:
        turns.append(ChatTurn(role="system", content=guard))
    return turns, legacy, route


async def _current_context(provider: WebSearchProvider, content: str, route) -> WebSearchResult | None:
    if not route.needs_fresh_data:
        logger.info("route=%s web_search_used=false", route.intent.value)
        return None
    try:
        result = await provider.search(content)
        logger.info("route=fresh web_search_used=true")
        return result
    except WebSearchError as exc:
        logger.warning("route=fresh web_search_used=false failure=%s", exc.kind)
        return None


def _add_current_context(turns: list[ChatTurn], result: WebSearchResult | None, needs_fresh_data: bool) -> None:
    if result:
        turns.insert(1, ChatTurn(role="system", content=web_grounding(result)))
    elif needs_fresh_data:
        turns.insert(1, ChatTurn(role="system", content="CURRENT INFORMATION UNAVAILABLE: Give an in-role limitation for the current part. Never fabricate it. Stable general context is allowed when useful."))


def _persist_sources(db: Session, message: Message, result: WebSearchResult | None) -> None:
    if not result:
        return
    for source in result.sources:
        db.add(MessageWebSource(message=message, **source.as_dict()))


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_legacy_conversation(payload: LegacyConversationCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_persona_legacy(db, user.id, payload.legacy_id)
    conversation = Conversation(user_id=user.id, legacy_id=payload.legacy_id, title=payload.title.strip(), mode="legacy")
    db.add(conversation); db.commit(); db.refresh(conversation)
    return conversation


@router.get("/visitor-profile", response_model=VisitorProfileState)
def get_visitor_profile(legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    legacy = require_persona_legacy(db, user.id, legacy_id)
    profile = _profile(db, legacy_id, user.id)
    if profile:
        profile.last_seen_at = datetime.now(timezone.utc); db.commit(); db.refresh(profile)
    return {"profile": profile, "greeting": greeting(legacy.subject_name or "this Legacy", profile)}


@router.put("/visitor-profile", response_model=VisitorProfileResponse)
def update_visitor_profile(payload: VisitorProfileUpdate, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_persona_legacy(db, user.id, legacy_id)
    profile = upsert_profile(db, legacy_id, user.id, payload.preferred_name, payload.claimed_relationship)
    db.commit(); db.refresh(profile); return profile


@router.delete("/visitor-profile", status_code=status.HTTP_204_NO_CONTENT)
def reset_visitor_profile(legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_persona_legacy(db, user.id, legacy_id)
    profile = _profile(db, legacy_id, user.id)
    if profile:
        db.delete(profile); db.commit()
    return Response(status_code=204)


@router.get("", response_model=list[ConversationResponse])
def list_legacy_conversations(legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_persona_legacy(db, user.id, legacy_id)
    return db.scalars(select(Conversation).where(
        Conversation.user_id == user.id,
        Conversation.legacy_id == legacy_id,
        Conversation.mode == "legacy",
    ).order_by(Conversation.updated_at.desc(), Conversation.id.desc())).all()


@router.patch("/{conversation_id}", response_model=ConversationResponse)
def rename_legacy_conversation(payload: ConversationRename, conversation_id: int, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conversation = _visitor_conversation(db, conversation_id, user.id, legacy_id)
    conversation.title = payload.title; db.commit(); db.refresh(conversation); return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_legacy_conversation(conversation_id: int, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conversation = _visitor_conversation(db, conversation_id, user.id, legacy_id)
    db.delete(conversation); db.commit(); return Response(status_code=204)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
def list_legacy_messages(conversation_id: int, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conversation = _visitor_conversation(db, conversation_id, user.id, legacy_id)
    return db.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id)).all()


@router.post("/{conversation_id}/messages", response_model=MessagePairResponse, status_code=status.HTTP_201_CREATED)
async def send_legacy_message(payload: MessageCreate, conversation_id: int, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db), provider: LegacyPersonaProvider = Depends(get_legacy_persona_provider), memory_provider: MemoryProvider = Depends(get_memory_provider), web_provider: WebSearchProvider = Depends(get_web_search_provider)):
    conversation = _visitor_conversation(db, conversation_id, user.id, legacy_id)
    content = payload.content.strip()
    if not content: raise HTTPException(status_code=422, detail="Message content must not be blank.")
    user_message = Message(conversation_id=conversation.id, role=MessageRole.USER, content=content)
    db.add(user_message); capture_from_message(db, _profile(db, legacy_id, user.id), legacy_id, user.id, content); db.flush()
    turns, _legacy, route = await _turns(db, conversation, content, memory_provider)
    current = await _current_context(web_provider, content, route)
    _add_current_context(turns, current, route.needs_fresh_data)
    try:
        answer = await provider.respond(turns)
    except LegacyPersonaProviderError as exc:
        db.rollback(); raise HTTPException(status_code=503, detail={"code": exc.kind, "message": "This Legacy is temporarily unavailable."}) from None
    persona_message = Message(conversation_id=conversation.id, role=MessageRole.ASSISTANT, content=answer)
    db.add(persona_message); db.flush(); _persist_sources(db, persona_message, current); conversation.updated_at = datetime.now(timezone.utc)
    if conversation.title in {"New chat", "New conversation"}: conversation.title = _title(content)
    db.commit(); db.refresh(user_message); db.refresh(persona_message)
    return {"user_message": user_message, "rya_message": persona_message}


@router.post("/{conversation_id}/messages/stream")
async def stream_legacy_message(payload: MessageCreate, conversation_id: int, legacy_id: int = Query(..., ge=1), user: User = Depends(get_current_user), db: Session = Depends(get_db), provider: LegacyPersonaProvider = Depends(get_legacy_persona_provider), memory_provider: MemoryProvider = Depends(get_memory_provider), web_provider: WebSearchProvider = Depends(get_web_search_provider)):
    conversation = _visitor_conversation(db, conversation_id, user.id, legacy_id)
    content = payload.content.strip()
    if not content: raise HTTPException(status_code=422, detail="Message content must not be blank.")
    user_message = Message(conversation_id=conversation.id, role=MessageRole.USER, content=content)
    db.add(user_message); capture_from_message(db, _profile(db, legacy_id, user.id), legacy_id, user.id, content); conversation.updated_at = datetime.now(timezone.utc)
    if conversation.title in {"New chat", "New conversation"}: conversation.title = _title(content)
    db.commit(); db.refresh(user_message)
    turns, legacy, route = await _turns(db, conversation, content, memory_provider)

    async def event_stream():
        chunks: list[str] = []
        yield _sse("start", {"conversation_id": conversation.id, "user_message_id": user_message.id, "legacy_id": legacy.id, "mode": "legacy", "route": route.intent.value, "input_mode": payload.input_mode})
        if route.needs_fresh_data:
            yield _sse("activity", {"message": "Checking the latest information…"})
        current = await _current_context(web_provider, content, route)
        _add_current_context(turns, current, route.needs_fresh_data)
        if current and current.sources:
            yield _sse("sources", {"current_information": True, "sources": [source.as_dict() for source in current.sources]})
        try:
            async for delta in provider.stream(turns): chunks.append(delta); yield _sse("delta", {"delta": delta})
        except asyncio.CancelledError: raise
        except LegacyPersonaProviderError as exc:
            yield _sse("error", {"code": exc.kind, "message": "This Legacy couldn't finish that response. Try again."}); return
        answer = "".join(chunks).strip()
        if not answer:
            yield _sse("error", {"code": "legacy_persona_empty_response", "message": "This Legacy couldn't finish that response. Try again."}); return
        try:
            persona_message = Message(conversation_id=conversation.id, role=MessageRole.ASSISTANT, content=answer)
            db.add(persona_message); db.flush(); _persist_sources(db, persona_message, current); conversation.updated_at = datetime.now(timezone.utc); db.commit(); db.refresh(persona_message)
        except SQLAlchemyError:
            db.rollback(); yield _sse("error", {"code": "message_persistence_failed", "message": "The response could not be saved."}); return
        yield _sse("done", {"message_id": persona_message.id, "conversation_id": conversation.id, "memories_saved": 0, "mode": "legacy", "input_mode": payload.input_mode, "current_information": bool(current), "sources": [source.as_dict() for source in current.sources] if current else []})

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"})
