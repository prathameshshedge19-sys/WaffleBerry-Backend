"""Dependency factory for provider-independent chat services."""

from functools import lru_cache

from app.config import get_settings
from app.services.ai.ai_service import AIService
from app.services.ai.provider import AIProvider
from app.services.ai.context_builder import ContextBuilder
from app.services.ai.provider_registry import create_ai_provider
from app.services.ai.retry import AIRetryPolicy
from app.services.chat_service import ChatService
from app.services.memory.extractor import MemoryExtractionService
from app.services.memory.storage_pipeline import MemoryStoragePipeline
from app.services.memory.validation import MemoryValidationService
from app.services.memory.retrieval import MemoryRetrievalService
from app.services.memory.embedding import MemoryEmbeddingService
from app.services.memory.embedding_registry import create_embedding_provider
from app.services.memory.grounding import (
    CompanionMemoryGrounding,
    MemoryGroundingBudget,
)
from app.services.ai.transcription_service import TranscriptionService
from app.services.ai.speech_service import SpeechService
from app.services.ai.sarvam_speech_provider import SarvamBulbulProvider
from app.services.ai.sarvam_speech_service import SarvamSpeechService
from app.services.pronunciation_dictionary_service import (
    PronunciationDictionaryResolver,
)
from app.services.message_speech_service import MessageSpeechService
from app.services.voice_profile_resolver import StandardVoiceResolver
from app.services.personal_voice_speech_service import PersonalVoiceSpeechService


@lru_cache()
def get_ai_provider() -> AIProvider:
    """Return the shared configured provider adapter."""
    return create_ai_provider(get_settings())


@lru_cache()
def get_ai_service() -> AIService:
    """Return one shared provider-backed AI service for all AI modes."""
    settings = get_settings()
    provider = get_ai_provider()
    retry_policy = AIRetryPolicy(
        max_retries=settings.ai_retry_max_retries,
        base_delay_seconds=settings.ai_retry_base_delay_seconds,
        max_delay_seconds=settings.ai_retry_max_delay_seconds,
        jitter_seconds=settings.ai_retry_jitter_seconds,
    )
    return AIService(provider, retry_policy=retry_policy)


@lru_cache()
def get_memory_embedding_service() -> MemoryEmbeddingService | None:
    """Return configured multilingual retrieval or a lexical-only fallback."""
    settings = get_settings()
    if not settings.memory_semantic_retrieval_enabled:
        return None
    return MemoryEmbeddingService(
        create_embedding_provider(settings),
        threshold=settings.memory_semantic_threshold,
    )


@lru_cache()
def get_transcription_service() -> TranscriptionService:
    """Return transient audio transcription orchestration."""
    settings = get_settings()
    return TranscriptionService(
        get_ai_provider(),
        model=settings.audio_transcription_model,
    )


@lru_cache()
def get_speech_service() -> SpeechService:
    """Return transient speech-synthesis orchestration."""
    settings = get_settings()
    return SpeechService(
        get_ai_provider(),
        model=settings.openai_tts_model,
        default_voice=settings.openai_tts_voice,
        standard_male_voice=settings.openai_tts_male_voice,
        standard_female_voice=settings.openai_tts_female_voice,
        default_format=settings.openai_tts_format,
        max_text_characters=settings.tts_max_text_characters,
        timeout_seconds=settings.tts_timeout_seconds,
    )


@lru_cache()
def get_sarvam_message_speech_service() -> SarvamSpeechService:
    settings = get_settings()
    return SarvamSpeechService(
            SarvamBulbulProvider(
                api_key=settings.sarvam_api_key,
                model=settings.sarvam_model,
                male_speaker=settings.sarvam_speaker_male,
                female_speaker=settings.sarvam_speaker_female,
                output_format=settings.sarvam_output_format,
                timeout_seconds=settings.sarvam_timeout_seconds,
                max_audio_bytes=settings.sarvam_max_audio_bytes,
                pace=settings.sarvam_pace,
                temperature=settings.sarvam_temperature,
            ),
            max_text_characters=settings.sarvam_max_text_characters,
            dictionary_resolver=PronunciationDictionaryResolver(
                settings.sarvam_pronunciation_dictionary_id,
                required=settings.sarvam_pronunciation_dictionary_required,
            ),
            emotion_enabled=settings.speech_emotion_enabled,
            nonverbal_cues_enabled=settings.speech_nonverbal_cues_enabled,
            discourse_markers_enabled=settings.speech_discourse_markers_enabled,
        )


@lru_cache()
def get_message_speech_service() -> MessageSpeechService:
    """Return read-only stored-message speech orchestration."""
    settings = get_settings()
    sarvam_service = get_sarvam_message_speech_service()
    return MessageSpeechService(
        sarvam_service,
        StandardVoiceResolver(settings.default_standard_voice_profile),
        max_text_characters=settings.sarvam_max_text_characters,
        personal_voice_service=PersonalVoiceSpeechService(
            sarvam_service=sarvam_service,
            openai_service=get_speech_service(),
        ),
    )


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
        MemoryRetrievalService(get_memory_embedding_service()),
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
