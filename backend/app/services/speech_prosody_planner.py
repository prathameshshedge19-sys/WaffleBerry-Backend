"""Safe punctuation-only emotional prosody planning for speech synthesis."""

from dataclasses import dataclass
import re

from app.services.speech_emotion_analyzer import (
    EmotionConfidence,
    SpeechEmotion,
    SpeechEmotionAnalysis,
)
from app.services.speech_language_analyzer import SpeechLanguageMode


@dataclass(frozen=True, slots=True)
class SpeechEmotionProfile:
    emotion: SpeechEmotion
    pace: float
    temperature: float
    sentence_pause_ms: int
    paragraph_pause_ms: int
    allow_nonverbal_cues: bool = False


EMOTION_PROFILES = {
    SpeechEmotion.NEUTRAL: SpeechEmotionProfile(SpeechEmotion.NEUTRAL, 0.96, 0.55, 260, 480),
    SpeechEmotion.WARM: SpeechEmotionProfile(SpeechEmotion.WARM, 0.92, 0.68, 300, 540),
    SpeechEmotion.REASSURING: SpeechEmotionProfile(SpeechEmotion.REASSURING, 0.88, 0.62, 340, 620),
    SpeechEmotion.PEACEFUL: SpeechEmotionProfile(SpeechEmotion.PEACEFUL, 0.84, 0.50, 380, 700),
    SpeechEmotion.NOSTALGIC: SpeechEmotionProfile(SpeechEmotion.NOSTALGIC, 0.87, 0.66, 360, 680),
    SpeechEmotion.SAD: SpeechEmotionProfile(SpeechEmotion.SAD, 0.82, 0.52, 380, 700),
    SpeechEmotion.JOYFUL: SpeechEmotionProfile(SpeechEmotion.JOYFUL, 1.00, 0.76, 250, 460),
    SpeechEmotion.EXCITED: SpeechEmotionProfile(SpeechEmotion.EXCITED, 1.06, 0.82, 220, 420),
    SpeechEmotion.SERIOUS: SpeechEmotionProfile(SpeechEmotion.SERIOUS, 0.90, 0.45, 320, 580),
    SpeechEmotion.ANGRY: SpeechEmotionProfile(SpeechEmotion.ANGRY, 1.00, 0.70, 280, 520),
}


@dataclass(frozen=True, slots=True)
class SpeechRenderingPlan:
    emotion: SpeechEmotion
    confidence: EmotionConfidence
    language_mode: SpeechLanguageMode
    pace: float
    temperature: float
    canonical_text: str
    provider_text: str
    prosody_shaping_applied: bool


class SpeechRenderingIntegrityComparator:
    """Ensure shaping changed punctuation and whitespace, never words."""

    _PERMITTED = re.compile(r"[\s,.!?;:…\u0964\u0965\-–—]+")

    def is_safe(self, canonical_text: str, provider_text: str) -> bool:
        return self._PERMITTED.sub("", canonical_text) == self._PERMITTED.sub(
            "", provider_text
        )


class SpeechProsodyPlanner:
    _REFLECTIVE = frozenset({
        SpeechEmotion.NOSTALGIC, SpeechEmotion.SAD, SpeechEmotion.PEACEFUL,
    })

    def __init__(
        self,
        comparator: SpeechRenderingIntegrityComparator | None = None,
    ) -> None:
        self._comparator = comparator or SpeechRenderingIntegrityComparator()

    def plan(
        self,
        *,
        canonical_text: str,
        language_mode: SpeechLanguageMode,
        analysis: SpeechEmotionAnalysis,
        enabled: bool,
    ) -> SpeechRenderingPlan:
        selected = analysis if enabled else SpeechEmotionAnalysis(
            SpeechEmotion.NEUTRAL, EmotionConfidence.LOW
        )
        profile = EMOTION_PROFILES[selected.emotion]
        shaped = canonical_text
        if enabled:
            shaped = re.sub(r"!{2,}", "!", shaped)
            shaped = re.sub(r"\?{2,}", "?", shaped)
            shaped = re.sub(r"[!?]{2,}", lambda match: match.group(0)[0], shaped)
            shaped = re.sub(r"\s*[—–]\s*", ", ", shaped)
            shaped = re.sub(r"[ \t]*\n{2,}[ \t]*", "\n\n", shaped)
            if selected.emotion in self._REFLECTIVE:
                shaped = re.sub(r"\.(?=\s)", "...", shaped, count=1)
            if len(shaped) > 180:
                shaped = re.sub(
                    r",\s+(?=(?:and|but|because)\b)", ". ", shaped,
                    count=1, flags=re.IGNORECASE,
                )
        if not self._comparator.is_safe(canonical_text, shaped):
            selected = SpeechEmotionAnalysis(
                SpeechEmotion.NEUTRAL, EmotionConfidence.LOW
            )
            profile = EMOTION_PROFILES[SpeechEmotion.NEUTRAL]
            shaped = canonical_text
        return SpeechRenderingPlan(
            emotion=selected.emotion,
            confidence=selected.confidence,
            language_mode=language_mode,
            pace=profile.pace,
            temperature=profile.temperature,
            canonical_text=canonical_text,
            provider_text=shaped,
            prosody_shaping_applied=shaped != canonical_text,
        )
