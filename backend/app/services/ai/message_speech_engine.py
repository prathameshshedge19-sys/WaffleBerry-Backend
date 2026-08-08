"""Provider-neutral engines for rendering a finalized stored message."""

import logging
import time
from typing import Protocol

from app.services.ai.exceptions import (
    AIAuthenticationError,
    AIConnectionError,
    AIProviderUnavailableError,
    AIQuotaExceededError,
    AIRateLimitError,
    AITimeoutError,
)
from app.services.ai.provider import SpeechResult
from app.services.ai.speech_service import SpeechService
from app.services.voice_profile_resolver import StandardVoiceProfile


logger = logging.getLogger(__name__)


class MessageSpeechEngine(Protocol):
    """Render one finalized message without changing conversation state."""

    async def synthesize(
        self,
        *,
        text: str,
        standard_voice_profile: StandardVoiceProfile,
        response_format: str | None,
        preserve_text: bool = True,
    ) -> SpeechResult:
        """Return provider-neutral playable audio."""
        ...


class ConfiguredMessageSpeechEngine:
    """Select the configured engine and optionally fall back once to TTS."""

    _FALLBACK_ERRORS = (
        AIAuthenticationError,
        AIConnectionError,
        AIProviderUnavailableError,
        AIQuotaExceededError,
        AIRateLimitError,
        AITimeoutError,
    )

    def __init__(
        self,
        *,
        selected_engine: str,
        tts_engine: SpeechService,
        realtime_engine: MessageSpeechEngine | None = None,
        fallback_to_tts: bool = True,
    ) -> None:
        selected = selected_engine.strip().lower()
        if selected not in {"tts", "realtime"}:
            from app.services.ai.exceptions import AIConfigurationError

            raise AIConfigurationError(
                "MESSAGE_SPEECH_ENGINE must be 'tts' or 'realtime'."
            )
        if selected == "realtime" and realtime_engine is None:
            from app.services.ai.exceptions import AIConfigurationError

            raise AIConfigurationError("Realtime speech is not configured.")
        self._selected = selected
        self._tts = tts_engine
        self._realtime = realtime_engine
        self._fallback = fallback_to_tts

    async def synthesize(
        self,
        *,
        text: str,
        standard_voice_profile: StandardVoiceProfile,
        response_format: str | None,
        preserve_text: bool = True,
    ) -> SpeechResult:
        started = time.monotonic()
        fallback_occurred = False
        try:
            if self._selected == "tts":
                result = await self._tts.synthesize(
                    text=text,
                    standard_voice_profile=standard_voice_profile,
                    response_format=response_format,
                    preserve_text=preserve_text,
                )
            else:
                try:
                    result = await self._realtime.synthesize(
                        text=text,
                        standard_voice_profile=standard_voice_profile,
                        response_format=response_format,
                        preserve_text=preserve_text,
                    )
                except self._FALLBACK_ERRORS:
                    if not self._fallback:
                        raise
                    fallback_occurred = True
                    result = await self._tts.synthesize(
                        text=text,
                        standard_voice_profile=standard_voice_profile,
                        response_format=response_format,
                        preserve_text=preserve_text,
                    )
        except Exception as exc:
            logger.warning(
                "Message speech failed (engine=%s, voice_profile=%s, "
                "format=%s, elapsed_ms=%d, category=%s, fallback=%s).",
                self._selected,
                standard_voice_profile.value,
                response_format or "default",
                int((time.monotonic() - started) * 1000),
                getattr(exc, "code", type(exc).__name__),
                fallback_occurred,
            )
            raise
        logger.info(
            "Message speech completed (engine=%s, voice_profile=%s, "
            "format=%s, elapsed_ms=%d, fallback=%s, audio_bytes=%d).",
            self._selected,
            standard_voice_profile.value,
            result.file_extension,
            int((time.monotonic() - started) * 1000),
            fallback_occurred,
            len(result.content),
        )
        return result
