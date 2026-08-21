"""Shared, privacy-safe language normalization for Chat and Live Call semantics."""

from dataclasses import dataclass, replace
import json
import re
import unicodedata

from app.services.ai.ai_service import AIService
from app.services.ai.exceptions import AIServiceError
from app.services.ai.provider import AIMessage
from app.services.speech_language_analyzer import SpeechLanguageAnalyzer, SpeechLanguageMode


@dataclass(frozen=True, slots=True)
class NormalizedTurn:
    original_text: str
    detected_language: str
    normalized_english_text: str
    language_confidence: float
    script: str
    code_switching: bool
    translation_required: bool
    normalization_success: bool
    fallback_used: bool
    response_language: str = "english"
    speech_act: str = "statement"
    substantive_intent: str = "unknown"
    stage_used: str = "deterministic"


_NORMALIZATION_SCHEMA = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "detected_language": {"type": "string", "enum": ["marathi", "hindi", "romanized_marathi", "romanized_hindi", "mixed_marathi_english", "mixed_hindi_english", "multilingual_unknown"]},
            "response_language": {"type": "string", "enum": ["marathi", "hindi", "english"]},
            "normalized_english": {"type": "string"},
            "code_switched": {"type": "boolean"},
            "speech_act": {"type": "string", "enum": ["question", "statement", "acknowledgement", "farewell", "correction"]},
            "substantive_intent": {"type": "string", "enum": ["social", "general_knowledge", "personal_identity", "personal_family", "personal_memory", "referential_followup", "unknown"]},
            "translation_confidence": {"type": "number"},
        },
        "required": ["detected_language", "response_language", "normalized_english", "code_switched", "speech_act", "substantive_intent", "translation_confidence"],
}


