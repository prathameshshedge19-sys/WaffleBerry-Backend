"""Conservative Hindi and Marathi speech-language analysis tests."""

import unittest
import unicodedata

from app.services.speech_language_analyzer import (
    SpeechLanguageAnalyzer,
    SpeechLanguageMode,
)


class SpeechLanguageAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.detect = SpeechLanguageAnalyzer().detect

    def test_clear_language_modes(self):
        cases = (
            ("Hello, I still remember that evening.", SpeechLanguageMode.ENGLISH),
            (
                "दादर खूप गजबजलेलं आणि जिवंत ठिकाण आहे. मला तिथे जायचं आहे.",
                SpeechLanguageMode.MARATHI_DEVANAGARI,
            ),
            (
                "मुझे वह शाम याद है और वहाँ बहुत लोग बैठे थे।",
                SpeechLanguageMode.HINDI_DEVANAGARI,
            ),
            ("आज सुंदर दिवस आहे", SpeechLanguageMode.DEVANAGARI_UNKNOWN),
            (
                "Mala ajunhi athavta, apan sandhyakali gacchivar basun khup gappa maraycho.",
                SpeechLanguageMode.ROMANIZED_MARATHI,
            ),
            (
                "Dadar khup lively aahe, especially evening la, when the market is full.",
                SpeechLanguageMode.MIXED_MARATHI_ENGLISH,
            ),
            (
                "प्रथमेश १४ जुलै २०१९ रोजी सकाळी ७:३० वाजता मुंबईहून पुण्याला गेला होता।",
                SpeechLanguageMode.MARATHI_DEVANAGARI,
            ),
            (
                "Mumbai evening bahut lively hoti hai, especially when market is full.",
                SpeechLanguageMode.MIXED_HINDI_ENGLISH,
            ),
            ("東京の思い出", SpeechLanguageMode.MULTILINGUAL_UNKNOWN),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(self.detect(text), expected)

    def test_single_ambiguous_or_short_token_does_not_overclassify(self):
        self.assertEqual(self.detect("मला"), SpeechLanguageMode.DEVANAGARI_UNKNOWN)
        self.assertEqual(self.detect("A lovely la la land"), SpeechLanguageMode.ENGLISH)
        self.assertEqual(self.detect("This place aahe"), SpeechLanguageMode.ENGLISH)

    def test_whitespace_and_unicode_normalization_do_not_change_mode(self):
        text = "Mala   khup   athavta ani apan gappa maraycho"
        self.assertEqual(self.detect(text), SpeechLanguageMode.ROMANIZED_MARATHI)
        decomposed = unicodedata.normalize("NFD", "Café memories")
        self.assertEqual(self.detect(decomposed), SpeechLanguageMode.ENGLISH)

    def test_punctuation_only_is_unknown_and_empty_is_rejected(self):
        self.assertEqual(self.detect("?! । ॥"), SpeechLanguageMode.MULTILINGUAL_UNKNOWN)
        for value in ("", "  \n\t"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.detect(value)


if __name__ == "__main__":
    unittest.main()
