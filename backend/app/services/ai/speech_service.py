"""Provider-independent orchestration for transient speech synthesis."""

from app.services.ai.exceptions import AIConfigurationError, AIInvalidResponseError
from app.services.ai.provider import AIProvider, SPEECH_MEDIA_TYPES, SpeechResult


class SpeechService:
    """Resolve speech defaults and delegate generation to an AI provider."""

    def __init__(
        self,
        provider: AIProvider,
        *,
        model: str,
        default_voice: str,
        default_format: str,
        max_text_characters: int,
        timeout_seconds: float,
    ) -> None:
        self._provider = provider
        self._model = self._required_setting(model, "OPENAI_TTS_MODEL")
        self._default_voice = self._required_setting(
            default_voice,
            "OPENAI_TTS_VOICE",
        )
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

    async def synthesize(
        self,
        *,
        text: str,
        voice: str | None = None,
        response_format: str | None = None,
        preserve_text: bool = False,
    ) -> SpeechResult:
        """Generate non-empty speech audio without persistence."""
        resolved_text = text if preserve_text else text.strip()
        if not resolved_text.strip():
            raise ValueError("Speech text must not be blank.")
        if len(resolved_text) > self._max_text_characters:
            raise ValueError("Speech text exceeds the configured maximum.")
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

        result = await self._provider.synthesize_speech(
            text=resolved_text,
            model=self._model,
            voice=resolved_voice,
            response_format=resolved_format,
            timeout_seconds=self._timeout_seconds,
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

    @staticmethod
    def _required_setting(value: str | None, name: str) -> str:
        normalized = value.strip() if isinstance(value, str) else ""
        if not normalized:
            raise AIConfigurationError(f"{name} must be configured.")
        return normalized
