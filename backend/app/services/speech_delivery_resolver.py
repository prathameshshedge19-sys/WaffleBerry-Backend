"""Provider-independent natural speech delivery profiles."""

from dataclasses import dataclass
from enum import Enum
import re

from app.services.ai.exceptions import AIConfigurationError
from app.services.voice_profile_resolver import StandardVoiceProfile


class SpeechLanguageMode(str, Enum):
    """Deterministic script-level language guidance for speech delivery."""

    ENGLISH = "english"
    DEVANAGARI = "devanagari"
    MIXED = "mixed"
    MULTILINGUAL = "multilingual"


@dataclass(frozen=True)
class SpeechDeliveryProfile:
    """Final provider-neutral instructions for one speech request."""

    instructions: str
    language_mode: SpeechLanguageMode


MALE_DELIVERY_INSTRUCTIONS = """Speak in a warm, relaxed, natural conversational voice.
Sound emotionally present and familiar, as if speaking privately to a close family member.
Use realistic pauses, varied rhythm, gentle emphasis, and natural sentence endings.
Avoid sounding like an AI assistant, narrator, announcer, audiobook reader, or customer-service representative.
Do not over-enunciate every word.
Do not use exaggerated enthusiasm or theatrical emotion.
Keep the pacing calm but not slow.
Allow subtle imperfections in rhythm so the delivery feels human."""

FEMALE_DELIVERY_INSTRUCTIONS = """Speak in a warm, natural, emotionally present conversational voice.
Sound familiar and relaxed, as if speaking privately to a close family member.
Use realistic pauses, varied rhythm, soft emphasis, and natural sentence endings.
Avoid sounding like an AI assistant, narrator, announcer, audiobook reader, or customer-service representative.
Do not sound overly sweet, polished, theatrical, or artificially cheerful.
Keep the pacing conversational and emotionally grounded.
Allow subtle variation in rhythm so the delivery feels human."""

NEUTRAL_DELIVERY_INSTRUCTIONS = """Speak warmly and naturally in a relaxed conversational voice.
Use realistic pauses, varied rhythm, gentle emphasis, and natural sentence endings.
Avoid sounding like an AI assistant, narrator, announcer, or customer-service representative.
Keep the pacing conversational and emotionally grounded."""

LANGUAGE_INSTRUCTIONS = {
    SpeechLanguageMode.ENGLISH: (
        "Use clear, neutral conversational English without forcing a regional accent."
    ),
    SpeechLanguageMode.DEVANAGARI: (
        "Speak Devanagari-script text naturally with its original pronunciation "
        "and cadence. Do not translate it."
    ),
    SpeechLanguageMode.MIXED: (
        "Preserve natural code-switching between Devanagari and Latin-script "
        "text. Do not translate or force all words into one language's pronunciation."
    ),
    SpeechLanguageMode.MULTILINGUAL: (
        "Preserve the text's original language and pronunciation without translating."
    ),
}


class SpeechDeliveryResolver:
    """Resolve a stable profile and lightweight language-aware guidance."""

    def resolve(
        self,
        voice_profile: StandardVoiceProfile | str | None,
        text: str,
    ) -> SpeechDeliveryProfile:
        if voice_profile is None:
            base = NEUTRAL_DELIVERY_INSTRUCTIONS
        else:
            try:
                profile = StandardVoiceProfile(voice_profile)
            except (TypeError, ValueError):
                raise AIConfigurationError(
                    "Standard voice profile is not supported for speech delivery."
                ) from None
            base = (
                MALE_DELIVERY_INSTRUCTIONS
                if profile is StandardVoiceProfile.MALE
                else FEMALE_DELIVERY_INSTRUCTIONS
            )

        language_mode = self.detect_language_mode(text)
        return SpeechDeliveryProfile(
            instructions=f"{base}\n{LANGUAGE_INSTRUCTIONS[language_mode]}",
            language_mode=language_mode,
        )

    @staticmethod
    def detect_language_mode(text: str) -> SpeechLanguageMode:
        has_devanagari = bool(re.search(r"[\u0900-\u097f]", text))
        has_latin = bool(re.search(r"[A-Za-z]", text))
        if has_devanagari and has_latin:
            return SpeechLanguageMode.MIXED
        if has_devanagari:
            return SpeechLanguageMode.DEVANAGARI
        if has_latin:
            return SpeechLanguageMode.ENGLISH
        return SpeechLanguageMode.MULTILINGUAL
