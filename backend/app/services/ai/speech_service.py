"""Provider-independent orchestration for transient speech synthesis."""

import logging
from collections.abc import AsyncIterator

from app.services.ai.exceptions import AIConfigurationError, AIInvalidResponseError
from app.services.ai.provider import AIProvider, SPEECH_MEDIA_TYPES, SpeechChunk, SpeechResult
from app.services.speech_delivery_resolver import SpeechDeliveryResolver
from app.services.speech_text_normalizer import SpeechTextNormalizer
from app.services.voice_profile_resolver import StandardVoiceProfile


logger = logging.getLogger(__name__)


class SpeechService:
    """Resolve speech defaults and delegate generation to an AI provider."""

    def __init__(
        self,
        provider: AIProvider,
        *,
        model: str,
        default_voice: str,
        standard_male_voice: str,
        standard_female_voice: str,
        default_format: str,
        max_text_characters: int,
        timeout_seconds: float,
        text_normalizer: SpeechTextNormalizer | None = None,
        delivery_resolver: SpeechDeliveryResolver | None = None,
    ) -> None:
        self._provider = provider
        self._model = self._required_setting(model, "OPENAI_TTS_MODEL")
        self._default_voice = self._required_setting(
            default_voice,
            "OPENAI_TTS_VOICE",
        )
        self._standard_voices = {
            StandardVoiceProfile.MALE: self._required_setting(
                standard_male_voice,
                "OPENAI_TTS_MALE_VOICE",
            ),
            StandardVoiceProfile.FEMALE: self._required_setting(
                standard_female_voice,
                "OPENAI_TTS_FEMALE_VOICE",
            ),
        }
        normalized_format = self._required_setting(
            default_format,
            "OPENAI_TTS_FORMAT",
        ).lower()
        if normalized_format not in SPEECH_MEDIA_TYPES:
            raise AIConfigurationError("OPENAI_TTS_FORMAT is not supported.")
        if max_text_characters < 1 or max_text_characters > 4096:
            raise AIConfigurationError(
                "TTS_MAX_TEXT_CHARACTERS must be between 1 and 4096."
            )
        if timeout_seconds <= 0:
            raise AIConfigurationError("TTS_TIMEOUT_SECONDS must be positive.")
        self._default_format = normalized_format
        self._max_text_characters = max_text_characters
        self._timeout_seconds = timeout_seconds
        self._text_normalizer = text_normalizer or SpeechTextNormalizer()
        self._delivery_resolver = delivery_resolver or SpeechDeliveryResolver()

    async def synthesize(
        self,
        *,
        text: str,
        voice: str | None = None,
        standard_voice_profile: StandardVoiceProfile | str | None = None,
        response_format: str | None = None,
        preserve_text: bool = False,
        conversational_tone: str | None = None,
    ) -> SpeechResult:
        """Generate non-empty speech audio without persistence."""
        source_text = text if preserve_text else text.strip()
        if not source_text.strip():
            raise ValueError("Speech text must not be blank.")
        if len(source_text) > self._max_text_characters:
            raise ValueError("Speech text exceeds the configured maximum.")
        resolved_text = self._text_normalizer.normalize(source_text)
        if voice is not None and standard_voice_profile is not None:
            raise ValueError("Speech voice overrides are mutually exclusive.")
        if standard_voice_profile is not None:
            try:
                profile = StandardVoiceProfile(standard_voice_profile)
            except (TypeError, ValueError):
                raise AIConfigurationError(
                    "Standard voice profile is not supported."
                ) from None
            resolved_voice = self._standard_voices[profile]
        else:
            resolved_voice = (
                self._required_setting(voice, "voice")
                if voice is not None
                else self._default_voice
            )
        resolved_format = (
            response_format.strip().lower()
            if response_format is not None
            else self._default_format
        )
        if resolved_format not in SPEECH_MEDIA_TYPES:
            raise ValueError("Speech response format is not supported.")

        delivery = self._delivery_resolver.resolve(
            standard_voice_profile,
            resolved_text,
            conversational_tone,
        )
        logger.info(
            "TTS speech profile resolved (language_mode=%s, voice_profile=%s).",
            delivery.language_mode.value,
            (
                StandardVoiceProfile(standard_voice_profile).value
                if standard_voice_profile is not None
                else "neutral"
            ),
        )

        result = await self._provider.synthesize_speech(
            text=resolved_text,
            model=self._model,
            voice=resolved_voice,
            response_format=resolved_format,
            timeout_seconds=self._timeout_seconds,
            instructions=(
                delivery.instructions
                if self._model.startswith("gpt-4o-mini-tts")
                else None
            ),
        )
        if not isinstance(result, SpeechResult) or not result.content:
            raise AIInvalidResponseError(
                "Speech provider returned empty or malformed audio."
            )
        if (
            result.media_type != SPEECH_MEDIA_TYPES[resolved_format]
            or result.file_extension != resolved_format
        ):
            raise AIInvalidResponseError(
                "Speech provider returned inconsistent audio metadata."
            )
        return result

    @property
    def supports_streaming(self) -> bool:
        return self._provider.supports_streaming_speech

    async def stream(
        self, *, text: str, voice: str | None = None,
        standard_voice_profile: StandardVoiceProfile | str | None = None,
        conversational_tone: str | None = None,
    ) -> AsyncIterator[SpeechChunk]:
        """Yield raw 24 kHz PCM while preserving ordinary delivery guidance."""
        source_text = text.strip()
        if not source_text:
            raise ValueError("Speech text must not be blank.")
        if len(source_text) > self._max_text_characters:
            raise ValueError("Speech text exceeds the configured maximum.")
        resolved_text = self._text_normalizer.normalize(source_text)
        if voice is not None and standard_voice_profile is not None:
            raise ValueError("Speech voice overrides are mutually exclusive.")
        if standard_voice_profile is not None:
            try:
                profile = StandardVoiceProfile(standard_voice_profile)
            except (TypeError, ValueError):
                raise AIConfigurationError(
                    "Standard voice profile is not supported."
                ) from None
            resolved_voice = self._standard_voices[profile]
        else:
            resolved_voice = (
                self._required_setting(voice, "voice")
                if voice is not None else self._default_voice
            )
        delivery = self._delivery_resolver.resolve(
            standard_voice_profile, resolved_text, conversational_tone,
        )
        if not self.supports_streaming:
            raise NotImplementedError
        yielded = False
        async for chunk in self._provider.stream_speech(
            text=resolved_text,
            model=self._model,
            voice=resolved_voice,
            response_format="pcm",
            timeout_seconds=self._timeout_seconds,
            instructions=(
                delivery.instructions
                if self._model.startswith("gpt-4o-mini-tts") else None
            ),
        ):
            if not isinstance(chunk, SpeechChunk) or not chunk.content:
                raise AIInvalidResponseError(
                    "Speech provider returned an invalid streaming chunk."
                )
            yielded = True
            yield chunk
        if not yielded:
            raise AIInvalidResponseError("Speech provider returned no streaming audio.")

    @staticmethod
    def _required_setting(value: str | None, name: str) -> str:
        normalized = value.strip() if isinstance(value, str) else ""
        if not normalized:
            raise AIConfigurationError(f"{name} must be configured.")
        return normalized
