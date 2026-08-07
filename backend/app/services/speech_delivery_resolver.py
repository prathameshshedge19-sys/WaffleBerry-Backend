"""Provider-independent natural speech delivery profiles."""

from dataclasses import dataclass

from app.services.ai.exceptions import AIConfigurationError
from app.services.speech_language_analyzer import (
    SpeechLanguageAnalyzer,
    SpeechLanguageMode,
)
from app.services.voice_profile_resolver import StandardVoiceProfile


@dataclass(frozen=True)
class SpeechDeliveryProfile:
    instructions: str
    language_mode: SpeechLanguageMode


EMOTIONAL_DELIVERY_INSTRUCTIONS = {
    "warm": "Use restrained warmth and a gently familiar cadence.",
    "happy": "Use positive, warm energy at a natural pace without sounding theatrical.",
    "excited": "Use slightly quicker, lively delivery while remaining clear and controlled.",
    "gentle": "Use a slightly slower, softer, calm delivery with restrained pauses.",
    "comforting": "Use a slightly slower, quiet and supportive delivery without melodrama.",
    "nostalgic": "Use a slightly slower, reflective cadence without implying extra memories.",
    "serious": "Use measured, attentive delivery with calm emphasis.",
}


MALE_DELIVERY_INSTRUCTIONS = """Use a warm, relaxed, natural conversational voice.
Sound emotionally present and familiar, as if speaking privately to a close family member.
Use realistic pauses, varied rhythm, gentle emphasis, and natural sentence endings.
Avoid narrator, announcer, audiobook, customer-service, or theatrical delivery.
Keep the pacing calm but not slow, without excessive syllable-by-syllable enunciation."""

FEMALE_DELIVERY_INSTRUCTIONS = """Use a warm, natural, emotionally present conversational voice.
Sound familiar and relaxed, as if speaking privately to a close family member.
Use realistic pauses, varied rhythm, soft emphasis, and natural sentence endings.
Avoid narrator, announcer, audiobook, customer-service, overly sweet, polished, or theatrical delivery.
Keep the pacing conversational and emotionally grounded."""

NEUTRAL_DELIVERY_INSTRUCTIONS = """Use a warm, relaxed, natural conversational voice.
Use realistic pauses, varied rhythm, gentle emphasis, and natural sentence endings.
Avoid narrator, announcer, customer-service, or theatrical delivery."""

LANGUAGE_INSTRUCTIONS = {
    SpeechLanguageMode.ENGLISH: (
        "Speak naturally in clear conversational English without forcing a regional accent."
    ),
    SpeechLanguageMode.MARATHI_DEVANAGARI: (
        "Speak naturally in broadly understandable conversational Marathi. Use native Marathi "
        "pronunciation, stress, rhythm, sentence melody, phrase-level pauses, and soft endings. "
        "Do not use Hindi rhythm or Hindi vowel patterns. Use a relaxed, familiar Maharashtrian "
        "cadence without forcing a narrow regional dialect. Pronounce Indian names and Maharashtra "
        "place names naturally."
    ),
    SpeechLanguageMode.HINDI_DEVANAGARI: (
        "Speak naturally in relaxed everyday conversational Hindi. Use native Hindi pronunciation, "
        "stress, rhythm, sentence melody, phrase pauses, and conversational endings. Do not apply "
        "English rhythm or formal newsreader delivery. Pronounce Indian names and place names naturally."
    ),
    SpeechLanguageMode.DEVANAGARI_UNKNOWN: (
        "Speak the supplied Devanagari text faithfully in its written language, using natural "
        "Indian-language pronunciation, rhythm, and sentence melody. Do not assume all Devanagari "
        "text is Hindi. Preserve names, numbers, and code-switching."
    ),
    SpeechLanguageMode.ROMANIZED_MARATHI: (
        "Speak this Latin-script text as conversational Marathi, not ordinary English. Interpret "
        "common Romanized Marathi spellings with natural Marathi pronunciation and cadence. Keep "
        "clearly English words natural when code-switching. Do not transliterate the written text."
    ),
    SpeechLanguageMode.MIXED_MARATHI_ENGLISH: (
        "Speak this as natural Marathi-English code-switching. Use Marathi rhythm and pronunciation "
        "for Marathi words and natural English pronunciation for English words. Keep transitions "
        "smooth and conversational without forcing the sentence into one accent."
    ),
    SpeechLanguageMode.MIXED_HINDI_ENGLISH: (
        "Speak this as natural Hindi-English code-switching. Use Hindi rhythm and pronunciation for "
        "Hindi words and natural English pronunciation for English words. Keep transitions smooth "
        "and conversational without forcing the sentence into one accent."
    ),
    SpeechLanguageMode.MULTILINGUAL_UNKNOWN: (
        "Preserve the written language, natural pronunciation, names, numbers, and code-switching "
        "without assuming or translating the language."
    ),
}

FINAL_FIDELITY_INSTRUCTIONS = (
    "Preserve the exact supplied wording, names, numbers, dates, times, currency values, and word "
    "order. Do not translate, transliterate, paraphrase, summarize, answer, explain, add, or remove "
    "anything. Speak only the supplied text."
)


class SpeechDeliveryResolver:
    def __init__(self, analyzer: SpeechLanguageAnalyzer | None = None) -> None:
        self._analyzer = analyzer or SpeechLanguageAnalyzer()

    def resolve(
        self,
        voice_profile: StandardVoiceProfile | str | None,
        text: str,
        conversational_tone: str | None = None,
    ) -> SpeechDeliveryProfile:
        if voice_profile is None:
            warmth = NEUTRAL_DELIVERY_INSTRUCTIONS
        else:
            try:
                profile = StandardVoiceProfile(voice_profile)
            except (TypeError, ValueError):
                raise AIConfigurationError(
                    "Standard voice profile is not supported for speech delivery."
                ) from None
            warmth = (
                MALE_DELIVERY_INSTRUCTIONS
                if profile is StandardVoiceProfile.MALE
                else FEMALE_DELIVERY_INSTRUCTIONS
            )
        language_mode = self._analyzer.detect(text)
        tone_guidance = EMOTIONAL_DELIVERY_INSTRUCTIONS.get(
            getattr(conversational_tone, "value", conversational_tone), ""
        )
        instruction_parts = [LANGUAGE_INSTRUCTIONS[language_mode], warmth]
        if tone_guidance:
            instruction_parts.append(tone_guidance)
        instruction_parts.append(FINAL_FIDELITY_INSTRUCTIONS)
        return SpeechDeliveryProfile(
            instructions="\n".join(instruction_parts),
            language_mode=language_mode,
        )

    def detect_language_mode(self, text: str) -> SpeechLanguageMode:
        return self._analyzer.detect(text)
