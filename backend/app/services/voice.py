import hashlib
import logging
from dataclasses import dataclass
from typing import Protocol

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError, OpenAIError, RateLimitError

from app.config import Settings, get_settings
from app.services.speech_pronunciation import normalize_for_companion_speech


logger = logging.getLogger(__name__)
VOICE_PREVIEW_TEXT = "Hi, I'm Riya. I'm here to listen."
VOICE_INSTRUCTIONS = "Speak warmly, calmly, naturally, and conversationally. Be emotionally aware but restrained and never theatrical."


class VoiceProviderError(RuntimeError):
    def __init__(self, kind: str):
        super().__init__("Voice provider request failed.")
        self.kind = kind


class VoiceProvider(Protocol):
    async def transcribe(self, audio: bytes, filename: str, content_type: str) -> str: ...
    async def synthesize(self, text: str, voice: str) -> bytes: ...


class OpenAIVoiceProvider:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.openai_api_key:
            raise VoiceProviderError("voice_provider_configuration")
        self.client = AsyncOpenAI(api_key=self.settings.openai_api_key)

    @staticmethod
    def _error(exc: OpenAIError) -> VoiceProviderError:
        if isinstance(exc, APIConnectionError):
            return VoiceProviderError("voice_provider_connection")
        if isinstance(exc, AuthenticationError):
            return VoiceProviderError("voice_provider_authentication")
        if isinstance(exc, RateLimitError):
            return VoiceProviderError("voice_provider_rate_limit")
        if isinstance(exc, APIStatusError):
            return VoiceProviderError("voice_provider_api_status")
        return VoiceProviderError("voice_provider_error")

    async def transcribe(self, audio: bytes, filename: str, content_type: str) -> str:
        try:
            result = await self.client.audio.transcriptions.create(
                model=self.settings.voice_transcription_model,
                file=(filename, audio, content_type),
                response_format="json",
            )
        except OpenAIError as exc:
            raise self._error(exc) from exc
        text = getattr(result, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise VoiceProviderError("voice_transcription_empty")
        return text.strip()

    async def synthesize(self, text: str, voice: str) -> bytes:
        try:
            response = await self.client.audio.speech.create(
                model=self.settings.voice_tts_model,
                voice=voice,
                input=text,
                instructions=VOICE_INSTRUCTIONS,
                response_format="mp3",
            )
        except OpenAIError as exc:
            raise self._error(exc) from exc
        audio = response.content
        if not audio:
            raise VoiceProviderError("voice_synthesis_empty")
        return audio


@dataclass(frozen=True)
class SpeechCacheKey:
    message_id: int
    voice: str
    content_hash: str


class SpeechCache:
    """Small process-local cache; source microphone recordings are never retained."""

    def __init__(self, max_entries: int = 256):
        self.max_entries = max_entries
        self._audio: dict[SpeechCacheKey, bytes] = {}

    @staticmethod
    def key(message_id: int, voice: str, content: str) -> SpeechCacheKey:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return SpeechCacheKey(message_id, voice, digest)

    def get(self, key: SpeechCacheKey) -> bytes | None:
        return self._audio.get(key)

    def put(self, key: SpeechCacheKey, audio: bytes) -> None:
        if len(self._audio) >= self.max_entries:
            self._audio.pop(next(iter(self._audio)))
        self._audio[key] = audio


speech_cache = SpeechCache()


def speech_text(content: str) -> str:
    return normalize_for_companion_speech(content)


def get_voice_provider() -> VoiceProvider:
    return OpenAIVoiceProvider()
