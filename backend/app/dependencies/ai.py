"""Dependency factory for provider-independent chat services."""

from functools import lru_cache

from app.config import get_settings
from app.services.ai.ai_service import AIService
from app.services.ai.context_builder import ContextBuilder
from app.services.ai.provider_registry import create_ai_provider
from app.services.ai.retry import AIRetryPolicy
from app.services.chat_service import ChatService


@lru_cache()
def get_chat_service() -> ChatService:
    """Return the configured application-wide chat service."""
    settings = get_settings()
    provider = create_ai_provider(settings)
    retry_policy = AIRetryPolicy(
        max_retries=settings.ai_retry_max_retries,
        base_delay_seconds=settings.ai_retry_base_delay_seconds,
        max_delay_seconds=settings.ai_retry_max_delay_seconds,
        jitter_seconds=settings.ai_retry_jitter_seconds,
    )
    ai_service = AIService(provider, retry_policy=retry_policy)
    context_builder = ContextBuilder(
        max_context_messages=settings.ai_max_context_messages
    )
    return ChatService(ai_service, context_builder)
