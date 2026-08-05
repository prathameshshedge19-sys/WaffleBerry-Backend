"""Tests for deterministic, meaning-preserving spoken-text cleanup."""

import unittest

from app.services.speech_text_normalizer import SpeechTextNormalizer


class SpeechTextNormalizerTests(unittest.TestCase):
    def setUp(self):
        self.normalize = SpeechTextNormalizer().normalize

    def test_plain_text_and_names_are_preserved(self):
        self.assertEqual(
            self.normalize("Asha remembers Dadar clearly."),
            "Asha remembers Dadar clearly.",
        )

    def test_markdown_emphasis_headings_and_lists_become_spoken_text(self):
        source = """## Memory

- **Warm** evenings
- *Quiet* conversations

1. First visit
2) Second visit"""
        self.assertEqual(
            self.normalize(source),
            "Memory.\n\nWarm evenings. Quiet conversations.\n\n"
            "1. First visit. 2. Second visit.",
        )

    def test_links_urls_html_code_and_technical_ids_are_cleaned(self):
        source = (
            '<p>Read [the memory](https://example.com/memory) 😊</p>\n'
            'https://example.com/raw\n'
            'ID 123e4567-e89b-12d3-a456-426614174000\n'
            '```python\nprint("not conversational")\n```'
        )
        normalized = self.normalize(source)
        self.assertIn("Read the memory", normalized)
        self.assertNotIn("https", normalized)
        self.assertNotIn("123e4567", normalized)
        self.assertNotIn("print", normalized)
        self.assertNotIn("😊", normalized)

    def test_punctuation_whitespace_and_paragraphs_are_normalized(self):
        self.assertEqual(
            self.normalize("Hello!!!   Are you there???\n\nWait — yes; I am...."),
            "Hello! Are you there?\n\nWait, yes. I am...",
        )

    def test_unicode_marathi_hindi_and_mixed_text_are_preserved(self):
        samples = (
            "दादर खूप गजबजलेलं आणि जिवंत ठिकाण आहे.",
            "मुझे वह शाम आज भी याद है।",
            "Dadar khup lively aahe, especially evening la.",
            "आज market मध्ये खूप लोक आहेत.",
            "Café 東京 Asha.",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(self.normalize(sample), sample)

    def test_inline_code_keeps_content_without_visual_delimiters(self):
        self.assertEqual(self.normalize("Use `memory_id` carefully."), "Use memory_id carefully.")

    def test_empty_normalized_result_is_rejected(self):
        for source in ("😊✨", "https://example.com", "```x\ncode\n```"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.normalize(source)


if __name__ == "__main__":
    unittest.main()
