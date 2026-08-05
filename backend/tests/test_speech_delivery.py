"""Tests for centralized, language-aware natural speech delivery."""

import unittest

from app.services.ai.exceptions import AIConfigurationError
from app.services.speech_delivery_resolver import (
    FEMALE_DELIVERY_INSTRUCTIONS,
    LANGUAGE_INSTRUCTIONS,
    MALE_DELIVERY_INSTRUCTIONS,
    SpeechDeliveryResolver,
    SpeechLanguageMode,
)
from app.services.voice_profile_resolver import StandardVoiceProfile


class SpeechDeliveryResolverTests(unittest.TestCase):
    def setUp(self):
        self.resolver = SpeechDeliveryResolver()

    def test_male_and_female_profiles_are_centralized_and_deterministic(self):
        male = self.resolver.resolve(StandardVoiceProfile.MALE, "Hello")
        female = self.resolver.resolve(StandardVoiceProfile.FEMALE, "Hello")
        self.assertTrue(male.instructions.startswith(MALE_DELIVERY_INSTRUCTIONS))
        self.assertTrue(female.instructions.startswith(FEMALE_DELIVERY_INSTRUCTIONS))
        self.assertNotEqual(male.instructions, female.instructions)
        self.assertEqual(
            male,
            self.resolver.resolve(StandardVoiceProfile.MALE, "Hello"),
        )

    def test_language_modes_use_deterministic_script_detection(self):
        cases = (
            ("Hello there", SpeechLanguageMode.ENGLISH),
            ("तू कसा आहेस?", SpeechLanguageMode.DEVANAGARI),
            ("आज market मध्ये जाऊया", SpeechLanguageMode.MIXED),
            ("東京", SpeechLanguageMode.MULTILINGUAL),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                delivery = self.resolver.resolve(StandardVoiceProfile.FEMALE, text)
                self.assertEqual(delivery.language_mode, expected)
                self.assertTrue(
                    delivery.instructions.endswith(LANGUAGE_INSTRUCTIONS[expected])
                )

    def test_invalid_voice_profile_fails_safely(self):
        with self.assertRaises(AIConfigurationError):
            self.resolver.resolve("cedar", "Hello")


if __name__ == "__main__":
    unittest.main()
