import re


_COMPANION_NAME = re.compile(r"(?<![\w])Rya(?![\w])")
_FULL_BRAND_NAME = re.compile(r"(?<![\w])LegaRya(?![\w])")


def normalize_for_companion_speech(text: str, *, pronounce_full_brand: bool = False) -> str:
    """Return TTS-only text with Rya pronounced as two-syllable 'Riya'.

    This helper must never be used to render visible UI, persist identity, or
    normalize names belonging to Legacy subjects, visitors, or collaborators.
    """
    speech_text = _FULL_BRAND_NAME.sub("LegaRiya", text) if pronounce_full_brand else text
    return _COMPANION_NAME.sub("Riya", speech_text)
