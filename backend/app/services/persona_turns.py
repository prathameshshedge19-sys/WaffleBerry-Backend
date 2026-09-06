"""Read-only Legacy persona reasoning; identity writes and sources stay in routes."""

from dataclasses import dataclass
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services import turn_observability as obs, usage_accounting as usage
from app.config import get_settings
from app.models.conversation import Conversation, Message
from app.models.legacy import Legacy
from app.models.visitor import LegacyVisitorProfile
from app.services.conversation_turns import PreparedTurn, TurnActorContext
from app.services.legacy_intelligence import QueryRoute, analyze_legacy_query
from app.services.legacy_persona import nickname_cadence_guard, persona_system_context
from app.services.memory import LivingMemoryService, MemoryProvider, MemoryProviderError
from app.services.personality_style import select_personality_style
from app.services.rya import ChatTurn
from app.services.visitor_identity import current_language, visitor_evidence
from app.services.web_search import WebSearchError, WebSearchProvider, WebSearchResult, web_grounding

logger = logging.getLogger("app.api.routes.legacy_conversations")


@dataclass(frozen=True)
class PersonaPreparedTurn(PreparedTurn):
    route: QueryRoute


async def prepare_persona_turn(db: Session, actor: TurnActorContext, conversation: Conversation,
                               legacy: Legacy, user_message: Message, memory_provider: MemoryProvider) -> PersonaPreparedTurn:
    actor.validate_scope(conversation, legacy, user_message)
    if actor.mode != "legacy" or not actor.capabilities.read_only_retrieval:
        raise ValueError("Persona preparation requires a viewer turn")
    content = actor.content
    memory_service = LivingMemoryService(memory_provider)
    active_memories = memory_service.active_memories(db, legacy.id)
    with obs.stage("classification"):
        route = analyze_legacy_query(content, legacy.subject_name, active_memories)
    obs.metadata(route=route.intent.value.lower())
    memories = ()
    if route.needs_memory:
        try:
            with obs.stage("memory_retrieval"):
                memories = await memory_service.retrieve_read_only(db, legacy.id, content, route)
        except MemoryProviderError as exc:
            obs.degraded("memory_retrieval_failed")
    with obs.stage("history_loading"):
        recent = db.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id.desc()).limit(get_settings().ai_max_context_messages)).all()
    with obs.stage("relationship_context"):
        visitor = visitor_evidence(active_memories, db.scalar(select(LegacyVisitorProfile).where(LegacyVisitorProfile.legacy_id == legacy.id, LegacyVisitorProfile.viewer_user_id == conversation.user_id)))
    visitor["current_turn_language"] = current_language(content)
    with obs.stage("personality_selection"):
        style = select_personality_style(db, legacy, content, memories, visitor, recent, history_order="newest_first")
    obs.metadata(personality_status="available" if style is not None else "fallback")
    if style is None: obs.degraded("personality_unavailable")
    turns = [ChatTurn(role="system", content=persona_system_context(legacy, memories, route, active_memories, visitor, personality_style=style))]
    turns.extend(ChatTurn(role=message.role.value, content=message.content) for message in reversed(recent))
    guard = nickname_cadence_guard(visitor, turns)
    if guard:
        turns.append(ChatTurn(role="system", content=guard))
    return PersonaPreparedTurn(actor, turns, route)


async def _current_context(provider: WebSearchProvider, content: str, route) -> WebSearchResult | None:
    if not route.needs_fresh_data:
        return None
    try:
        with obs.stage("current_information"):
            result = await usage.invoke("web", provider.search, content)
        return result
    except WebSearchError as exc:
        obs.degraded("current_info_unavailable")
        return None


def _add_current_context(turns: list[ChatTurn], result: WebSearchResult | None, needs_fresh_data: bool) -> None:
    if result:
        turns.insert(1, ChatTurn(role="system", content=web_grounding(result)))
    elif needs_fresh_data:
        turns.insert(1, ChatTurn(role="system", content="CURRENT INFORMATION UNAVAILABLE: Give an in-role limitation for the current part. Never fabricate it. Stable general context is allowed when useful."))


async def add_current_information(prepared: PersonaPreparedTurn, provider: WebSearchProvider) -> WebSearchResult | None:
    if not isinstance(prepared, PersonaPreparedTurn) or not prepared.actor.capabilities.current_information:
        raise ValueError("Current information requires a persona turn")
    current = await _current_context(provider, prepared.actor.content, prepared.route)
    _add_current_context(prepared.turns, current, prepared.route.needs_fresh_data)
    return current
