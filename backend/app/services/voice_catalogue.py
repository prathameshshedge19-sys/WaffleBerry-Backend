"""Central provider-neutral catalogue for selectable Berry voices."""

from dataclasses import dataclass
from enum import Enum


INDIAN_RECOMMENDATION = (
    "Best suited for Indian languages and Indian English"
)
NATURAL_ENGLISH_RECOMMENDATION = (
    "Best suited for natural English, international languages, and Live Calls"
)


class VoiceGender(str, Enum):
    MALE = "male"
    FEMALE = "female"


class VoiceProvider(str, Enum):
    SARVAM = "sarvam"
    OPENAI = "openai"


@dataclass(frozen=True, slots=True)
class VoiceDefinition:
    id: str
    display_name: str
    gender: VoiceGender
    provider: VoiceProvider
    provider_voice: str
    recommendation: str

    def public_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.display_name,
            "recommendation": self.recommendation,
        }


VOICE_CATALOGUE = (
    VoiceDefinition("rohan", "Rohan", VoiceGender.MALE, VoiceProvider.SARVAM, "rohan", INDIAN_RECOMMENDATION),
    VoiceDefinition("mani", "Mani", VoiceGender.MALE, VoiceProvider.SARVAM, "mani", INDIAN_RECOMMENDATION),
    VoiceDefinition("shubh", "Shubh", VoiceGender.MALE, VoiceProvider.SARVAM, "shubh", INDIAN_RECOMMENDATION),
    VoiceDefinition("varun", "Varun", VoiceGender.MALE, VoiceProvider.SARVAM, "varun", INDIAN_RECOMMENDATION),
    VoiceDefinition("cedar", "Cedar", VoiceGender.MALE, VoiceProvider.OPENAI, "cedar", NATURAL_ENGLISH_RECOMMENDATION),
    VoiceDefinition("rupali", "Rupali", VoiceGender.FEMALE, VoiceProvider.SARVAM, "rupali", INDIAN_RECOMMENDATION),
    VoiceDefinition("simran", "Simran", VoiceGender.FEMALE, VoiceProvider.SARVAM, "simran", INDIAN_RECOMMENDATION),
    VoiceDefinition("ritu", "Ritu", VoiceGender.FEMALE, VoiceProvider.SARVAM, "ritu", INDIAN_RECOMMENDATION),
    VoiceDefinition("suhani", "Suhani", VoiceGender.FEMALE, VoiceProvider.SARVAM, "suhani", INDIAN_RECOMMENDATION),
    VoiceDefinition("marin", "Marin", VoiceGender.FEMALE, VoiceProvider.OPENAI, "marin", NATURAL_ENGLISH_RECOMMENDATION),
)
VOICE_BY_ID = {voice.id: voice for voice in VOICE_CATALOGUE}


def get_voice(voice_id: str | None) -> VoiceDefinition | None:
    return VOICE_BY_ID.get(voice_id) if voice_id is not None else None


def public_catalogue() -> dict[str, list[dict[str, str]]]:
    return {
        gender.value: [
            voice.public_dict()
            for voice in VOICE_CATALOGUE
            if voice.gender == gender
        ]
        for gender in VoiceGender
    }
