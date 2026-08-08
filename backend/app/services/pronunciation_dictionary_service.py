"""Provider-independent validation and resolution for pronunciation data."""

from dataclasses import dataclass
import json
from pathlib import Path
import unicodedata

from app.services.ai.exceptions import AIConfigurationError


SARVAM_DICTIONARY_LANGUAGES = frozenset({
    "hi-IN", "bn-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN", "mr-IN",
    "gu-IN", "pa-IN", "od-IN", "en-IN",
})
MAX_DICTIONARY_ENTRIES = 100
MAX_DICTIONARY_BYTES = 1024 * 1024


class PronunciationDictionaryValidationError(ValueError):
    """A local dictionary source is malformed or exceeds provider limits."""


@dataclass(frozen=True, slots=True)
class ValidatedPronunciationDictionary:
    version: int
    description: str | None
    pronunciations: dict[str, dict[str, str]]
    entry_count: int

    def provider_payload(self) -> dict[str, object]:
        """Return only the exact object accepted by Sarvam."""
        return {"pronunciations": self.pronunciations}


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PronunciationDictionaryValidationError(
                "Pronunciation dictionary contains a duplicate JSON key."
            )
        result[key] = value
    return result


class PronunciationDictionarySourceLoader:
    """Load a UTF-8 versioned source and enforce Sarvam's documented limits."""

    def load(self, path: str | Path) -> ValidatedPronunciationDictionary:
        source = Path(path)
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise PronunciationDictionaryValidationError(
                "Pronunciation dictionary file could not be read."
            ) from exc
        if len(raw) > MAX_DICTIONARY_BYTES:
            raise PronunciationDictionaryValidationError(
                "Pronunciation dictionary exceeds the 1 MB limit."
            )
        try:
            document = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PronunciationDictionaryValidationError(
                "Pronunciation dictionary must be valid UTF-8 JSON."
            ) from exc
        return self.validate(document)

    def validate(self, document: object) -> ValidatedPronunciationDictionary:
        if not isinstance(document, dict):
            raise PronunciationDictionaryValidationError(
                "Pronunciation dictionary must be a JSON object."
            )
        allowed_metadata = {"version", "description", "pronunciations"}
        if set(document) - allowed_metadata:
            raise PronunciationDictionaryValidationError(
                "Pronunciation dictionary contains unsupported metadata."
            )
        version = document.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise PronunciationDictionaryValidationError(
                "Pronunciation dictionary version must be a positive integer."
            )
        description = document.get("description")
        if description is not None and (
            not isinstance(description, str) or not description.strip()
        ):
            raise PronunciationDictionaryValidationError(
                "Pronunciation dictionary description must be nonblank."
            )
        pronunciations = document.get("pronunciations")
        if not isinstance(pronunciations, dict) or not pronunciations:
            raise PronunciationDictionaryValidationError(
                "Pronunciation dictionary requires pronunciations."
            )
        total = 0
        cleaned: dict[str, dict[str, str]] = {}
        for language, mapping in pronunciations.items():
            if language not in SARVAM_DICTIONARY_LANGUAGES:
                raise PronunciationDictionaryValidationError(
                    "Pronunciation dictionary contains an unsupported language."
                )
            if not isinstance(mapping, dict) or not mapping:
                raise PronunciationDictionaryValidationError(
                    "Each pronunciation language must contain an object."
                )
            cleaned[language] = {}
            for term, spoken in mapping.items():
                self._validate_text(term, "source term")
                self._validate_text(spoken, "pronunciation")
                normalized_term = unicodedata.normalize("NFC", term)
                if normalized_term in cleaned[language]:
                    raise PronunciationDictionaryValidationError(
                        "Pronunciation dictionary contains a duplicate source term."
                    )
                cleaned[language][normalized_term] = (
                    unicodedata.normalize("NFC", spoken)
                )
                total += 1
        if total > MAX_DICTIONARY_ENTRIES:
            raise PronunciationDictionaryValidationError(
                "Pronunciation dictionary exceeds the 100-word limit."
            )
        payload = json.dumps(
            {"pronunciations": cleaned}, ensure_ascii=False
        ).encode("utf-8")
        if len(payload) > MAX_DICTIONARY_BYTES:
            raise PronunciationDictionaryValidationError(
                "Pronunciation dictionary provider payload exceeds 1 MB."
            )
        return ValidatedPronunciationDictionary(
            version=version,
            description=description.strip() if description else None,
            pronunciations=cleaned,
            entry_count=total,
        )

    @staticmethod
    def _validate_text(value: object, label: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise PronunciationDictionaryValidationError(
                f"Pronunciation dictionary {label} must be nonblank."
            )
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise PronunciationDictionaryValidationError(
                f"Pronunciation dictionary {label} contains control characters."
            )


class PronunciationDictionaryResolver:
    """Resolve the global dictionary; Legacy-specific resolution comes later."""

    def __init__(self, dictionary_id: str | None, *, required: bool) -> None:
        normalized = dictionary_id.strip() if isinstance(dictionary_id, str) else ""
        if isinstance(dictionary_id, str) and any(
            unicodedata.category(character) == "Cc" for character in dictionary_id
        ):
            raise AIConfigurationError(
                "SARVAM_PRONUNCIATION_DICTIONARY_ID is invalid."
            )
        if normalized and (
            len(normalized) > 256
            or any(character.isspace() for character in normalized)
        ):
            raise AIConfigurationError(
                "SARVAM_PRONUNCIATION_DICTIONARY_ID is invalid."
            )
        if required and not normalized:
            raise AIConfigurationError(
                "A Sarvam pronunciation dictionary is required but not configured."
            )
        self._dictionary_id = normalized or None

    def resolve(
        self,
        *,
        language_code: str,
        legacy_id: int | None = None,
    ) -> str | None:
        del legacy_id
        if language_code not in SARVAM_DICTIONARY_LANGUAGES:
            raise AIConfigurationError("Pronunciation dictionary language is invalid.")
        return self._dictionary_id
