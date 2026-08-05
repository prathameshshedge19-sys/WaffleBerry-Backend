"""Conservative provider-independent language analysis for speech delivery."""

from enum import Enum
import re
import unicodedata


class SpeechLanguageMode(str, Enum):
    ENGLISH = "english"
    MARATHI_DEVANAGARI = "marathi_devanagari"
    HINDI_DEVANAGARI = "hindi_devanagari"
    DEVANAGARI_UNKNOWN = "devanagari_unknown"
    ROMANIZED_MARATHI = "romanized_marathi"
    MIXED_MARATHI_ENGLISH = "mixed_marathi_english"
    MIXED_HINDI_ENGLISH = "mixed_hindi_english"
    MULTILINGUAL_UNKNOWN = "multilingual_unknown"


class SpeechLanguageAnalyzer:
    """Classify only when multiple token-level indicators provide evidence."""

    _DEVANAGARI_TOKEN = re.compile(r"[\u0900-\u097f]+")
    _LATIN_TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")

    _MARATHI = frozenset(
        {
            "आहे", "आहेत", "होता", "होती", "होते", "मला", "तुला",
            "माझा", "माझी", "माझे", "तुझा", "तुझी", "खूप", "आणि",
            "तिथे", "इथे", "मध्ये", "साठी", "करायचो", "करायची",
            "बसलो", "बसून", "आठवतं", "आठवते", "गेलो", "गेली", "झालं",
            "नाही", "आपण", "गप्पा", "मारायचो", "वाजता", "हून",
            "गेला", "रोजी", "सकाळी",
        }
    )
    _HINDI = frozenset(
        {
            "है", "हैं", "था", "थी", "थे", "मुझे", "तुम्हें", "मेरा",
            "मेरी", "मेरे", "बहुत", "और", "वहाँ", "यहाँ", "में", "लिए",
            "करते", "बैठकर", "याद", "गया", "गई", "हुआ", "नहीं", "हम",
            "वह", "की", "को", "बातें",
        }
    )
    _ROMANIZED_MARATHI = frozenset(
        {
            "aahe", "ahet", "khup", "mala", "tula", "majha", "majhi",
            "majhe", "tujha", "tujhi", "ani", "tithe", "ithe", "nahi",
            "hota", "hoti", "hote", "kela", "keli", "gela", "geli",
            "athavta", "athavte", "apan", "aapan", "sandhyakali", "gappa",
            "maraycho", "vajta", "hun", "sakali", "basun", "gacchivar",
            "ajunhi", "majhyasathi", "la",
        }
    )
    _ROMANIZED_HINDI = frozenset(
        {
            "hai", "hain", "tha", "thi", "the", "mujhe", "tumhe", "mera",
            "meri", "mere", "bahut", "aur", "wahan", "yahan", "mein",
            "liye", "karte", "baithkar", "yaad", "gaya", "gayi", "hua",
            "nahi", "jab", "hota", "hoti", "hum", "woh", "baatein",
        }
    )
    _COMMON_ENGLISH = frozenset(
        {
            "a", "an", "and", "are", "as", "at", "be", "especially",
            "evening", "for", "from", "full", "in", "is", "it", "lively",
            "market", "of", "people", "the", "this", "to", "was", "when",
            "with", "you", "your", "remember", "today", "still",
        }
    )

    def detect(self, text: str) -> SpeechLanguageMode:
        if not isinstance(text, str):
            raise ValueError("Speech language input must be a string.")
        normalized = unicodedata.normalize("NFC", text)
        if not normalized.strip():
            raise ValueError("Speech language input must not be blank.")

        devanagari = [
            token
            for token in self._DEVANAGARI_TOKEN.findall(normalized)
            if any(unicodedata.category(character).startswith("L") for character in token)
        ]
        latin = [token.casefold() for token in self._LATIN_TOKEN.findall(normalized)]
        if devanagari:
            marathi = self._score(devanagari, self._MARATHI)
            hindi = self._score(devanagari, self._HINDI)
            language = self._confident_language(marathi, hindi)
            has_substantial_latin = len(latin) >= 2
            if language == "marathi":
                return (
                    SpeechLanguageMode.MIXED_MARATHI_ENGLISH
                    if has_substantial_latin
                    else SpeechLanguageMode.MARATHI_DEVANAGARI
                )
            if language == "hindi":
                return (
                    SpeechLanguageMode.MIXED_HINDI_ENGLISH
                    if has_substantial_latin
                    else SpeechLanguageMode.HINDI_DEVANAGARI
                )
            return SpeechLanguageMode.DEVANAGARI_UNKNOWN

        if latin:
            marathi = self._score(latin, self._ROMANIZED_MARATHI)
            hindi = self._score(latin, self._ROMANIZED_HINDI)
            english = self._score(latin, self._COMMON_ENGLISH)
            if marathi >= 3 and marathi >= hindi + 1:
                return (
                    SpeechLanguageMode.MIXED_MARATHI_ENGLISH
                    if english >= 2
                    else SpeechLanguageMode.ROMANIZED_MARATHI
                )
            if hindi >= 3 and hindi >= marathi + 1 and english >= 2:
                return SpeechLanguageMode.MIXED_HINDI_ENGLISH
            if english >= 1 or all(token.isascii() for token in latin):
                return SpeechLanguageMode.ENGLISH
        return SpeechLanguageMode.MULTILINGUAL_UNKNOWN

    @staticmethod
    def _score(tokens: list[str], indicators: frozenset[str]) -> int:
        return sum(token in indicators for token in tokens)

    @staticmethod
    def _confident_language(marathi: int, hindi: int) -> str | None:
        if marathi >= 2 and marathi >= hindi + 1:
            return "marathi"
        if hindi >= 2 and hindi >= marathi + 1:
            return "hindi"
        return None
