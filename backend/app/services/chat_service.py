"""Companion chat orchestration and approved-memory grounding."""

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.user import Conversation, Message
from app.services.ai.ai_service import AIService
from app.services.ai.context_builder import ContextBuilder, ConversationMessage
from app.services.ai.provider import AIMessage
from app.services.ai.exceptions import MemoryGroundingError
from app.services.memory.grounding import CompanionMemoryGrounding
from app.services.memory.retrieval import (
    MemoryRetrievalNotFoundError,
    MemoryRetrievalService,
)


@dataclass(frozen=True)
class PreparedCompanionInput:
    messages: list[AIMessage]
    memory_ids: tuple[int, ...] = ()
    retrieved_at: datetime | None = None


@dataclass(frozen=True)
class CompanionGeneration:
    content: str
    memory_ids: tuple[int, ...] = ()
    retrieved_at: datetime | None = None


@dataclass(frozen=True)
class CompanionStreamPlan:
    stream: AsyncIterator[str]
    memory_ids: tuple[int, ...] = ()
    retrieved_at: datetime | None = None


class ChatService:
    """Load conversation context and prepare provider-neutral AI input."""

    def __init__(
        self,
        ai_service: AIService,
        context_builder: ContextBuilder,
        memory_retrieval: MemoryRetrievalService | None = None,
        memory_grounding: CompanionMemoryGrounding | None = None,
    ) -> None:
        self._ai_service = ai_service
        self._context_builder = context_builder
        self._memory_retrieval = memory_retrieval or MemoryRetrievalService()
        self._memory_grounding = memory_grounding or CompanionMemoryGrounding()

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
        memory_ids: tuple[int, ...] = ()
        retrieved_at = None
        legacy_id = getattr(conversation, "legacy_id", None)
        if (
            legacy_id is not None
            and isinstance(user_message, str)
            and user_message.strip()
        ):
            try:
                ranked = self._memory_retrieval.search_approved(
                    db,
                    user_id=conversation.user_id,
                    legacy_id=legacy_id,
                    query=user_message,
                )
            except (MemoryRetrievalNotFoundError, SQLAlchemyError) as exc:
                db.rollback()
                raise MemoryGroundingError(
                    "Approved Legacy memories could not be prepared."
                ) from exc
            selection = self._memory_grounding.select(
                ranked.memories
            )
            grounding_context = selection.context
            if selection.memories:
                memory_ids = tuple(
                    memory.memory_id for memory in selection.memories
                )
                retrieved_at = datetime.now(timezone.utc)
        return PreparedCompanionInput(
            messages=self._context_builder.build_chat_messages(
                history,
                user_message,
                grounding_context=grounding_context,
            ),
            memory_ids=memory_ids,
            retrieved_at=retrieved_at,
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
        content = await self._ai_service.generate_response(prepared.messages)
        return CompanionGeneration(
            content=content,
            memory_ids=prepared.memory_ids,
            retrieved_at=prepared.retrieved_at,
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
            stream=self._ai_service.stream_response(prepared.messages),
            memory_ids=prepared.memory_ids,
            retrieved_at=prepared.retrieved_at,
        )

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
