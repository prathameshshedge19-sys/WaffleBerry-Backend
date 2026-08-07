"""Companion chat orchestration and approved-memory grounding."""

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import json
import re
from uuid import uuid4
from types import SimpleNamespace

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.user import Conversation, Message
from app.crud.memory import LegacyCRUD
from app.services.ai.ai_service import AIService
from app.services.ai.context_builder import ContextBuilder, ConversationMessage
from app.services.ai.provider import AIMessage
from app.services.ai.exceptions import MemoryGroundingError
from app.services.ai.exceptions import AIProviderError
from app.services.ai.external_knowledge import (
    attach_external_context,
    attach_web_failure_context,
    ExternalKnowledgeClassifier,
    QueryKnowledgeMode,
)
from app.services.ai.provider import ExternalKnowledgeMode
from app.services.conversation_continuity import ConversationContinuity
from app.services.memory.grounding import CompanionMemoryGrounding
from app.services.memory.identity_retrieval import (
    detect_identity_intent,
    IdentityFactRetrievalService,
    IdentityGroundingResult,
)
from app.services.memory.fidelity import (
    MemoryFidelityAnalyzer,
    MemoryFidelityService,
)
from app.services.memory.multilingual_retrieval import detect_query_language_mode
from app.services.memory.name_resolution import NameResolution, ProperNameResolver
from app.services.memory.retrieval import (
    MemoryRetrievalArchivedError,
    MemoryRetrievalNotFoundError,
    MemoryRetrievalService,
)
from app.services.memory.retrieval_ranking import MemoryRelevanceRanker
from app.services.persona_profile import PersonaProfile, PersonaProfileService


logger = logging.getLogger(__name__)


