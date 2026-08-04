"""Provider-neutral AI contracts."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import BinaryIO, Literal, Mapping, Sequence


AIMessageRole = Literal["system", "user", "assistant"]
ExternalKnowledgeMode = Literal["web_search"]

SPEECH_MEDIA_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "pcm": "audio/L16",
}


@dataclass(frozen=True, slots=True)
class AIMessage:
    """A provider-neutral chat message."""

    role: AIMessageRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("AI message content must not be blank.")


@dataclass(frozen=True, slots=True)
class SpeechResult:
    """Provider-neutral generated speech audio."""

    content: bytes
    media_type: str
    file_extension: str


class AIProvider(ABC):
    """Interface implemented by every AI provider adapter."""

    @abstractmethod
    async def generate_response(
        self,
        messages: Sequence[AIMessage],
        *,
        structured_response_schema: Mapping[str, object] | None = None,
        external_knowledge_mode: ExternalKnowledgeMode | None = None,
    ) -> str:
        """Generate text, optionally constrained by a JSON Schema."""
        raise NotImplementedError

    async def stream_response(
        self,
        messages: Sequence[AIMessage],
        *,
        external_knowledge_mode: ExternalKnowledgeMode | None = None,
    ) -> AsyncIterator[str]:
        """Stream assistant text when supported by the provider."""
        if False:
            yield ""
        raise NotImplementedError

    async def transcribe_audio(
        self,
        audio: BinaryIO,
        *,
        filename: str,
        content_type: str,
        model: str,
    ) -> str:
        """Return transcript text for one transient audio stream."""
        raise NotImplementedError

    async def synthesize_speech(
        self,
        *,
        text: str,
        model: str,
        voice: str,
        response_format: str,
        timeout_seconds: float,
    ) -> SpeechResult:
        """Return transient speech audio without exposing provider objects."""
        raise NotImplementedError
