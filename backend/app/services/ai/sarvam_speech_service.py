"""Normalized language-aware orchestration for Sarvam message speech."""

import logging
import time

from app.services.ai.provider import SpeechResult
from app.services.ai.exceptions import AIConfigurationError
from app.services.ai.sarvam_speech_provider import SarvamBulbulProvider
from app.services.speech_language_analyzer import (
    SpeechLanguageAnalyzer,
    SpeechLanguageMode,
)
from app.services.speech_text_normalizer import SpeechTextNormalizer
from app.services.voice_profile_resolver import StandardVoiceProfile
from app.services.pronunciation_dictionary_service import (
    PronunciationDictionaryResolver,
)
from app.services.speech_emotion_analyzer import (
    EmotionConfidence, SpeechEmotion, SpeechEmotionAnalysis, SpeechEmotionAnalyzer,
)
from app.services.speech_prosody_planner import SpeechProsodyPlanner


logger = logging.getLogger(__name__)

_LANGUAGE_CODES = {
    SpeechLanguageMode.ENGLISH: "en-IN",
    SpeechLanguageMode.HINDI_DEVANAGARI: "hi-IN",
    SpeechLanguageMode.MARATHI_DEVANAGARI: "mr-IN",
    SpeechLanguageMode.ROMANIZED_MARATHI: "mr-IN",
    SpeechLanguageMode.MIXED_MARATHI_ENGLISH: "mr-IN",
    SpeechLanguageMode.MIXED_HINDI_ENGLISH: "hi-IN",
    SpeechLanguageMode.DEVANAGARI_UNKNOWN: "hi-IN",
    SpeechLanguageMode.MULTILINGUAL_UNKNOWN: "en-IN",
}


def sarvam_language_code(mode: SpeechLanguageMode) -> str:
    """Map every analyzer outcome to a supported Sarvam language code."""
    return _LANGUAGE_CODES[mode]


class SarvamSpeechService:
    def __init__(
        self,
        provider: SarvamBulbulProvider,
        *,
        max_text_characters: int,
        normalizer: SpeechTextNormalizer | None = None,
        language_analyzer: SpeechLanguageAnalyzer | None = None,
        dictionary_resolver: PronunciationDictionaryResolver | None = None,
        emotion_analyzer: SpeechEmotionAnalyzer | None = None,
        prosody_planner: SpeechProsodyPlanner | None = None,
        emotion_enabled: bool = True,
        nonverbal_cues_enabled: bool = False,
        discourse_markers_enabled: bool = False,
    ) -> None:
        self._provider = provider
        self._max_text_characters = max_text_characters
        self._normalizer = normalizer or SpeechTextNormalizer()
        self._language_analyzer = language_analyzer or SpeechLanguageAnalyzer()
        self._dictionary_resolver = dictionary_resolver or (
            PronunciationDictionaryResolver(None, required=False)
        )
        self._emotion_analyzer = emotion_analyzer or SpeechEmotionAnalyzer()
        self._prosody_planner = prosody_planner or SpeechProsodyPlanner()
        self._emotion_enabled = emotion_enabled
        if nonverbal_cues_enabled:
            raise AIConfigurationError(
                "Nonverbal speech cues are not supported by the configured provider."
            )
        if discourse_markers_enabled:
            raise AIConfigurationError(
                "Speech discourse markers have not passed listening validation."
            )

    async def synthesize(
        self,
        *,
        text: str,
        standard_voice_profile: StandardVoiceProfile,
        response_format: str | None,
        selected_voice: str | None = None,
        preserve_text: bool = True,
        conversational_tone: str | None = None,
    ) -> SpeechResult:
        del response_format, preserve_text
        normalized = self._normalizer.normalize(text)
        if len(normalized) > self._max_text_characters:
            raise ValueError("Speech text exceeds the provider limit.")
        language_mode = self._language_analyzer.detect(normalized)
        language_code = sarvam_language_code(language_mode)
        tone_value = getattr(conversational_tone, "value", conversational_tone)
        tone_emotion = {
            "neutral": SpeechEmotion.NEUTRAL,
            "warm": SpeechEmotion.WARM, "happy": SpeechEmotion.JOYFUL,
            "excited": SpeechEmotion.EXCITED, "gentle": SpeechEmotion.PEACEFUL,
            "comforting": SpeechEmotion.REASSURING,
            "nostalgic": SpeechEmotion.NOSTALGIC, "serious": SpeechEmotion.SERIOUS,
        }.get(tone_value)
        analysis = (
            SpeechEmotionAnalysis(tone_emotion, EmotionConfidence.MEDIUM)
            if tone_emotion is not None else self._emotion_analyzer.analyze(
                normalized, language_mode=language_mode
            )
        )
        plan = self._prosody_planner.plan(
            canonical_text=normalized,
            language_mode=language_mode,
            analysis=analysis,
            enabled=self._emotion_enabled,
        )
        if len(plan.provider_text) > self._max_text_characters:
            plan = self._prosody_planner.plan(
                canonical_text=normalized,
                language_mode=language_mode,
                analysis=analysis,
                enabled=False,
            )
        dictionary_id = self._dictionary_resolver.resolve(
            language_code=language_code,
        )
        started = time.monotonic()
        try:
            provider_options = {
                "text": plan.provider_text,
                "standard_voice_profile": standard_voice_profile,
                "language_code": language_code,
                "dictionary_id": dictionary_id,
                "pace": plan.pace,
                "temperature": plan.temperature,
            }
            if selected_voice is not None:
                provider_options["selected_voice"] = selected_voice
            result = await self._provider.synthesize(
                **provider_options,
            )
        except Exception as exc:
            logger.warning(
                "Message speech failed (provider=sarvam, voice_profile=%s, "
                "language_mode=%s, emotion=%s, confidence=%s, pace=%.2f, "
                "temperature=%.2f, prosody_shaping_applied=%s, "
                "dictionary_applied=%s, elapsed_ms=%d, category=%s).",
                selected_voice or standard_voice_profile.value,
                language_mode.value,
                plan.emotion.value,
                plan.confidence.value,
                plan.pace,
                plan.temperature,
                plan.prosody_shaping_applied,
                dictionary_id is not None,
                int((time.monotonic() - started) * 1000),
                getattr(exc, "code", type(exc).__name__),
            )
            raise
        logger.info(
            "Message speech completed (provider=sarvam, voice_profile=%s, "
            "language_mode=%s, emotion=%s, confidence=%s, pace=%.2f, "
            "temperature=%.2f, prosody_shaping_applied=%s, "
            "dictionary_applied=%s, elapsed_ms=%d, audio_bytes=%d).",
            selected_voice or standard_voice_profile.value,
            language_mode.value,
            plan.emotion.value,
            plan.confidence.value,
            plan.pace,
            plan.temperature,
            plan.prosody_shaping_applied,
            dictionary_id is not None,
            int((time.monotonic() - started) * 1000),
            len(result.content),
        )
        return result
