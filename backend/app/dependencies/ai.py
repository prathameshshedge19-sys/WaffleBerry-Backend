"""Dependency factory for provider-independent chat services."""

from functools import lru_cache

from app.services.ai.ai_service import AIService
from app.services.ai.openai_provider import OpenAIProvider
from app.services.chat_service import ChatService


@lru_cache()
def get_chat_service() -> ChatService:
    """Return the configured application-wide chat service."""
    provider = OpenAIProvider()
    ai_service = AIService(provider)
    return ChatService(ai_service)
