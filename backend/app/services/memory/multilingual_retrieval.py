"""Unicode-safe diagnostics and normalization for multilingual retrieval."""

import re
import unicodedata


_DEVANAGARI_PATTERN = re.compile(r"[\u0900-\u097f]")
_LATIN_PATTERN = re.compile(r"[a-z]")


def _unicode_words(value: str) -> list[str]:
    """Group Unicode letters/numbers and their combining marks into words."""
    words: list[str] = []
    current: list[str] = []
    for character in value:
        category = unicodedata.category(character)
        if category[0] in {"L", "N"}:
            current.append(character)
        elif category[0] == "M" and current:
            current.append(character)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def retrieval_tokens(value: str | None) -> list[str]:
    """Return normalized source tokens without translation or alias expansion."""
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return list(dict.fromkeys(_unicode_words(normalized)))


def normalize_embedding_text(value: str | None) -> str:
    """Normalize compatibility characters while preserving language and facts."""
    return " ".join(unicodedata.normalize("NFKC", value or "").split())


def detect_query_language_mode(value: str | None) -> str:
    """Return a content-free script mode suitable for private diagnostics."""
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    has_devanagari = bool(_DEVANAGARI_PATTERN.search(normalized))
    has_latin = bool(_LATIN_PATTERN.search(normalized))
    if has_devanagari and has_latin:
        return "mixed_devanagari_latin"
    if has_devanagari:
        return "devanagari"
    if has_latin:
        return "latin_or_romanized"
    return "unknown"
