"""Deterministic tests for provider-neutral Legacy voice profiles."""

import unittest

from app.services.ai.exceptions import AIConfigurationError
from app.services.voice_profile_resolver import (
    FEMALE_RELATIONSHIPS,
    MALE_RELATIONSHIPS,
    StandardVoiceProfile,
    StandardVoiceResolver,
)


class StandardVoiceResolverTests(unittest.TestCase):
    def test_every_supported_male_relationship_resolves_to_male(self):
        resolver = StandardVoiceResolver("standard_female")
        for relationship in MALE_RELATIONSHIPS:
            with self.subTest(relationship=relationship):
                self.assertEqual(
                    resolver.resolve(relationship),
                    StandardVoiceProfile.MALE,
                )

    def test_every_supported_female_relationship_resolves_to_female(self):
        resolver = StandardVoiceResolver("standard_male")
        for relationship in FEMALE_RELATIONSHIPS:
            with self.subTest(relationship=relationship):
                self.assertEqual(
                    resolver.resolve(relationship),
                    StandardVoiceProfile.FEMALE,
                )

    def test_case_and_surrounding_whitespace_are_normalized(self):
        resolver = StandardVoiceResolver("standard_female")
        self.assertEqual(
            resolver.resolve("  GRANDMOTHER  "),
            StandardVoiceProfile.FEMALE,
        )

    def test_ambiguous_unknown_and_missing_values_use_fallback(self):
        resolver = StandardVoiceResolver("standard_male")
        for relationship in ("Friend", "Partner", "Unknown", "", None):
            with self.subTest(relationship=relationship):
                self.assertEqual(
                    resolver.resolve(relationship),
                    StandardVoiceProfile.MALE,
                )

    def test_invalid_configured_fallback_is_rejected(self):
        with self.assertRaises(AIConfigurationError):
            StandardVoiceResolver("marin")


if __name__ == "__main__":
    unittest.main()
