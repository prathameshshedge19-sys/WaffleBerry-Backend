"""Provider-neutral AI contracts."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import BinaryIO, Literal, Mapping, Protocol, Sequence


AIMessageRole = Literal["system", "user", "assistant"]
ExternalKnowledgeMode = Literal["web_search"]


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    """Provider-neutral per-request generation limits."""

    max_output_tokens: int | None = None

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


@dataclass(frozen=True, slots=True)
class SpeechChunk:
    """One ordered transient chunk from a streaming speech response."""

    content: bytes
    media_type: str
    file_extension: str
    sample_rate: int | None = None


class StreamingTranscriptionSession(Protocol):
    """One transient provider stream scoped to one user turn."""

    async def append_audio(self, chunk: bytes) -> str | None: ...
    async def finalize(self) -> str: ...
    async def close(self) -> None: ...


class AIProvider(ABC):
    """Interface implemented by every AI provider adapter."""

    @abstractmethod
    async def generate_response(
        self,
        messages: Sequence[AIMessage],
        *,
        structured_response_schema: Mapping[str, object] | None = None,
        external_knowledge_mode: ExternalKnowledgeMode | None = None,
        generation_options: GenerationOptions | None = None,
    ) -> str:
        """Generate text, optionally constrained by a JSON Schema."""
        raise NotImplementedError

    async def stream_response(
        self,
        messages: Sequence[AIMessage],
        *,
        external_knowledge_mode: ExternalKnowledgeMode | None = None,
        generation_options: GenerationOptions | None = None,
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
        instructions: str | None = None,
    ) -> SpeechResult:
        """Return transient speech audio without exposing provider objects."""
        raise NotImplementedError

    @property
    def supports_streaming_transcription(self) -> bool:
        """Whether this adapter accepts incremental live audio."""
        return False

    @property
    def supports_streaming_speech(self) -> bool:
        """Whether this adapter yields audio before synthesis completes."""
        return False

    async def start_transcription_stream(
        self, *, model: str, content_type: str,
    ) -> StreamingTranscriptionSession:
        """Open a transient incremental transcription stream when supported."""
        raise NotImplementedError

    async def stream_speech(
        self, *, text: str, model: str, voice: str, response_format: str,
        timeout_seconds: float, instructions: str | None = None,
    ) -> AsyncIterator[SpeechChunk]:
        """Yield ordered transient speech chunks when supported."""
        if False:
            yield SpeechChunk(b"", "", "")
        raise NotImplementedError
