"""Conservative multilingual emotion analyzer tests."""

import unittest

from app.services.speech_emotion_analyzer import (
    EmotionConfidence,
    SpeechEmotion,
    SpeechEmotionAnalyzer,
)
from app.services.speech_language_analyzer import SpeechLanguageMode


class SpeechEmotionAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = SpeechEmotionAnalyzer()

    def emotion(self, text, mode=SpeechLanguageMode.ENGLISH):
        return self.analyzer.analyze(text, language_mode=mode).emotion

    def test_all_supported_english_modes(self):
        cases = (
            ("The train leaves at nine tomorrow.", SpeechEmotion.NEUTRAL),
            ("Hello, welcome back, dear friend.", SpeechEmotion.WARM),
            ("I know this is hard. I'm here, and it will be okay.", SpeechEmotion.REASSURING),
            ("Breathe slowly and rest quietly for a moment.", SpeechEmotion.PEACEFUL),
            ("I remember those days and our old memories.", SpeechEmotion.NOSTALGIC),
            ("I miss you. It hurts to feel so lonely.", SpeechEmotion.SAD),
            ("Congratulations! I am so happy and proud of you.", SpeechEmotion.JOYFUL),
            ("This is so exciting! I can't wait.", SpeechEmotion.EXCITED),
            ("This is important. We need to decide carefully.", SpeechEmotion.SERIOUS),
            ("This is unacceptable. I am angry, but I will stay calm.", SpeechEmotion.ANGRY),
        )
        for text, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.emotion(text), expected)

    def test_reassurance_overrides_sadness_when_comfort_is_explicit(self):
        analysis = self.analyzer.analyze(
            "I know you are sad, but I'm here. You are not alone and it will be okay.",
            language_mode=SpeechLanguageMode.ENGLISH,
        )
        self.assertEqual(analysis.emotion, SpeechEmotion.REASSURING)
        self.assertIn(analysis.confidence, {EmotionConfidence.MEDIUM, EmotionConfidence.HIGH})

    def test_one_ambiguous_word_does_not_overclassify(self):
        for text in ("The loss column is on page four.", "This is an important field.", "A quiet file was created."):
            with self.subTest(text=text):
                self.assertEqual(self.emotion(text), SpeechEmotion.NEUTRAL)

    def test_hindi_marathi_and_mixed_language_signals(self):
        cases = (
            ("चिंता मत करो। मैं तुम्हारे साथ हूँ। सब ठीक हो जाएगा।", SpeechLanguageMode.HINDI_DEVANAGARI, SpeechEmotion.REASSURING),
            ("मला अजूनही आठवतं, ते दिवस आणि जुन्या आठवणी.", SpeechLanguageMode.MARATHI_DEVANAGARI, SpeechEmotion.NOSTALGIC),
            ("मला तुझा अभिमान आहे! Congratulations, खूप आनंद झाला!", SpeechLanguageMode.MIXED_MARATHI_ENGLISH, SpeechEmotion.JOYFUL),
            ("यह गलत है। यह अस्वीकार्य है।", SpeechLanguageMode.HINDI_DEVANAGARI, SpeechEmotion.ANGRY),
        )
        for text, mode, expected in cases:
            with self.subTest(mode=mode, expected=expected):
                self.assertEqual(self.emotion(text, mode), expected)

    def test_punctuation_only_is_neutral_and_empty_is_invalid(self):
        self.assertEqual(self.emotion("...?!"), SpeechEmotion.NEUTRAL)
        for text in ("", " \n "):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.emotion(text)


if __name__ == "__main__":
    unittest.main()
