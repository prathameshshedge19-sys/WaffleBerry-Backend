"""Read-only orchestration for speech from persisted assistant messages."""

from sqlalchemy.orm import Session

from app.crud.user import ConversationCRUD, MessageCRUD, UserCRUD
from app.models.user import MessageRole, User
from app.services.ai.provider import SpeechResult
from app.services.ai.message_speech_engine import MessageSpeechEngine
from app.services.voice_profile_resolver import StandardVoiceResolver
from app.services.personal_voice_speech_service import PersonalVoiceSpeechService
from app.services.voice_catalogue import get_voice


class MessageSpeechError(Exception):
    """Safe application error for an ineligible stored message."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.status_code = status_code


class MessageSpeechService:
    """Authorize and synthesize immutable stored assistant content."""

    def __init__(
        self,
        speech_service: MessageSpeechEngine,
        voice_resolver: StandardVoiceResolver,
        *,
        max_text_characters: int,
        personal_voice_service: PersonalVoiceSpeechService | None = None,
    ) -> None:
        self._speech_service = speech_service
        self._voice_resolver = voice_resolver
        self._max_text_characters = max_text_characters
        self._personal_voice_service = personal_voice_service

    async def synthesize_assistant_message(
        self,
        *,
        db: Session,
        current_user: User,
        conversation_id: int,
        message_id: int,
        response_format: str | None,
    ) -> SpeechResult:
        """Use stored assistant text without changing persisted chat data."""
        conversation = ConversationCRUD.get_user_conversation(
            db,
            conversation_id,
            current_user.user_id,
        )
        if conversation is None:
            raise MessageSpeechError(
                "message_not_found",
                "Message not found.",
                404,
            )

        message = MessageCRUD.get_conversation_message(
            db,
            conversation_id,
            message_id,
        )
        if message is None:
            raise MessageSpeechError(
                "message_not_found",
                "Message not found.",
                404,
            )
        if message.role != MessageRole.ASSISTANT:
            raise MessageSpeechError(
                "assistant_message_required",
                "Only assistant messages can be converted to speech.",
                409,
            )

        stored_text = message.content
        if not isinstance(stored_text, str) or not stored_text.strip():
            raise MessageSpeechError(
                "speech_text_invalid",
                "The stored assistant message cannot be converted to speech.",
                409,
            )
        if len(stored_text) > self._max_text_characters:
            raise MessageSpeechError(
                "speech_text_too_long",
                "The stored assistant message is too long for speech generation.",
                409,
            )

        legacy = getattr(conversation, "legacy", None)
        voice_profile = self._voice_resolver.resolve(
            getattr(legacy, "relationship", None)
        )
        settings = UserCRUD.get_settings(db, current_user.user_id)
        selected_voice = get_voice(
            settings.preferred_voice if settings else None
        )

        # End the read-only transaction before awaiting the external provider.
        db.rollback()
        if selected_voice is not None:
            if self._personal_voice_service is None:
                raise RuntimeError("Personal voice routing is not configured.")
            return await self._personal_voice_service.synthesize(
                text=stored_text,
                voice=selected_voice,
                response_format=response_format,
            )
        return await self._speech_service.synthesize(
            text=stored_text,
            standard_voice_profile=voice_profile,
            response_format=response_format,
            preserve_text=True,
        )
