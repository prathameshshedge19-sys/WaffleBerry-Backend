"""Local pronunciation source and runtime resolver tests."""

import json
from pathlib import Path
import tempfile
import unittest

from app.services.ai.exceptions import AIConfigurationError
from app.services.pronunciation_dictionary_service import (
    PronunciationDictionaryResolver,
    PronunciationDictionarySourceLoader,
    PronunciationDictionaryValidationError,
)


class PronunciationDictionarySourceTests(unittest.TestCase):
    def setUp(self):
        self.loader = PronunciationDictionarySourceLoader()
        self.valid = {
            "version": 1,
            "description": "Reviewed terms",
            "pronunciations": {
                "mr-IN": {"Prathamesh": "प्रथमेश"},
                "hi-IN": {"Dombivli": "डोंबिवली"},
                "en-IN": {"WaffleBerry": "waffle berry"},
            },
        }

    def test_valid_unicode_source_and_metadata_stripping(self):
        result = self.loader.validate(self.valid)
        self.assertEqual(result.version, 1)
        self.assertEqual(result.entry_count, 3)
        self.assertEqual(
            result.provider_payload(),
            {"pronunciations": self.valid["pronunciations"]},
        )
        self.assertNotIn("version", result.provider_payload())
        self.assertNotIn("description", result.provider_payload())

    def test_required_structure_and_values_are_validated(self):
        invalid = (
            {},
            {"version": 0, "pronunciations": {"en-IN": {"x": "y"}}},
            {"version": 1, "pronunciations": {"xx-XX": {"x": "y"}}},
            {"version": 1, "pronunciations": {"en-IN": {"": "y"}}},
            {"version": 1, "pronunciations": {"en-IN": {"x": " "}}},
            {"version": 1, "pronunciations": {"en-IN": {"x": None}}},
            {"version": 1, "pronunciations": {"en-IN": {"x\n": "y"}}},
        )
        for document in invalid:
            with self.subTest(document=document), self.assertRaises(
                PronunciationDictionaryValidationError
            ):
                self.loader.validate(document)

    def test_total_provider_word_limit_is_enforced(self):
        document = {
            "version": 1,
            "pronunciations": {
                "en-IN": {f"term-{index}": "spoken" for index in range(101)}
            },
        }
        with self.assertRaises(PronunciationDictionaryValidationError):
            self.loader.validate(document)

    def test_invalid_json_and_duplicate_keys_are_rejected(self):
        values = (
            b"not json",
            b'{"version":1,"pronunciations":{"en-IN":{"AI":"A I","AI":"aye"}}}',
        )
        for value in values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "dictionary.json"
                source.write_bytes(value)
                with self.assertRaises(PronunciationDictionaryValidationError):
                    self.loader.load(source)

    def test_versioned_repository_source_is_valid(self):
        path = Path("config/pronunciation/waffleberry_global_v1.json")
        result = self.loader.load(path)
        self.assertEqual(result.version, 1)
        self.assertLessEqual(result.entry_count, 100)


class PronunciationDictionaryResolverTests(unittest.TestCase):
    def test_optional_absent_and_configured_global_dictionary(self):
        optional = PronunciationDictionaryResolver(None, required=False)
        self.assertIsNone(optional.resolve(language_code="mr-IN", legacy_id=42))
        configured = PronunciationDictionaryResolver(" p_global ", required=True)
        self.assertEqual(
            configured.resolve(language_code="hi-IN", legacy_id=None),
            "p_global",
        )

    def test_required_missing_and_invalid_ids_fail_safely(self):
        for identifier, required in ((None, True), ("bad id", False), ("x\n", False)):
            with self.subTest(identifier=identifier), self.assertRaises(
                AIConfigurationError
            ):
                PronunciationDictionaryResolver(identifier, required=required)


if __name__ == "__main__":
    unittest.main()
