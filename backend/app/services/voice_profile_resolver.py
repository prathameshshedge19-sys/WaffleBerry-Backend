"""Resolve internal standard voice profiles from persisted Legacy data."""

from enum import Enum

from app.services.ai.exceptions import AIConfigurationError


class StandardVoiceProfile(str, Enum):
    """Provider-neutral standard voices used before consented cloning."""

    MALE = "standard_male"
    FEMALE = "standard_female"


MALE_RELATIONSHIPS = frozenset({
    "father",
    "brother",
    "grandfather",
})
FEMALE_RELATIONSHIPS = frozenset({
    "mother",
    "sister",
    "grandmother",
})


class StandardVoiceResolver:
    """Apply deterministic Legacy relationship rules with a safe fallback."""

    def __init__(self, default_profile: str | StandardVoiceProfile) -> None:
        try:
            self._default_profile = StandardVoiceProfile(default_profile)
        except (TypeError, ValueError):
            raise AIConfigurationError(
                "DEFAULT_STANDARD_VOICE_PROFILE must be standard_male or "
                "standard_female."
            ) from None

    def resolve(self, relationship: str | None) -> StandardVoiceProfile:
        """Resolve only explicit known relationships; never infer identity."""
        normalized = self._normalize(relationship)
        if normalized in MALE_RELATIONSHIPS:
            return StandardVoiceProfile.MALE
        if normalized in FEMALE_RELATIONSHIPS:
            return StandardVoiceProfile.FEMALE
        return self._default_profile

    @staticmethod
    def _normalize(value: str | None) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(value.split()).casefold()
