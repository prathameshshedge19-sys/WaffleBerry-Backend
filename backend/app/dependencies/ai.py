"""Dependency factory for provider-independent chat services."""

from functools import lru_cache

from app.config import get_settings
from app.services.ai.ai_service import AIService
from app.services.ai.context_builder import ContextBuilder
from app.services.ai.provider_registry import create_ai_provider
from app.services.ai.retry import AIRetryPolicy
from app.services.chat_service import ChatService
from app.services.memory.extractor import MemoryExtractionService
from app.services.memory.storage_pipeline import MemoryStoragePipeline
from app.services.memory.validation import MemoryValidationService
from app.services.memory.retrieval import MemoryRetrievalService
from app.services.memory.grounding import (
    CompanionMemoryGrounding,
    MemoryGroundingBudget,
)


@lru_cache()
def get_ai_service() -> AIService:
    """Return one shared provider-backed AI service for all AI modes."""
    settings = get_settings()
    provider = create_ai_provider(settings)
    retry_policy = AIRetryPolicy(
        max_retries=settings.ai_retry_max_retries,
        base_delay_seconds=settings.ai_retry_base_delay_seconds,
        max_delay_seconds=settings.ai_retry_max_delay_seconds,
        jitter_seconds=settings.ai_retry_jitter_seconds,
    )
    return AIService(provider, retry_policy=retry_policy)


@lru_cache()
def get_chat_service() -> ChatService:
    """Return the configured application-wide chat service."""
    settings = get_settings()
    context_builder = ContextBuilder(
        max_context_messages=settings.ai_max_context_messages
    )
    return ChatService(
        get_ai_service(),
        context_builder,
        MemoryRetrievalService(),
        CompanionMemoryGrounding(
            MemoryGroundingBudget(
                max_memories=settings.memory_grounding_max_memories,
                max_estimated_tokens=(
                    settings.memory_grounding_max_estimated_tokens
                ),
                max_characters=(
                    settings.memory_grounding_max_characters
                ),
            )
        ),
    )


@lru_cache()
def get_memory_extraction_service() -> MemoryExtractionService:
    """Return extraction orchestration using the shared provider client."""
    return MemoryExtractionService(get_ai_service())


@lru_cache()
def get_memory_storage_pipeline() -> MemoryStoragePipeline:
    """Return the internal extraction/validation/persistence coordinator."""
    return MemoryStoragePipeline(
        get_memory_extraction_service(),
        MemoryValidationService(),
    )
