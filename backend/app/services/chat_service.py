"""Chat orchestration prepared for future AI response generation."""

from collections.abc import AsyncIterator, Iterable

from sqlalchemy.orm import Session

from app.models.user import Conversation, Message
from app.services.ai.ai_service import AIService
from app.services.ai.context_builder import ContextBuilder, ConversationMessage
from app.services.ai.provider import AIMessage


class ChatService:
    """Load conversation context and prepare provider-neutral AI input."""

    def __init__(
        self,
        ai_service: AIService,
        context_builder: ContextBuilder,
    ) -> None:
        self._ai_service = ai_service
        self._context_builder = context_builder

    def prepare_ai_input(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
    ) -> list[AIMessage]:
        """Fetch ordered history without calling a provider or persisting data."""
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
        return self._context_builder.build_chat_messages(
            history,
            user_message,
        )

    async def generate_response(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
    ) -> str:
        """Generate assistant text without changing persistence state."""
        messages = self.prepare_ai_input(db, conversation, user_message)
        return await self._ai_service.generate_response(messages)

    def stream_response(
        self,
        db: Session,
        conversation: Conversation,
        user_message: str,
    ) -> AsyncIterator[str]:
        """Prepare ordered context and return a provider-neutral text stream."""
        messages = self.prepare_ai_input(db, conversation, user_message)
        return self._ai_service.stream_response(messages)

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
