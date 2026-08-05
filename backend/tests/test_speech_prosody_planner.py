"""Punctuation-only prosody and rendering-integrity tests."""

import unittest

from app.services.speech_emotion_analyzer import (
    EmotionConfidence,
    SpeechEmotion,
    SpeechEmotionAnalysis,
)
from app.services.speech_language_analyzer import SpeechLanguageMode
from app.services.speech_prosody_planner import (
    EMOTION_PROFILES,
    SpeechProsodyPlanner,
    SpeechRenderingIntegrityComparator,
)


def analysis(emotion):
    return SpeechEmotionAnalysis(emotion, EmotionConfidence.MEDIUM)


class SpeechEmotionProfileTests(unittest.TestCase):
    def test_every_emotion_has_one_safe_bulbul_v3_profile(self):
        self.assertEqual(set(EMOTION_PROFILES), set(SpeechEmotion))
        for emotion, profile in EMOTION_PROFILES.items():
            with self.subTest(emotion=emotion):
                self.assertGreaterEqual(profile.pace, 0.5)
                self.assertLessEqual(profile.pace, 2.0)
                self.assertGreaterEqual(profile.temperature, 0.01)
                self.assertLessEqual(profile.temperature, 1.0)
                self.assertFalse(profile.allow_nonverbal_cues)
        self.assertLessEqual(EMOTION_PROFILES[SpeechEmotion.EXCITED].pace, 1.1)
        self.assertGreaterEqual(EMOTION_PROFILES[SpeechEmotion.SAD].pace, 0.8)
        self.assertLessEqual(EMOTION_PROFILES[SpeechEmotion.ANGRY].temperature, 0.7)


class SpeechProsodyPlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = SpeechProsodyPlanner()

    def plan(self, text, emotion=SpeechEmotion.NEUTRAL, mode=SpeechLanguageMode.ENGLISH):
        return self.planner.plan(
            canonical_text=text,
            language_mode=mode,
            analysis=analysis(emotion),
            enabled=True,
        )

    def test_repeated_punctuation_and_dashes_are_controlled(self):
        plan = self.plan("Wonderful!!! Really??? Yes — truly!", SpeechEmotion.JOYFUL)
        self.assertEqual(plan.provider_text, "Wonderful! Really? Yes, truly!")
        self.assertNotIn("!!", plan.provider_text)
        self.assertNotIn("??", plan.provider_text)

    def test_reflective_mode_uses_at_most_one_ellipsis(self):
        plan = self.plan("I remember those days. We sat together. It was quiet.", SpeechEmotion.NOSTALGIC)
        self.assertEqual(plan.provider_text.count("..."), 1)
        self.assertEqual(plan.canonical_text, "I remember those days. We sat together. It was quiet.")

    def test_run_on_gets_safe_boundary_and_words_remain_ordered(self):
        text = "This was a long afternoon " + "with everyone together " * 8 + ", and we finally returned home."
        plan = self.plan(text, SpeechEmotion.WARM)
        self.assertIn(". and we finally", plan.provider_text)
        self.assertTrue(SpeechRenderingIntegrityComparator().is_safe(text, plan.provider_text))

    def test_paragraphs_questions_commas_and_danda_remain_valid(self):
        samples = (
            "First paragraph.\n\nSecond paragraph.",
            "Are you ready? Yes, I am.",
            "मला आठवतं। आपण तिथे बसलो होतो।",
        )
        for text in samples:
            with self.subTest(text=text):
                plan = self.plan(text, SpeechEmotion.WARM, SpeechLanguageMode.MARATHI_DEVANAGARI)
                self.assertTrue(SpeechRenderingIntegrityComparator().is_safe(text, plan.provider_text))
                if "?" in text:
                    self.assertIn("?", plan.provider_text)
                if "।" in text:
                    self.assertEqual(plan.provider_text.count("।"), text.count("।"))

    def test_names_places_dates_numbers_currency_and_dictionary_terms_unchanged(self):
        text = "Prathamesh went from Dombivli to Pune on 14 July 2019 with ₹500 for WaffleBerry."
        plan = self.plan(text, SpeechEmotion.NOSTALGIC)
        for token in ("Prathamesh", "Dombivli", "Pune", "14", "2019", "₹500", "WaffleBerry"):
            self.assertIn(token, plan.provider_text)
        self.assertTrue(SpeechRenderingIntegrityComparator().is_safe(text, plan.provider_text))

    def test_unsafe_transformation_falls_back_to_canonical_and_neutral(self):
        class RejectAll:
            def is_safe(self, canonical_text, provider_text):
                return False
        planner = SpeechProsodyPlanner(RejectAll())
        result = planner.plan(
            canonical_text="Original words!!!",
            language_mode=SpeechLanguageMode.ENGLISH,
            analysis=analysis(SpeechEmotion.EXCITED),
            enabled=True,
        )
        self.assertEqual(result.provider_text, "Original words!!!")
        self.assertEqual(result.emotion, SpeechEmotion.NEUTRAL)
        self.assertFalse(result.prosody_shaping_applied)

    def test_disabled_mode_is_neutral_and_does_not_shape(self):
        result = self.planner.plan(
            canonical_text="Hello!!!",
            language_mode=SpeechLanguageMode.ENGLISH,
            analysis=analysis(SpeechEmotion.EXCITED),
            enabled=False,
        )
        self.assertEqual(result.provider_text, "Hello!!!")
        self.assertEqual(result.emotion, SpeechEmotion.NEUTRAL)


if __name__ == "__main__":
    unittest.main()