def _safe_log(level: int, event: str, **metadata) -> None:
    """Emit journal-visible structured metadata with no story or prompt text."""
    logger.log(
        level,
        json.dumps(
            {"event": event, **metadata},
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


@dataclass(frozen=True)
class PreparedCompanionInput:
    messages: list[AIMessage]
    memory_ids: tuple[int, ...] = ()
    retrieved_at: datetime | None = None
    request_id: str = ""
    query_mode: QueryKnowledgeMode = "autobiographical_memory"
    external_knowledge_mode: ExternalKnowledgeMode | None = None
    external_lookup_messages: tuple[AIMessage, ...] = ()
    grounding_chars: int = 0
    identity_context_chars: int = 0
    identity_direct: bool = False


@dataclass(frozen=True)
class CompanionGeneration:
    content: str
    memory_ids: tuple[int, ...] = ()
    retrieved_at: datetime | None = None
    request_id: str = ""


@dataclass(frozen=True)
class CompanionStreamPlan:
    stream: AsyncIterator[str]
    memory_ids: tuple[int, ...] = ()
    retrieved_at: datetime | None = None
    request_id: str = ""


class ChatService:
    """Load conversation context and prepare provider-neutral AI input."""

    def __init__(
        self,
        ai_service: AIService,
        context_builder: ContextBuilder,
        memory_retrieval: MemoryRetrievalService | None = None,
        memory_grounding: CompanionMemoryGrounding | None = None,
        persona_profiles: PersonaProfileService | None = None,
        memory_fidelity: MemoryFidelityService | None = None,
        conversation_continuity: ConversationContinuity | None = None,
        identity_retrieval: IdentityFactRetrievalService | None = None,
        name_resolver: ProperNameResolver | None = None,
    ) -> None:
        self._ai_service = ai_service
        self._context_builder = context_builder
        self._memory_retrieval = memory_retrieval or MemoryRetrievalService()
        self._memory_grounding = memory_grounding or CompanionMemoryGrounding()
        self._persona_profiles = persona_profiles or PersonaProfileService()
        self._memory_fidelity = memory_fidelity or MemoryFidelityService()
        self._conversation_continuity = (
            conversation_continuity or ConversationContinuity()
        )
        self._identity_retrieval = (
            identity_retrieval or IdentityFactRetrievalService()
        )
        self._name_resolver = name_resolver or ProperNameResolver()

    def prepare_ai_input(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
        *, conversation_style: str = "natural", response_length: str = "balanced",
    ) -> list[AIMessage]:
        """Return provider messages while retaining the established contract."""
        return self._prepare_companion_input(
            db,
            conversation,
            user_message,
            conversation_style=conversation_style,
            response_length=response_length,
        ).messages

    def _prepare_companion_input(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
        *,
        history_override: Iterable[ConversationMessage] | None = None,
        live_call: bool = False,
        conversation_style: str = "natural",
        response_length: str = "balanced",
    ) -> PreparedCompanionInput:
        """Prepare messages and internal grounding provenance together."""
        if history_override is None:
            history = (
                db.query(Message)
                .filter(Message.conversation_id == conversation.conversation_id)
                .order_by(
                    Message.created_at.desc(),
                    Message.message_id.desc(),
                )
                .limit(self._context_builder.history_query_limit)
                .all()
            )
            history.reverse()
        else:
            history = list(history_override)[-self._context_builder.history_query_limit:]
        grounding_context = None
        memory_grounding_context = None
        identity_context = None
        request_id = uuid4().hex
        memory_ids: tuple[int, ...] = ()
        retrieved_at = None
        persona_display_name = None
        persona_relationship = None
        retrieval_available = True
        persona_profile = PersonaProfile()
        fidelity_plan = MemoryFidelityAnalyzer().analyze([])
        query_classification = MemoryRelevanceRanker.classify_query(user_message)
        query_intent = query_classification.intent
        query_language_mode = detect_query_language_mode(user_message)
        knowledge_plan = ExternalKnowledgeClassifier.classify(user_message)
        name_resolution = NameResolution()
        legacy_id = getattr(conversation, "legacy_id", None)
        if (
            legacy_id is not None
            and isinstance(user_message, str)
            and user_message.strip()
        ):
            try:
                legacy = getattr(conversation, "legacy", None)
                if (
                    legacy is None
                    or legacy.owner_user_id != conversation.user_id
                ):
                    legacy = LegacyCRUD.get_user_legacy(
                        db,
                        legacy_id,
                        conversation.user_id,
                    )
            except SQLAlchemyError as exc:
                db.rollback()
                raise MemoryGroundingError(
                    "Legacy identity could not be prepared."
                ) from exc
            if legacy is None:
                raise MemoryGroundingError(
                    "Legacy identity could not be prepared."
                )
            persona_display_name = legacy.display_name
            persona_relationship = legacy.relationship
            try:
                persona_profile = self._persona_profiles.build(
                    db,
                    legacy_id=legacy_id,
                )
            except SQLAlchemyError:
                db.rollback()
                persona_profile = PersonaProfile()
            try:
                name_resolution = self._name_resolver.resolve(
                    db,
                    user_id=conversation.user_id,
                    legacy_id=legacy_id,
                    query=user_message,
                )
            except SQLAlchemyError:
                db.rollback()
                name_resolution = NameResolution()
            try:
                identity_arguments = {
                    "user_id": conversation.user_id,
                    "legacy_id": legacy_id,
                    "query": user_message,
                }
                if name_resolution.fact_type is not None:
                    identity_arguments["fact_type_override"] = (
                        name_resolution.fact_type
                    )
                    identity_arguments["canonical_value_override"] = (
                        name_resolution.canonical_value
                    )
                identity_result = self._identity_retrieval.retrieve(
                    db,
                    **identity_arguments,
                )
            except SQLAlchemyError:
                db.rollback()
                identity_result = IdentityGroundingResult(
                    detect_identity_intent(user_message),
                    None,
                )
            try:
                retrieval_query = self._conversation_continuity.build_retrieval_query(
                    history,
                    user_message,
                )
                retrieval_query = name_resolution.expand_query(retrieval_query)
                query_classification = MemoryRelevanceRanker.classify_query(
                    retrieval_query
                )
                query_intent = query_classification.intent
                query_language_mode = detect_query_language_mode(retrieval_query)
                ranked = self._memory_retrieval.search_approved(
                    db,
                    user_id=conversation.user_id,
                    legacy_id=legacy_id,
                    query=retrieval_query,
                )
            except SQLAlchemyError:
                db.rollback()
                retrieval_available = False
                _safe_log(
                    logging.WARNING,
                    "companion_memory_retrieval_failed",
                    request_id=request_id,
                    user_id=conversation.user_id,
                    conversation_id=conversation.conversation_id,
                    legacy_id=legacy_id,
                    retrieval_failure_category="database_error",
                    query_intent=query_intent,
                    query_language_mode=query_language_mode,
                    retrieval_route="semantic_unavailable_lexical_fallback",
                    query_scope=("broad" if query_classification.broad else "specific"),
                    approved_memory_count=None,
                    matched_memory_count=0,
                    selected_memory_ids=[],
                    top_relevance_scores=[],
                )
            except (
                MemoryRetrievalNotFoundError,
                MemoryRetrievalArchivedError,
            ) as exc:
                db.rollback()
                _safe_log(
                    logging.WARNING,
                    "companion_memory_retrieval_failed",
                    request_id=request_id,
                    user_id=conversation.user_id,
                    conversation_id=conversation.conversation_id,
                    legacy_id=legacy_id,
                    retrieval_failure_category=type(exc).__name__,
                    query_intent=query_intent,
                    query_language_mode=query_language_mode,
                    retrieval_route="semantic_unavailable_lexical_fallback",
                    query_scope=("broad" if query_classification.broad else "specific"),
                    approved_memory_count=None,
                    matched_memory_count=0,
                    selected_memory_ids=[],
                    top_relevance_scores=[],
                )
                raise MemoryGroundingError(
                    "Approved Legacy memories could not be prepared."
                ) from exc
            else:
                selection = (
                    self._memory_grounding.select(ranked.memories, compact=True)
                    if live_call else self._memory_grounding.select(ranked.memories)
                )
                grounding_context = selection.context
                memory_grounding_context = selection.context
                identity_context = (
                    identity_result.compact_context
                    if live_call and identity_result.compact_context is not None
                    else identity_result.context
                )
                if identity_context is not None:
                    grounding_context = (
                        identity_context
                        if grounding_context is None
                        else f"{identity_context}\n\n{grounding_context}"
                    )
                if selection.memories:
                    memory_ids = tuple(
                        memory.memory_id for memory in selection.memories
                    )
                    retrieved_at = datetime.now(timezone.utc)
                _safe_log(
                    logging.INFO,
                    "companion_memory_retrieval",
                    request_id=request_id,
                    user_id=conversation.user_id,
                    conversation_id=conversation.conversation_id,
                    legacy_id=legacy_id,
                    approved_candidate_count=ranked.approved_memory_count,
                    approved_memory_count=ranked.approved_memory_count,
                    retrieved_memory_count=ranked.matched_memory_count,
                    matched_memory_count=ranked.matched_memory_count,
                    selected_memory_ids=list(memory_ids),
                    query_intent=query_intent,
                    query_language_mode=query_language_mode,
                    retrieval_route=(
                        "multilingual_semantic_lexical_hybrid"
                        if ranked.semantic_route_used
                        else "semantic_unavailable_lexical_fallback"
                    ),
                    semantic_candidate_count=ranked.semantic_candidate_count,
                    semantic_top_score=max(
                        (
                            memory.semantic_score
                            for memory in selection.memories
                            if memory.semantic_score is not None
                        ),
                        default=None,
                    ),
                    embedding_versions=list(dict.fromkeys(
                        memory.embedding_version
                        for memory in selection.memories
                        if memory.embedding_version
                    )),
                    query_scope=("broad" if query_classification.broad else "specific"),
                    positive_scoring_memory_count=ranked.matched_memory_count,
                    selected_topic_buckets=list(dict.fromkeys(
                        bucket for memory in selection.memories
                        for bucket in memory.topic_buckets
                    )),
                    selected_relevance_scores=[
                        memory.relevance_score for memory in selection.memories
                    ],
                    top_relevance_scores=[
                        memory.relevance_score
                        for memory in ranked.memories[:8]
                    ],
                    grounding_context_created=grounding_context is not None,
                    provider_call_attempted=False,
                )
                _safe_log(
                    logging.INFO,
                    "companion_identity_retrieval",
                    request_id=request_id,
                    user_id=conversation.user_id,
                    conversation_id=conversation.conversation_id,
                    legacy_id=legacy_id,
                    identity_intent_detected=(
                        identity_result.fact_type is not None
                    ),
                    identity_fact_type=(
                        identity_result.fact_type.value
                        if identity_result.fact_type is not None
                        else None
                    ),
                    identity_candidate_count=identity_result.candidate_count,
                    identity_conflict_present=identity_result.conflict_present,
                    identity_fallback_to_memory=(
                        identity_result.fact_type is not None
                        and identity_result.context is None
                    ),
                )
                _safe_log(
                    logging.INFO,
                    "companion_name_resolution",
                    request_id=request_id,
                    user_id=conversation.user_id,
                    conversation_id=conversation.conversation_id,
                    legacy_id=legacy_id,
                    name_resolution_attempted=True,
                    candidate_count=name_resolution.candidate_count,
                    resolution_method=(
                        "deterministic_transliteration_context"
                        if name_resolution.canonical_value is not None
                        else None
                    ),
                    resolution_confidence_bucket=(
                        "high" if name_resolution.confidence >= 0.90 else "none"
                    ),
                    relationship_context_used=(
                        name_resolution.relationship_context_used
                    ),
                    ambiguous_resolution=name_resolution.ambiguous,
                    fallback_used=(name_resolution.canonical_value is None),
                )
                try:
                    fidelity_plan = self._memory_fidelity.analyze_selected(
                        db,
                        legacy_id=legacy_id,
                        memories=list(selection.memories),
                        retrieval_available=True,
                    )
                except SQLAlchemyError:
                    db.rollback()
                    fidelity_plan = MemoryFidelityAnalyzer().analyze(
                        list(selection.memories),
                        has_uncertainty=True,
                    )
            if not retrieval_available:
                fidelity_plan = MemoryFidelityAnalyzer().analyze(
                    [],
                    retrieval_available=False,
                )
        else:
            _safe_log(
                logging.INFO,
                "companion_memory_retrieval",
                request_id=request_id,
                user_id=conversation.user_id,
                conversation_id=conversation.conversation_id,
                legacy_id=legacy_id,
                approved_candidate_count=0,
                approved_memory_count=0,
                retrieved_memory_count=0,
                matched_memory_count=0,
                selected_memory_ids=[],
                query_intent=query_intent,
                query_language_mode=query_language_mode,
                retrieval_route="not_applicable",
                query_scope=("broad" if query_classification.broad else "specific"),
                top_relevance_scores=[],
                grounding_context_created=False,
                provider_call_attempted=False,
            )
        return PreparedCompanionInput(
            messages=self._apply_presentation_preferences(self._context_builder.build_chat_messages(
                history,
                user_message,
                grounding_context=grounding_context,
                persona_display_name=persona_display_name,
                persona_relationship=persona_relationship,
                retrieval_available=retrieval_available,
                persona_style_profile=persona_profile.prompt_data(),
                persona_fidelity_guidance=fidelity_plan.prompt_guidance(),
                external_knowledge_enabled=(
                    knowledge_plan.query_mode != "autobiographical_memory"
                ),
                live_call=live_call,
            ), conversation_style, response_length, live_call),
            memory_ids=memory_ids,
            retrieved_at=retrieved_at,
            request_id=request_id,
            query_mode=knowledge_plan.query_mode,
            external_knowledge_mode=knowledge_plan.external_knowledge_mode,
            external_lookup_messages=(
                ExternalKnowledgeClassifier.build_public_lookup_messages(
                    user_message
                )
                if knowledge_plan.web_search_requested
                else ()
            ),
            grounding_chars=len(memory_grounding_context or ""),
            identity_context_chars=len(identity_context or "") if legacy_id is not None else 0,
            identity_direct=(
                legacy_id is not None
                and identity_result.compact_context is not None
            ),
        )

    async def generate_response(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
    ) -> str:
        """Generate assistant text without changing persistence state."""
        result = await self.generate_response_with_provenance(
            db,
            conversation,
            user_message,
        )
        return result.content

    async def generate_response_with_provenance(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
        *, conversation_style: str = "natural", response_length: str = "balanced",
    ) -> CompanionGeneration:
        """Generate text and return internal supplied-memory provenance."""
        prepared = self._prepare_companion_input(
            db,
            conversation,
            user_message,
            conversation_style=conversation_style,
            response_length=response_length,
        )
        db.rollback()
        self._log_provider_attempt(prepared, conversation)
        try:
            synthesis_messages = prepared.messages
            if prepared.external_knowledge_mode == "web_search":
                external_facts = await self._ai_service.generate_response(
                    prepared.external_lookup_messages,
                    external_knowledge_mode="web_search",
                )
                synthesis_messages = attach_external_context(
                    prepared.messages,
                    external_facts,
                )
                _safe_log(
                    logging.INFO,
                    "companion_external_knowledge_completed",
                    request_id=prepared.request_id,
                    query_mode=prepared.query_mode,
                    web_search_requested=True,
                    web_search_completed=True,
                    memory_count_supplied=len(prepared.memory_ids),
                    external_source_count=self._source_link_count(
                        external_facts
                    ),
                    fallback_reason=None,
                    provider_tool_exception_type=None,
                )
            content = await self._ai_service.generate_response(
                synthesis_messages
            )
        except AIProviderError as exc:
            if prepared.external_knowledge_mode != "web_search":
                raise
            _safe_log(
                logging.WARNING,
                "companion_external_knowledge_fallback",
                request_id=prepared.request_id,
                query_mode=prepared.query_mode,
                web_search_requested=True,
                web_search_completed=False,
                memory_count_supplied=len(prepared.memory_ids),
                external_source_count=0,
                fallback_reason="provider_tool_failure",
                provider_tool_exception_type=type(exc).__name__,
            )
            content = await self._ai_service.generate_response(
                attach_web_failure_context(prepared.messages)
            )
        return CompanionGeneration(
            content=content,
            memory_ids=prepared.memory_ids,
            retrieved_at=prepared.retrieved_at,
            request_id=prepared.request_id,
        )

    def stream_response(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
    ) -> AsyncIterator[str]:
        """Prepare ordered context and return a provider-neutral text stream."""
        return self.stream_response_with_provenance(
            db,
            conversation,
            user_message,
        ).stream

    def stream_response_with_provenance(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
        *, conversation_style: str = "natural", response_length: str = "balanced",
    ) -> CompanionStreamPlan:
        """Prepare stream and provenance before provider iteration begins."""
        prepared = self._prepare_companion_input(
            db,
            conversation,
            user_message,
            conversation_style=conversation_style,
            response_length=response_length,
        )
        db.rollback()
        return CompanionStreamPlan(
            stream=self._stream_with_provider_log(prepared, conversation),
            memory_ids=prepared.memory_ids,
            retrieved_at=prepared.retrieved_at,
            request_id=prepared.request_id,
        )

    def _log_provider_attempt(
        self,
        prepared: PreparedCompanionInput,
        conversation: Conversation,
    ) -> None:
        _safe_log(
            logging.INFO,
            "companion_provider_call",
            request_id=prepared.request_id,
            user_id=conversation.user_id,
            conversation_id=conversation.conversation_id,
            legacy_id=conversation.legacy_id,
            selected_memory_ids=list(prepared.memory_ids),
            grounding_context_created=bool(prepared.memory_ids),
            provider_call_attempted=True,
            query_mode=prepared.query_mode,
            web_search_requested=(
                prepared.external_knowledge_mode == "web_search"
            ),
            memory_count_supplied=len(prepared.memory_ids),
            web_search_completed=False,
            external_source_count=0,
            fallback_reason=None,
            provider_tool_exception_type=None,
        )

    @staticmethod
    def _apply_presentation_preferences(
        messages: list[AIMessage], conversation_style: str,
        response_length: str, live_call: bool,
    ) -> list[AIMessage]:
        if live_call:
            return messages
        style = {
            "natural": "Use the established natural conversational tone.",
            "gentle": "Use calm, soft wording.",
            "expressive": "Use slightly more energetic wording.",
        }[conversation_style]
        length = {
            "short": "Keep the answer concise when supported facts permit it.",
            "balanced": "Use the normal conversational level of detail.",
            "detailed": "Give a more complete answer using only supported information.",
        }[response_length]
        messages.insert(1, AIMessage(
            role="system",
            content=("Presentation preferences only; never alter retrieval, identity, grounding, "
                     f"contradiction handling, uncertainty, or factual content. {style} {length}"),
        ))
        return messages

    def prepare_live_call_input(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        legacy_name: str,
        relationship: str,
        user_message: str,
        history: Iterable[ConversationMessage],
    ) -> PreparedCompanionInput:
        """Build read-only grounded context for an authorized ephemeral call."""
        transient_conversation = SimpleNamespace(
            conversation_id=None,
            user_id=user_id,
            legacy_id=legacy_id,
            legacy=SimpleNamespace(
                owner_user_id=user_id,
                display_name=legacy_name,
                relationship=relationship,
            ),
        )
        return self._prepare_companion_input(
            db,
            transient_conversation,
            user_message,
            history_override=history,
            live_call=True,
        )

    @staticmethod
    def _source_link_count(content: str) -> int:
        return len(set(re.findall(r"https?://[^\s)\]]+", content)))

    async def _stream_with_provider_log(
        self,
        prepared: PreparedCompanionInput,
        conversation: Conversation,
    ) -> AsyncIterator[str]:
        self._log_provider_attempt(prepared, conversation)
        received = False
        try:
            synthesis_messages = prepared.messages
            external_facts = None
            if prepared.external_knowledge_mode == "web_search":
                external_facts = await self._ai_service.generate_response(
                    prepared.external_lookup_messages,
                    external_knowledge_mode="web_search",
                )
                synthesis_messages = attach_external_context(
                    prepared.messages,
                    external_facts,
                )
            async for chunk in self._ai_service.stream_response(
                synthesis_messages
            ):
                received = True
                yield chunk
            if prepared.external_knowledge_mode == "web_search":
                _safe_log(
                    logging.INFO,
                    "companion_external_knowledge_completed",
                    request_id=prepared.request_id,
                    query_mode=prepared.query_mode,
                    web_search_requested=True,
                    web_search_completed=True,
                    memory_count_supplied=len(prepared.memory_ids),
                    external_source_count=self._source_link_count(
                        external_facts or ""
                    ),
                    fallback_reason=None,
                    provider_tool_exception_type=None,
                )
        except AIProviderError as exc:
            if received or prepared.external_knowledge_mode != "web_search":
                raise
            _safe_log(
                logging.WARNING,
                "companion_external_knowledge_fallback",
                request_id=prepared.request_id,
                query_mode=prepared.query_mode,
                web_search_requested=True,
                web_search_completed=False,
                memory_count_supplied=len(prepared.memory_ids),
                external_source_count=0,
                fallback_reason="provider_tool_failure",
                provider_tool_exception_type=type(exc).__name__,
            )
            async for chunk in self._ai_service.stream_response(
                attach_web_failure_context(prepared.messages)
            ):
                yield chunk

    def stream_story_response(
        self,
        history: Iterable[ConversationMessage],
        *,
        chapter: str,
        relationship: str,
        display_name: str,
    ) -> AsyncIterator[str]:
        """Stream Story Guide text through the shared AI service."""
        messages = self._context_builder.build_story_messages(
            history,
            chapter=chapter,
            relationship=relationship,
            display_name=display_name,
        )
        return self._ai_service.stream_response(messages)
