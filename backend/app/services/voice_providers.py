"""Provider contracts and dependency-free fakes for the L21 voice pipeline.

The real Whisper preparation adapter lives at the separate worker boundary.
Synthesis adapters remain intentionally absent in L21.3. Deterministic fakes
keep the ordinary suite independent of media tools, ML weights, and GPUs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
from typing import Literal, Protocol


WHISPER_MODEL = "openai/whisper-large-v3-turbo"
WHISPER_REVISION = "41f01f3fe87f28c78e2fbf8b568835947dd65ed9"
REFERENCE_RECIPE = "l21-reference-preparation-v1"
REFERENCE_BINDING_SCHEMA = "l21-reference-binding-v1"


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
    reference_audio: bytes
    raw_transcript_digest: str
    detected_language: str
    selected_start_ms: int
    asr_model: str
    asr_revision: str
    recipe_revision: str


@dataclass(frozen=True)
class ClonedSpeech:
    pcm_s16le: bytes
    sample_rate: int
    channels: int
    text_digest: str
    completion_generation: int


class ReferencePreparationProvider(Protocol):
    async def prepare(self, *, source: bytes, language: str, operation_generation: int,
                      declared_mime: str = "audio/wav") -> PreparedReference: ...


class ClonedSpeechProvider(Protocol):
    async def synthesize(
        self, *, authoritative_text: str, reference_audio: bytes,
        reference_text: str, language: str, operation_generation: int,
    ) -> ClonedSpeech: ...


Outcome = Literal["success", "failure", "timeout", "cancellation", "malformed", "stale_completion"]


def reference_binding_digest(*, audio_digest: str, transcript_digest: str,
                             raw_transcript_digest: str, language: str,
                             asr_model: str, asr_revision: str,
                             recipe_revision: str) -> str:
    value = {
        "schema": REFERENCE_BINDING_SCHEMA,
        "audio_sha256": audio_digest,
        "normalized_transcript_sha256": transcript_digest,
        "raw_transcript_sha256": raw_transcript_digest,
        "language": language,
        "asr_model": asr_model,
        "asr_revision": asr_revision,
        "recipe_revision": recipe_revision,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()


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

    async def prepare(self, *, source: bytes, language: str, operation_generation: int,
                      declared_mime: str = "audio/wav") -> PreparedReference:
        _fault(self.outcome)
        source_digest = hashlib.sha256(source).hexdigest()
        self.calls.append((source_digest, operation_generation, language))
        if self.outcome == "malformed":
            return PreparedReference("", "bad", source_digest, "bad", 0, 0, -1,
                operation_generation, b"", "bad", "", -1, "", "", "")
        transcript = "नमस्कार, हा चाचणी संदर्भ आहे."
        transcript_digest = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
        raw_digest = transcript_digest
        binding = reference_binding_digest(audio_digest=source_digest,
            transcript_digest=transcript_digest, raw_transcript_digest=raw_digest,
            language=language, asr_model="fake-reference-test-only",
            asr_revision="test-only", recipe_revision=REFERENCE_RECIPE)
        generation = operation_generation - 1 if self.outcome == "stale_completion" else operation_generation
        return PreparedReference(transcript, transcript_digest, source_digest, binding,
            24000, 1, 1000, generation, source, raw_digest, language, 0,
            "fake-reference-test-only", "test-only", REFERENCE_RECIPE)


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
                value.transcript_digest, value.raw_transcript_digest,
                value.audio_digest, value.binding_digest))
            or value.detected_language not in {"mr", "hi"} or value.selected_start_ms < 0
            or not value.asr_model or not value.asr_revision
            or value.recipe_revision != REFERENCE_RECIPE
            or hashlib.sha256(value.transcript.encode("utf-8")).hexdigest() != value.transcript_digest
            or (value.reference_audio and hashlib.sha256(value.reference_audio).hexdigest() != value.audio_digest)
            or reference_binding_digest(audio_digest=value.audio_digest,
                transcript_digest=value.transcript_digest,
                raw_transcript_digest=value.raw_transcript_digest,
                language="mr", asr_model=value.asr_model,
                asr_revision=value.asr_revision,
                recipe_revision=value.recipe_revision) != value.binding_digest):
        raise VoiceProviderFailure("voice_provider_output_invalid")


def validate_speech(value: ClonedSpeech, text: str, generation: int) -> None:
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if (not isinstance(value, ClonedSpeech) or value.completion_generation != generation
            or value.sample_rate != 24000 or value.channels != 1
            or not value.pcm_s16le or len(value.pcm_s16le) % 2
            or len(value.pcm_s16le) > 24_000 * 2 * 120
            or value.text_digest != expected):
        raise VoiceProviderFailure("voice_provider_output_invalid")
