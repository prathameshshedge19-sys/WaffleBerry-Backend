"""Dependency-free provider contracts for the L21 control plane.

Real IndicF5/Whisper/Vocos adapters intentionally do not exist in this phase.
The deterministic fakes exercise worker validation and cancellation without
loading media tools, ML libraries, weights, credentials, or a GPU.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from typing import Literal, Protocol


class VoiceProviderFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PreparedReference:
    transcript: str
    transcript_digest: str
    audio_digest: str
    binding_digest: str
    sample_rate: int
    channels: int
    duration_ms: int
    completion_generation: int


@dataclass(frozen=True)
class ClonedSpeech:
    pcm_s16le: bytes
    sample_rate: int
    channels: int
    text_digest: str
    completion_generation: int


class ReferencePreparationProvider(Protocol):
    async def prepare(self, *, source: bytes, language: str, operation_generation: int) -> PreparedReference: ...


class ClonedSpeechProvider(Protocol):
    async def synthesize(
        self, *, authoritative_text: str, reference_audio: bytes,
        reference_text: str, language: str, operation_generation: int,
    ) -> ClonedSpeech: ...


Outcome = Literal["success", "failure", "timeout", "cancellation", "malformed", "stale_completion"]


def _fault(outcome: Outcome) -> None:
    if outcome == "failure":
        raise VoiceProviderFailure("voice_provider_failed")
    if outcome == "timeout":
        raise TimeoutError("voice_provider_timeout")
    if outcome == "cancellation":
        raise asyncio.CancelledError()


class FakeReferencePreparationProvider:
    provider_name = "fake-reference-test-only"

    def __init__(self, outcome: Outcome = "success"):
        self.outcome = outcome
        self.calls: list[tuple[str, int, str]] = []

    async def prepare(self, *, source: bytes, language: str, operation_generation: int) -> PreparedReference:
        _fault(self.outcome)
        source_digest = hashlib.sha256(source).hexdigest()
        self.calls.append((source_digest, operation_generation, language))
        if self.outcome == "malformed":
            return PreparedReference("", "bad", source_digest, "bad", 0, 0, -1, operation_generation)
        transcript = "नमस्कार, हा चाचणी संदर्भ आहे."
        transcript_digest = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
        binding = hashlib.sha256(f"{source_digest}:{transcript_digest}:{language}".encode()).hexdigest()
        generation = operation_generation - 1 if self.outcome == "stale_completion" else operation_generation
        return PreparedReference(transcript, transcript_digest, source_digest, binding, 24000, 1, 1000, generation)


class FakeClonedSpeechProvider:
    provider_name = "fake-cloned-speech-test-only"

    def __init__(self, outcome: Outcome = "success"):
        self.outcome = outcome
        self.calls: list[tuple[str, str, int]] = []

    async def synthesize(
        self, *, authoritative_text: str, reference_audio: bytes,
        reference_text: str, language: str, operation_generation: int,
    ) -> ClonedSpeech:
        _fault(self.outcome)
        text_digest = hashlib.sha256(authoritative_text.encode("utf-8")).hexdigest()
        self.calls.append((text_digest, language, operation_generation))
        if self.outcome == "malformed":
            return ClonedSpeech(b"odd", 0, 2, "bad", operation_generation)
        # 20 ms of deterministic 24 kHz mono signed-16-bit PCM. This is a test
        # fixture, not speech and never enters a production enrollment flow.
        seed = hashlib.sha256(reference_audio + reference_text.encode() + authoritative_text.encode()).digest()
        pcm = (seed * 30)[:960]
        generation = operation_generation - 1 if self.outcome == "stale_completion" else operation_generation
        return ClonedSpeech(pcm, 24000, 1, text_digest, generation)


def validate_prepared(value: PreparedReference, generation: int) -> None:
    if (not isinstance(value, PreparedReference) or value.completion_generation != generation
            or not value.transcript or len(value.transcript) > 4096
            or value.sample_rate != 24000 or value.channels != 1
            or not 250 <= value.duration_ms <= 15000
            or any(len(item) != 64 for item in (
                value.transcript_digest, value.audio_digest, value.binding_digest))
            or hashlib.sha256(value.transcript.encode("utf-8")).hexdigest() != value.transcript_digest):
        raise VoiceProviderFailure("voice_provider_output_invalid")


def validate_speech(value: ClonedSpeech, text: str, generation: int) -> None:
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if (not isinstance(value, ClonedSpeech) or value.completion_generation != generation
            or value.sample_rate != 24000 or value.channels != 1
            or not value.pcm_s16le or len(value.pcm_s16le) % 2
            or len(value.pcm_s16le) > 24_000 * 2 * 120
            or value.text_digest != expected):
        raise VoiceProviderFailure("voice_provider_output_invalid")