class LanguageNormalizationService:
    """Normalize supported personal-query semantics without altering source text."""

    _DEVANAGARI = re.compile(r"[\u0900-\u097f]")
    _LATIN = re.compile(r"[A-Za-z]")
    _ROMAN_MR = frozenset({
        "aahe", "ahet", "tujha", "tujhi", "tumcha", "apla", "aplya", "amcha",
        "navra", "kutra", "kutryanchi", "nav", "nava", "kiti", "kay", "kon", "ajun",
    })
    _ROMAN_HI = frozenset({
        "hai", "hain", "tumhara", "tumhare", "hamara", "pati", "kutte", "naam",
        "kya", "kaun", "kitna", "aur", "bhai",
    })

    def __init__(self, ai_service: AIService | None = None) -> None:
        self._speech = SpeechLanguageAnalyzer()
        self._ai = ai_service

    async def normalize_semantically(
        self, text: str, recent_language_context: str | None = None,
    ) -> NormalizedTurn:
        stage_one = self.normalize_user_turn(text, recent_language_context)
        needs_semantic = not self._is_confidently_clear_english(stage_one)
        if not needs_semantic:
            return stage_one
        if self._ai is None:
            return stage_one
        instruction = (
            "Interpret one user turn, including noisy multilingual speech-to-text and corrupted spacing or "
            "spelling when the meaning is reasonably supported. Recent language context is only a weak hint; "
            "a clear current language wins. Do not answer it and do not infer biography. Remove greeting/filler "
            "when a substantive proposition follows. Translate only its meaning into concise English. "
            "Preserve proper nouns, brands, model names, numbers, and units exactly. response_language is "
            "the language of the current substantive turn. If meaning is unclear, preserve the original wording "
            "as normalized_english and use low confidence rather than inventing meaning. Return only the strict structure."
        )
        context_hint = (
            f" Recent language context: {recent_language_context}."
            if recent_language_context in {"marathi", "hindi", "english"} else ""
        )
        for attempt in range(2):
            try:
                raw = await self._ai.generate_response((
                    AIMessage(role="system", content=("REPAIR invalid structure. " if attempt else "") + instruction + context_hint),
                    AIMessage(role="user", content=stage_one.original_text),
                ), structured_response_schema=_NORMALIZATION_SCHEMA)
                payload = json.loads(raw)
                normalized_english = payload["normalized_english"].strip()
                confidence = float(payload["translation_confidence"])
                if not normalized_english or len(normalized_english) > 500 or not 0 <= confidence <= 1:
                    raise ValueError("Semantic normalization values are outside the bounded contract.")
                normalized_english = self._preserve_recognized_subject(
                    stage_one.normalized_english_text, normalized_english,
                )
                return NormalizedTurn(
                    original_text=stage_one.original_text,
                    detected_language=payload["detected_language"],
                    normalized_english_text=normalized_english,
                    language_confidence=confidence,
                    script=stage_one.script, code_switching=bool(payload["code_switched"]),
                    translation_required=True, normalization_success=True, fallback_used=False,
                    response_language=payload["response_language"], speech_act=payload["speech_act"],
                    substantive_intent=payload["substantive_intent"], stage_used="semantic_model",
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError, AIServiceError):
                continue
        return replace(
            stage_one, fallback_used=True, stage_used="fallback",
            normalization_success=False,
            response_language=self._fallback_response_language(stage_one, recent_language_context),
        )

    @staticmethod
    def _preserve_recognized_subject(
        deterministic_english: str, semantic_english: str,
    ) -> str:
        """Do not let stage two discard an explicit subject already recognized by stage one."""
        from app.services.turn_understanding import interpret_turn

        deterministic = interpret_turn(deterministic_english)
        semantic = interpret_turn(semantic_english)
        if (deterministic.explicit_subjects
                and deterministic.resolved_topic != semantic.resolved_topic):
            return deterministic_english
        return semantic_english

    @staticmethod
    def _is_confidently_clear_english(turn: NormalizedTurn) -> bool:
        """Only ordinary, confidently parsed English may bypass semantic normalization."""
        if turn.detected_language != "english" or turn.script != "latin":
            return False
        tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", turn.original_text.casefold())
        if not tokens:
            return False
        common = {
            "a", "about", "and", "are", "bye", "can", "do", "family", "hello", "hi",
            "hmm", "how", "i", "is", "it", "mango", "me", "my", "name", "no", "of", "ok",
            "okay", "our", "please", "right", "tell", "thank", "thanks", "the", "them",
            "they", "this", "to", "yes",
            "tv", "what", "who", "why", "you", "your",
        }
        known = sum(token in common for token in tokens)
        return known / len(tokens) >= 0.6

    @staticmethod
    def _fallback_response_language(
        turn: NormalizedTurn, recent_language_context: str | None,
    ) -> str:
        if turn.response_language in {"marathi", "hindi"}:
            return turn.response_language
        if recent_language_context in {"marathi", "hindi"} and (
            turn.detected_language == "multilingual_unknown"
            or not LanguageNormalizationService._is_confidently_clear_english(turn)
        ):
            return recent_language_context
        return "english"

    def normalize_user_turn(
        self, text: str, recent_language_context: str | None = None,
    ) -> NormalizedTurn:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Language normalization requires substantive text.")
        original = " ".join(unicodedata.normalize("NFC", text).split())
        mode = self._detect(original, recent_language_context)
        semantic = self._semantic_english(original, mode)
        translated = semantic != original
        neutral_acknowledgement = bool(re.fullmatch(
            r"(?:ok(?:ay)?|hmm+|yes|no|right)[.!?]*", original, re.IGNORECASE,
        ))
        response_language = (
            recent_language_context
            if neutral_acknowledgement and recent_language_context in {"marathi", "hindi", "english"}
            else "marathi" if "marathi" in mode else "hindi" if "hindi" in mode else "english"
        )
        return NormalizedTurn(
            original_text=original, detected_language=mode,
            normalized_english_text=semantic, language_confidence=0.95 if translated else 0.9,
            script="devanagari" if self._DEVANAGARI.search(original) else "latin",
            code_switching=bool(self._DEVANAGARI.search(original) and self._LATIN.search(original))
            or mode.startswith("mixed_"),
            translation_required=mode != "english", normalization_success=True,
            fallback_used=mode != "english" and not translated,
            response_language=response_language,
            speech_act="question" if "?" in original else "statement",
            substantive_intent="unknown", stage_used="deterministic",
        )

    def _detect(self, text: str, recent: str | None) -> str:
        tokens = set(re.findall(r"[A-Za-z]+", text.casefold()))
        mr = len(tokens & self._ROMAN_MR)
        hi = len(tokens & self._ROMAN_HI)
        devanagari = bool(self._DEVANAGARI.search(text))
        latin = bool(self._LATIN.search(text))
        if not devanagari:
            if mr >= 1 and self._english_content(tokens) and mr >= hi:
                return "mixed_marathi_english"
            if hi >= 1 and self._english_content(tokens) and hi > mr:
                return "mixed_hindi_english"
            if mr >= 2 and mr > hi:
                return "mixed_marathi_english" if self._english_content(tokens) else "romanized_marathi"
            if hi >= 2 and hi > mr:
                return "mixed_hindi_english" if self._english_content(tokens) else "romanized_hindi"
        try:
            mode = self._speech.detect(text)
        except ValueError:
            mode = SpeechLanguageMode.MULTILINGUAL_UNKNOWN
        if mode == SpeechLanguageMode.DEVANAGARI_UNKNOWN and recent in {"marathi", "hindi"}:
            return recent
        if mode == SpeechLanguageMode.DEVANAGARI_UNKNOWN:
            # Distinct copulas are reliable even in very short questions.
            if any(token in text for token in ("आहे", "आहेत", "किती", "तुझ", "आपल", "आमच")):
                return "mixed_marathi_english" if latin else "marathi"
            if any(token in text for token in ("है", "हैं", "क्या", "क्यों", "तुम्हार", "हमार")):
                return "mixed_hindi_english" if latin else "hindi"
        mapping = {
            SpeechLanguageMode.ENGLISH: "english",
            SpeechLanguageMode.MARATHI_DEVANAGARI: "marathi",
            SpeechLanguageMode.HINDI_DEVANAGARI: "hindi",
            SpeechLanguageMode.ROMANIZED_MARATHI: "romanized_marathi",
            SpeechLanguageMode.MIXED_MARATHI_ENGLISH: "mixed_marathi_english",
            SpeechLanguageMode.MIXED_HINDI_ENGLISH: "mixed_hindi_english",
        }
        return mapping.get(mode, "multilingual_unknown")

    @staticmethod
    def _english_content(tokens: set[str]) -> bool:
        return bool(tokens & {"what", "about", "dogs", "dog", "tv", "husband", "name", "size"})

    @staticmethod
    def _semantic_english(text: str, mode: str) -> str:
        if mode == "english":
            return text
        lower = text.casefold()
        concepts = {
            "dogs": bool(re.search(r"dogs?|कुत्र|कुत्त|kutr|kutte", lower)),
            "tv": bool(re.search(r"\btv\b|टीव्ही", lower)),
            "husband": bool(re.search(r"husband|नवरा|पति|navra|pati", lower)),
            "brother": bool(re.search(r"brother|भाऊ|भाई|bhau|bhai", lower)),
            "family": bool(re.search(r"family|कुटुंब|परिवार|kutumb|parivar", lower)),
            "name": bool(re.search(r"name|names|नाव|नाम|nav|naam", lower)),
            "size": bool(re.search(r"size|साईज|आकार|inch|इंच|kiti|kitna|किती|कितना", lower)),
            "else": bool(re.search(r"what else|आणखी|और क्या|ajun|aur kya", lower)),
        }
        possessive = bool(re.search(
            r"our|your|आपल|आमच|तुझ|तुम्हार|हमार|aplya|apla|amcha|tujha|tumhar|hamar", lower,
        ))
        if concepts["else"]:
            return "What else do you remember about him?"
        if concepts["tv"]:
            return "What size is our TV?" if concepts["size"] else "What about our TV?"
        if concepts["dogs"]:
            if concepts["name"]:
                return "What are our dogs' names?"
            return "Tell me about our dogs." if possessive else "What are dogs?"
        if concepts["husband"]:
            return "What is your husband's name?" if concepts["name"] else "Who is your husband?"
        if concepts["brother"]:
            return "What is your brother's name?" if concepts["name"] else "Tell me about your brother."
        if concepts["family"]:
            return "Who is in your family?"
        return text
