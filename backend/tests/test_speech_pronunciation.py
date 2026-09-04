from app.services.speech_pronunciation import normalize_for_companion_speech


def test_standalone_companion_name_is_pronunciation_safe_for_tts():
    assert normalize_for_companion_speech("Rya") == "Riya"
    assert normalize_for_companion_speech("I'm Rya.") == "I'm Riya."
    assert normalize_for_companion_speech("Talk with Rya") == "Talk with Riya"


def test_normalization_does_not_touch_unrelated_names_or_embedded_text():
    source = "Arya and Priya talk about Pallavi, Prathamesh, and LegaRya."
    assert normalize_for_companion_speech(source) == source


def test_full_brand_pronunciation_is_explicit_and_tts_only():
    assert normalize_for_companion_speech("Welcome to LegaRya.", pronounce_full_brand=True) == "Welcome to LegaRiya."
    assert normalize_for_companion_speech("Welcome to LegaRya.") == "Welcome to LegaRya."
