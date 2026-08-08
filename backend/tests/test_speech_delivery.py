"""Tests for centralized language-aware natural speech delivery."""

import unittest

from app.services.ai.exceptions import AIConfigurationError
from app.services.speech_delivery_resolver import (
    FEMALE_DELIVERY_INSTRUCTIONS,
    FINAL_FIDELITY_INSTRUCTIONS,
    LANGUAGE_INSTRUCTIONS,
    MALE_DELIVERY_INSTRUCTIONS,
    SpeechDeliveryResolver,
)
from app.services.speech_language_analyzer import SpeechLanguageMode
from app.services.voice_profile_resolver import StandardVoiceProfile


class SpeechDeliveryResolverTests(unittest.TestCase):
    def setUp(self):
        self.resolver = SpeechDeliveryResolver()

    def test_male_and_female_warmth_profiles_remain_present(self):
        male = self.resolver.resolve(StandardVoiceProfile.MALE, "Hello")
        female = self.resolver.resolve(StandardVoiceProfile.FEMALE, "Hello")
        self.assertIn(MALE_DELIVERY_INSTRUCTIONS, male.instructions)
        self.assertIn(FEMALE_DELIVERY_INSTRUCTIONS, female.instructions)
        self.assertNotEqual(male.instructions, female.instructions)

    def test_each_language_mode_uses_its_centralized_guidance(self):
        cases = (
            (
                "दादर खूप सुंदर आहे आणि मला तिथे जायचं आहे.",
                SpeechLanguageMode.MARATHI_DEVANAGARI,
                ("conversational Marathi", "Do not use Hindi rhythm"),
            ),
            (
                "मुझे वह शाम याद है और वहाँ बहुत लोग थे।",
                SpeechLanguageMode.HINDI_DEVANAGARI,
                ("conversational Hindi", "native Hindi"),
            ),
            (
                "आज सुंदर दिवस आहे",
                SpeechLanguageMode.DEVANAGARI_UNKNOWN,
                ("Do not assume all Devanagari text is Hindi",),
            ),
            (
                "Mala ajunhi athavta, apan sandhyakali khup gappa maraycho.",
                SpeechLanguageMode.ROMANIZED_MARATHI,
                ("conversational Marathi", "not ordinary English"),
            ),
            (
                "Dadar khup lively aahe, especially evening la, when the market is full.",
                SpeechLanguageMode.MIXED_MARATHI_ENGLISH,
                ("Marathi-English code-switching",),
            ),
            (
                "Mumbai evening bahut lively hoti hai, especially when market is full.",
                SpeechLanguageMode.MIXED_HINDI_ENGLISH,
                ("Hindi-English code-switching",),
            ),
        )
        for text, expected, phrases in cases:
            with self.subTest(mode=expected):
                delivery = self.resolver.resolve(StandardVoiceProfile.FEMALE, text)
                self.assertEqual(delivery.language_mode, expected)
                self.assertTrue(
                    delivery.instructions.startswith(LANGUAGE_INSTRUCTIONS[expected])
                )
                self.assertTrue(delivery.instructions.endswith(FINAL_FIDELITY_INSTRUCTIONS))
                for phrase in phrases:
                    self.assertIn(phrase, delivery.instructions)

    def test_business_logic_contains_no_provider_names(self):
        for instructions in LANGUAGE_INSTRUCTIONS.values():
            lowered = instructions.lower()
            self.assertNotIn("openai", lowered)
            self.assertNotIn("realtime", lowered)
            self.assertNotIn("cedar", lowered)
            self.assertNotIn("marin", lowered)

    def test_invalid_voice_profile_fails_safely(self):
        with self.assertRaises(AIConfigurationError):
            self.resolver.resolve("cedar", "Hello")


if __name__ == "__main__":
    unittest.main()
