"""Shared Rya builder preparation and post-success policy for text and voice."""

from dataclasses import dataclass, field
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services import turn_observability as obs, usage_accounting as usage
from app.config import get_settings
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.services.conversation_turns import (
    PreparedTurn, TurnActorContext, TurnCompletionContext, TurnCompletionResult,
)
from app.services.legacy_intelligence import analyze_legacy_query
from app.services.legacy_setup import setup_system_context
from app.services.memory import (
    LivingMemoryService, MemoryAnalysis, MemoryProvider, MemoryProviderError,
    memory_grounding, progressive_interviewing,
)
from app.services.progression import legacy_progress, local_date, record_builder_activity, streak_summary
from app.services.rya import ChatTurn

# Preserve existing log categories as well as error/fallback behavior.
logger = logging.getLogger("app.api.routes.conversations")


@dataclass(frozen=True)
class BuilderPreparedTurn(PreparedTurn):
    memory_service: LivingMemoryService = field(repr=False)
    memory_analysis: MemoryAnalysis | None = field(repr=False)


def _provider_turns(
    db: Session,
    conversation: Conversation,
    legacy: Legacy,
    activated_now: bool,
    relevant_memories=(),
    memory_analysis: MemoryAnalysis | None = None,
    active_memories=(),
    contributor_role: str = "owner",
) -> list[ChatTurn]:
    limit = get_settings().ai_max_context_messages
    with obs.stage("history_loading"):
        recent = db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.id.desc())
            .limit(limit)
        ).all()
        recent_questions = db.scalars(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.legacy_id == legacy.id,
                Conversation.user_id == conversation.user_id,
                Message.role == MessageRole.ASSISTANT,
                Message.content.contains("?"),
            )
            .order_by(Message.id.desc())
            .limit(12)
        ).all()
    system_turns = [ChatTurn(role="system", content=setup_system_context(legacy, activated_now))]
    if contributor_role == "collaborator":
        system_turns.append(ChatTurn(role="system", content=f"COLLABORATOR BUILDER CONTEXT\nThis user is a trusted contributor to {legacy.subject_name or 'the selected subject'}'s Legacy, not necessarily the Legacy subject or owner. Treat Conversation.legacy_id as authoritative for whose Legacy is being built. Interpret relationship phrases such as 'my aunt' in that target context, preserve the contributor's language and perspective, and never imply they are the subject. You may say 'you mentioned' or ask what they remember. Do not repeatedly announce their collaborator role."))
        system_turns.append(ChatTurn(role="system", content="MEMORY PERMISSIONS\nOnly the Legacy owner may delete canonical memories. You cannot carry out a contributor's forget/delete request or claim it was done. The contributor may still add, enrich, and correct facts under the existing contribution rules."))
    else:
        system_turns.append(ChatTurn(role="system", content=f"OWNER BUILDER CONTEXT\nThis user owns {legacy.subject_name or 'the selected subject'}'s Legacy. Preserve Rya's builder identity and use the active evidence graph to synthesize what has already been shared."))
    grounding = memory_grounding(relevant_memories)
    if grounding:
        system_turns.append(ChatTurn(role="system", content=grounding))
    interview_context = [*reversed(recent), *recent_questions]
    interviewing = progressive_interviewing(memory_analysis, active_memories, interview_context, subject_name=legacy.subject_name, contributor_role=contributor_role)
    if interviewing:
        system_turns.append(ChatTurn(role="system", content=interviewing))
    return system_turns + [
        ChatTurn(role=message.role.value, content=message.content) for message in reversed(recent)
    ]


async def _prepare_memory(
    db: Session,
    legacy: Legacy,
    content: str,
    provider: MemoryProvider,
):
    service = LivingMemoryService(provider)
    analysis: MemoryAnalysis | None = None
    try:
        with obs.stage("memory_analysis"):
            analysis = await service.analyze(db, legacy, content)
    except MemoryProviderError as exc:
        obs.degraded("provider_failed")
    query = analysis.normalized_query if analysis else content
    active = service.active_memories(db, legacy.id)
    with obs.stage("classification"):
        route = analyze_legacy_query(query, legacy.subject_name, active)
    obs.metadata(route=route.intent.value.lower())
    relevant = ()
    if route.needs_memory or bool(analysis and analysis.memories):
        try:
            with obs.stage("memory_retrieval"):
                relevant = await service.retrieve(db, legacy.id, query)
        except MemoryProviderError as exc:
            obs.degraded("memory_retrieval_failed")
    return service, analysis, relevant, active


@obs.timed("memory_effect")
async def _store_memory(
    service: LivingMemoryService,
    db: Session,
    legacy: Legacy,
    conversation: Conversation,
    source_message: Message,
    content: str,
    analysis: MemoryAnalysis | None,
    changed_by_user_id: int,
) -> list:
    try:
        return await service.store(db, legacy, conversation, source_message, content, analysis, changed_by_user_id)
    except MemoryProviderError as exc:
        obs.degraded("post_processing_failed")
        return []


async def prepare_builder_turn(db: Session, actor: TurnActorContext, conversation: Conversation,
                               legacy: Legacy, user_message: Message, memory_provider: MemoryProvider, *, activated_now: bool) -> BuilderPreparedTurn:
    actor.validate_scope(conversation, legacy, user_message)
    if actor.mode != "rya" or not actor.capabilities.apply_builder_memory:
        raise ValueError("Builder preparation requires a builder turn")
    service, analysis, relevant, active = await _prepare_memory(db, legacy, actor.content, memory_provider)
    turns = _provider_turns(db, conversation, legacy, activated_now, relevant, analysis, active, actor.role)
    return BuilderPreparedTurn(actor, turns, service, analysis)


async def complete_builder_turn(db: Session, prepared: PreparedTurn, completion: TurnCompletionContext,
                                *, include_progress: bool = False, authorization_guard=None) -> TurnCompletionResult:
    if not isinstance(prepared, BuilderPreparedTurn) or not prepared.actor.capabilities.apply_builder_memory:
        raise ValueError("Builder finalization requires a builder preparation")
    actor = prepared.actor
    completion.validate(actor)
    from app.models.turn import ConversationTurn
    from app.services.turn_effects import apply_effects
    durable = db.scalar(select(ConversationTurn).where(ConversationTurn.user_message_id == completion.user_message.id))
    if durable is not None:
        return await apply_effects(db, durable.id, prepared, completion, include_progress=include_progress,
                                   authorization_guard=authorization_guard)
    if authorization_guard is not None:
        raise ValueError("Guarded finalization requires a durable turn")
    changed = await _store_memory(prepared.memory_service, db, completion.legacy, completion.conversation,
                                  completion.user_message, actor.content, prepared.memory_analysis, actor.actor_id)
    # JSON only resolved a local date when memories changed; SSE always did so.
    today = local_date(actor.timezone_name) if include_progress or changed else None
    activity = None
    if changed:
        activity = record_builder_activity(db, user_id=actor.actor_id, legacy_id=actor.legacy_id,
                                           activity_type=changed[-1].operation_type, memory_id=changed[-1].id,
                                           activity_date=today)
    return TurnCompletionResult(
        memories_saved=len(changed),
        progress=legacy_progress(db, actor.legacy_id) if include_progress else None,
        streak=streak_summary(db, actor.legacy_id, today) if include_progress else None,
        today_just_completed=bool(activity and activity.was_first_today),
    )
