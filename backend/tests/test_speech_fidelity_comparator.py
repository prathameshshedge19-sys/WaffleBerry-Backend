"""Tests for strict semantic transcript fidelity comparison."""

import unittest
import unicodedata

from app.services.speech_fidelity_comparator import SpeechFidelityComparator


class SpeechFidelityComparatorTests(unittest.TestCase):
    def setUp(self):
        self.equivalent = SpeechFidelityComparator().equivalent

    def test_accepts_only_harmless_presentation_differences(self):
        cases = (
            ("मला तो दिवस आठवतो।", "मला   तो दिवस आठवतो."),
            ("I’m here, Prathamesh!", "i'm here Prathamesh"),
            ("Café Pune", unicodedata.normalize("NFD", "café Pune")),
            ("मुंबई॥ पुणे।", "मुंबई. पुणे!"),
        )
        for expected, actual in cases:
            with self.subTest(expected=expected, actual=actual):
                self.assertTrue(self.equivalent(expected, actual))

    def test_rejects_semantic_changes(self):
        cases = (
            ("मला तो दिवस आठवतो", "मला दिवस आठवतो"),
            ("मला तो दिवस आठवतो", "मला तो दिवस खूप आठवतो"),
            ("मला पुणे आठवते", "मुझे पुणे याद है"),
            ("Prathamesh went to Mumbai", "Prakash went to Mumbai"),
            ("Prathamesh went to Mumbai", "Prathamesh went to Pune"),
            ("14 July 2019", "14 July 2020"),
            ("₹500 at 7:30", "₹500 at 8:30"),
            ("₹500", "$500"),
            ("25%", "25"),
            ("मला तो दिवस आठवतो", "I remember that day"),
        )
        for expected, actual in cases:
            with self.subTest(expected=expected, actual=actual):
                self.assertFalse(self.equivalent(expected, actual))


if __name__ == "__main__":
    unittest.main()
