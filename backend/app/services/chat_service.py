"""Companion chat orchestration and approved-memory grounding."""

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import json
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.user import Conversation, Message
from app.crud.memory import LegacyCRUD
from app.services.ai.ai_service import AIService
from app.services.ai.context_builder import ContextBuilder, ConversationMessage
from app.services.ai.provider import AIMessage
from app.services.ai.exceptions import MemoryGroundingError
from app.services.conversation_continuity import ConversationContinuity
from app.services.memory.grounding import CompanionMemoryGrounding
from app.services.memory.fidelity import (
    MemoryFidelityAnalyzer,
    MemoryFidelityService,
)
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

    def prepare_ai_input(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
    ) -> list[AIMessage]:
        """Return provider messages while retaining the established contract."""
        return self._prepare_companion_input(
            db,
            conversation,
            user_message,
        ).messages

    def _prepare_companion_input(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
    ) -> PreparedCompanionInput:
        """Prepare messages and internal grounding provenance together."""
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
        grounding_context = None
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
                retrieval_query = self._conversation_continuity.build_retrieval_query(
                    history,
                    user_message,
                )
                query_classification = MemoryRelevanceRanker.classify_query(
                    retrieval_query
                )
                query_intent = query_classification.intent
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
                selection = self._memory_grounding.select(
                    ranked.memories
                )
                grounding_context = selection.context
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
                query_scope=("broad" if query_classification.broad else "specific"),
                top_relevance_scores=[],
                grounding_context_created=False,
                provider_call_attempted=False,
            )
        return PreparedCompanionInput(
            messages=self._context_builder.build_chat_messages(
                history,
                user_message,
                grounding_context=grounding_context,
                persona_display_name=persona_display_name,
                persona_relationship=persona_relationship,
                retrieval_available=retrieval_available,
                persona_style_profile=persona_profile.prompt_data(),
                persona_fidelity_guidance=fidelity_plan.prompt_guidance(),
            ),
            memory_ids=memory_ids,
            retrieved_at=retrieved_at,
            request_id=request_id,
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
    ) -> CompanionGeneration:
        """Generate text and return internal supplied-memory provenance."""
        prepared = self._prepare_companion_input(
            db,
            conversation,
            user_message,
        )
        db.rollback()
        self._log_provider_attempt(prepared, conversation)
        content = await self._ai_service.generate_response(prepared.messages)
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
    ) -> CompanionStreamPlan:
        """Prepare stream and provenance before provider iteration begins."""
        prepared = self._prepare_companion_input(
            db,
            conversation,
            user_message,
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
        )

    async def _stream_with_provider_log(
        self,
        prepared: PreparedCompanionInput,
        conversation: Conversation,
    ) -> AsyncIterator[str]:
        self._log_provider_attempt(prepared, conversation)
        async for chunk in self._ai_service.stream_response(prepared.messages):
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
