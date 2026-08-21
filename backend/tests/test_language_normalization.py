from app.services.ai.prompt_builder import MEMORY_EXTRACTION_SYSTEM_PROMPT
from app.services.language_normalization import LanguageNormalizationService
from app.services.ai.exceptions import (
    AIProviderError, AIProviderUnavailableError, AIRateLimitError, AITimeoutError,
)
import json
import asyncio


def test_personal_query_matrix_normalizes_to_shared_english_semantics():
    service = LanguageNormalizationService()
    dogs = (
        "What are our dogs' names?",
        "आपल्या कुत्र्यांची नावं काय आहेत?",
        "हमारे कुत्तों के नाम क्या हैं?",
        "tujhya kutryanchi nava kay ahet?",
        "hamare kutte ke naam kya hain?",
    )
    normalized = [service.normalize_user_turn(query) for query in dogs]
    assert {item.normalized_english_text for item in normalized} == {"What are our dogs' names?"}
    assert [item.detected_language for item in normalized] == [
        "english", "marathi", "hindi", "romanized_marathi", "romanized_hindi",
    ]


def test_tv_family_and_code_switched_queries_preserve_current_language_metadata():
    service = LanguageNormalizationService()
    cases = (
        ("आपला TV किती inch आहे?", "What size is our TV?", "mixed_marathi_english"),
        ("tumhare husband ka naam kya hai?", "What is your husband's name?", "mixed_hindi_english"),
        ("What about aplya dogs?", "Tell me about our dogs.", "mixed_marathi_english"),
        ("तुझा नवरा कोण आहे?", "Who is your husband?", "marathi"),
    )
    for original, semantic, language in cases:
        turn = service.normalize_user_turn(original)
        assert turn.original_text == original
        assert turn.normalized_english_text == semantic
        assert turn.detected_language == language
        assert turn.normalization_success


def test_marathi_tv_script_variants_keep_stage_one_subject_when_stage_two_drifts():
    bad = {
        "detected_language": "marathi", "response_language": "marathi",
        "normalized_english": "Tell me about our dogs.", "code_switched": False,
        "speech_act": "question", "substantive_intent": "personal_memory",
        "translation_confidence": 0.91,
    }
    for original in ("तुझ्याकडे टीव्ही आहे का?", "टीव्ही, टीव्ही आहे का?"):
        turn = asyncio.run(LanguageNormalizationService(
            SemanticAI([json.dumps(bad)])
        ).normalize_semantically(original))
        assert turn.normalized_english_text == "What about our TV?"
        assert turn.response_language == "marathi"


def test_names_measurements_and_source_wording_are_never_rewritten():
    service = LanguageNormalizationService()
    for name in ("Mohan Deshmukh", "Jean-Luc O'Neill", "Élodie D’Arcy", "Bruno", "Luffy"):
        original = f"tumhare husband ka naam {name} hai?"
        turn = service.normalize_user_turn(original)
        assert turn.original_text == original
    tv = service.normalize_user_turn("आपला TV 85 inch चा आहे?")
    assert "85" in tv.original_text
    assert "85" not in tv.normalized_english_text or tv.normalized_english_text == "What size is our TV?"
    assert "canonical memory titles, summaries, details" in MEMORY_EXTRACTION_SYSTEM_PROMPT
    assert "Evidence excerpts must remain" in MEMORY_EXTRACTION_SYSTEM_PROMPT
    assert "original language" in MEMORY_EXTRACTION_SYSTEM_PROMPT


def test_cross_language_followup_and_explicit_switch_have_stable_semantics():
    service = LanguageNormalizationService()
    assert service.normalize_user_turn("Tell me about your brother.").normalized_english_text == "Tell me about your brother."
    assert service.normalize_user_turn("आणखी काय आठवतं त्याच्याबद्दल?").normalized_english_text == "What else do you remember about him?"
    assert service.normalize_user_turn("What about our TV?").normalized_english_text == "What about our TV?"


def test_short_neutral_acknowledgements_inherit_active_conversation_language():
    service = LanguageNormalizationService()
    assert service.normalize_user_turn("Okay", "marathi").response_language == "marathi"
    assert service.normalize_user_turn("Hmm", "hindi").response_language == "hindi"
    assert service.normalize_user_turn("Okay", "english").response_language == "english"
    assert service.normalize_user_turn("What is a mango?", "marathi").response_language == "english"


class SemanticAI:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    async def generate_response(self, _messages, **options):
        self.calls += 1
        schema = options["structured_response_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert "name" not in schema and "strict" not in schema and "schema" not in schema
        assert set(schema["required"]) == set(schema["properties"])
        return self.outputs.pop(0)


def test_real_mixed_greeting_uses_structured_semantics_and_substantive_intent():
    payload = {
        "detected_language": "mixed_marathi_english", "response_language": "marathi",
        "normalized_english": "What is your name?", "code_switched": True,
        "speech_act": "question", "substantive_intent": "personal_identity",
        "translation_confidence": 0.97,
    }
    ai = SemanticAI([json.dumps(payload)])
    turn = asyncio.run(LanguageNormalizationService(ai).normalize_semantically(
        "Hi, तुझं नाव काय आहे?"
    ))
    assert ai.calls == 1
    assert turn.normalized_english_text == "What is your name?"
    assert turn.response_language == "marathi"
    assert turn.substantive_intent == "personal_identity"
    assert turn.stage_used == "semantic_model"


def test_semantic_failure_retries_once_then_keeps_safe_original_fallback():
    ai = SemanticAI(["not-json", "also-not-json"])
    original = "दुसरा नाव काय आहे?"
    turn = asyncio.run(LanguageNormalizationService(ai).normalize_semantically(original))
    assert ai.calls == 2
    assert turn.original_text == original
    assert turn.fallback_used
    assert turn.stage_used == "fallback"


def test_noisy_latin_asr_is_not_allowed_onto_clear_english_fast_path():
    payload = {
        "detected_language": "romanized_marathi", "response_language": "marathi",
        "normalized_english": "Who are you?", "code_switched": False,
        "speech_act": "question", "substantive_intent": "personal_identity",
        "translation_confidence": 0.72,
    }
    for original in (
        "mala sang tu kon ahes", "malasang tu kon ahe",
        "mala sang tu kon ahes na", "Malasang tu jornal KR",
    ):
        ai = SemanticAI([json.dumps(payload)])
        turn = asyncio.run(LanguageNormalizationService(ai).normalize_semantically(
            original, "marathi",
        ))
        assert ai.calls == 1
        assert turn.stage_used == "semantic_model"
        assert turn.response_language == "marathi"


class FailingSemanticAI:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    async def generate_response(self, _messages, **_options):
        self.calls += 1
        raise self.error


def test_expected_provider_failures_are_non_fatal_and_preserve_language_context():
    failures = (
        AIProviderError("invalid_json_schema"), AIRateLimitError("rate limited"),
        AITimeoutError("timeout"), AIProviderUnavailableError("unavailable"),
    )
    for error in failures:
        ai = FailingSemanticAI(error)
        original = "malasang tu kon ahe"
        turn = asyncio.run(LanguageNormalizationService(ai).normalize_semantically(
            original, "marathi",
        ))
        assert ai.calls == 2
        assert turn.original_text == original
        assert turn.normalized_english_text == original
        assert turn.response_language == "marathi"
        assert turn.stage_used == "fallback"
        assert not turn.normalization_success
