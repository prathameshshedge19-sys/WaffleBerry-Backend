"""Strict word-and-number fidelity comparison for generated speech transcripts."""

import re
import unicodedata


class SpeechFidelityComparator:
    """Ignore presentation-only differences while preserving semantic tokens."""

    _APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'"})

    def equivalent(self, expected: str, actual: str) -> bool:
        if not isinstance(expected, str) or not isinstance(actual, str):
            return False
        return self._comparable(expected) == self._comparable(actual)

    @classmethod
    def _comparable(cls, value: str) -> str:
        value = unicodedata.normalize("NFKC", value).translate(cls._APOSTROPHES)
        comparable = []
        for character in value.casefold():
            category = unicodedata.category(character)
            if category[0] in {"L", "M", "N"} or category == "Sc" or character == "%":
                comparable.append(character)
            else:
                comparable.append(" ")
        return re.sub(r"\s+", " ", "".join(comparable)).strip()
