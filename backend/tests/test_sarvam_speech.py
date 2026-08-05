"""Mock-only contract tests for Sarvam Bulbul v3 message speech."""

import base64
import json
import unittest

from app.services.ai.exceptions import (
    AIAuthenticationError,
    AIConnectionError,
    AIInvalidResponseError,
    AIProviderError,
    AIProviderUnavailableError,
    AIQuotaExceededError,
    AIRateLimitError,
    AITimeoutError,
)
from app.services.ai.sarvam_speech_provider import (
    SARVAM_TTS_ENDPOINT,
    SarvamBulbulProvider,
    SarvamHTTPResponse,
)
from app.services.ai.sarvam_speech_service import sarvam_language_code
from app.services.ai.sarvam_speech_service import SarvamSpeechService
from app.services.speech_language_analyzer import SpeechLanguageMode
from app.services.voice_profile_resolver import (
    StandardVoiceProfile,
    StandardVoiceResolver,
)


WAV = b"RIFF\x04\x00\x00\x00WAVE"


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response or SarvamHTTPResponse(
            200,
            json.dumps({"request_id": "safe", "audios": [
                base64.b64encode(WAV).decode("ascii")
            ]}).encode(),
        )
        self.error = error
        self.calls = []

    async def post(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def make_provider(transport, **overrides):
    values = dict(
        api_key="secret-key",
        model="bulbul:v3",
        male_speaker="shubh",
        female_speaker="priya",
        output_format="wav",
        timeout_seconds=60,
        max_audio_bytes=1024,
        pace=0.92,
        temperature=0.6,
        transport=transport,
    )
    values.update(overrides)
    return SarvamBulbulProvider(**values)


class SarvamProviderContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_endpoint_auth_payload_and_wav_decoding(self):
        transport = FakeTransport()
        result = await make_provider(transport).synthesize(
            text="Hello Berry",
            standard_voice_profile=StandardVoiceProfile.MALE,
            language_code="en-IN",
        )
        call = transport.calls[0]
        self.assertEqual(call["url"], SARVAM_TTS_ENDPOINT)
        self.assertEqual(call["headers"]["api-subscription-key"], "secret-key")
        self.assertEqual(call["timeout_seconds"], 60)
        self.assertEqual(call["payload"], {
            "text": "Hello Berry",
            "target_language_code": "en-IN",
            "speaker": "shubh",
            "pace": 0.92,
            "model": "bulbul:v3",
            "output_audio_codec": "wav",
            "temperature": 0.6,
        })
        self.assertEqual(result.content, WAV)
        self.assertEqual((result.media_type, result.file_extension), ("audio/wav", "wav"))

    async def test_relationship_profiles_are_mapped_inside_provider(self):
        cases = {
            "Father": "shubh", "Brother": "shubh", "Grandfather": "shubh",
            "Mother": "priya", "Sister": "priya", "Grandmother": "priya",
            "Unknown": "priya",
        }
        resolver = StandardVoiceResolver("standard_female")
        for relationship, expected in cases.items():
            with self.subTest(relationship=relationship):
                transport = FakeTransport()
                await make_provider(transport).synthesize(
                    text="Berry", standard_voice_profile=resolver.resolve(relationship),
                    language_code="en-IN",
                )
                self.assertEqual(transport.calls[0]["payload"]["speaker"], expected)

    async def test_dictionary_id_is_optional_and_forwarded_only_when_present(self):
        without = FakeTransport()
        await make_provider(without).synthesize(
            text="Prathamesh", standard_voice_profile=StandardVoiceProfile.MALE,
            language_code="mr-IN",
        )
        self.assertNotIn("dict_id", without.calls[0]["payload"])

        configured = FakeTransport()
        await make_provider(configured).synthesize(
            text="Prathamesh", standard_voice_profile=StandardVoiceProfile.FEMALE,
            language_code="hi-IN", dictionary_id="p_global_v1",
        )
        self.assertEqual(configured.calls[0]["payload"]["dict_id"], "p_global_v1")
        self.assertEqual(configured.calls[0]["payload"]["text"], "Prathamesh")
        self.assertEqual(configured.calls[0]["payload"]["speaker"], "priya")

    async def test_language_voice_and_dictionary_integration_matrix(self):
        cases = (
            ("mr-IN", StandardVoiceProfile.MALE, "shubh"),
            ("mr-IN", StandardVoiceProfile.FEMALE, "priya"),
            ("hi-IN", StandardVoiceProfile.MALE, "shubh"),
            ("hi-IN", StandardVoiceProfile.FEMALE, "priya"),
            ("en-IN", StandardVoiceProfile.FEMALE, "priya"),
        )
        for language, profile, speaker in cases:
            with self.subTest(language=language, profile=profile):
                transport = FakeTransport()
                await make_provider(transport).synthesize(
                    text="WaffleBerry", standard_voice_profile=profile,
                    language_code=language, dictionary_id="p_global_v1",
                )
                payload = transport.calls[0]["payload"]
                self.assertEqual(payload["target_language_code"], language)
                self.assertEqual(payload["speaker"], speaker)
                self.assertEqual(payload["dict_id"], "p_global_v1")

    async def test_emotion_controls_override_defaults_without_unsupported_fields(self):
        transport = FakeTransport()
        await make_provider(transport).synthesize(
            text="Wonderful news!",
            standard_voice_profile=StandardVoiceProfile.FEMALE,
            language_code="en-IN",
            dictionary_id="p_global_v1",
            pace=1.0,
            temperature=0.76,
        )
        payload = transport.calls[0]["payload"]
        self.assertEqual(payload["pace"], 1.0)
        self.assertEqual(payload["temperature"], 0.76)
        self.assertEqual(set(payload), {
            "text", "target_language_code", "speaker", "pace", "model",
            "output_audio_codec", "temperature", "dict_id",
        })
        for unsupported in (
            "emotion", "style", "instructions", "pitch", "loudness",
            "ssml", "pause", "phoneme", "nonverbal",
        ):
            self.assertNotIn(unsupported, payload)

    async def test_http_failures_are_translated(self):
        cases = (
            (401, AIAuthenticationError), (403, AIAuthenticationError),
            (402, AIQuotaExceededError), (429, AIRateLimitError),
            (408, AITimeoutError), (504, AITimeoutError),
            (503, AIProviderUnavailableError), (422, AIProviderError),
        )
        for status, expected in cases:
            with self.subTest(status=status), self.assertRaises(expected):
                await make_provider(FakeTransport(SarvamHTTPResponse(status, b"secret"))).synthesize(
                    text="Berry", standard_voice_profile=StandardVoiceProfile.FEMALE,
                    language_code="hi-IN",
                )

    async def test_transport_timeout_and_connection_errors_are_preserved(self):
        for error in (AITimeoutError("secret"), AIConnectionError("secret")):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                await make_provider(FakeTransport(error=error)).synthesize(
                    text="Berry", standard_voice_profile=StandardVoiceProfile.MALE,
                    language_code="mr-IN",
                )

    async def test_invalid_empty_non_wav_and_oversized_audio_are_rejected(self):
        payloads = (
            {"audios": ["not base64"]},
            {"audios": [""]},
            {"audios": [base64.b64encode(b"not wav audio").decode()]},
            {},
        )
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(AIInvalidResponseError):
                transport = FakeTransport(SarvamHTTPResponse(200, json.dumps(payload).encode()))
                await make_provider(transport).synthesize(
                    text="Berry", standard_voice_profile=StandardVoiceProfile.MALE,
                    language_code="en-IN",
                )
        with self.assertRaises(AIInvalidResponseError):
            await make_provider(FakeTransport(), max_audio_bytes=len(WAV) - 1).synthesize(
                text="Berry", standard_voice_profile=StandardVoiceProfile.MALE,
                language_code="en-IN",
            )

    async def test_invalid_language_is_rejected_before_transport(self):
        transport = FakeTransport()
        with self.assertRaises(AIProviderError):
            await make_provider(transport).synthesize(
                text="Berry", standard_voice_profile=StandardVoiceProfile.MALE,
                language_code="unknown",
            )
        self.assertEqual(transport.calls, [])


class SarvamLanguageMappingTests(unittest.TestCase):
    def test_every_language_mode_maps_without_unknown(self):
        expected = {
            SpeechLanguageMode.ENGLISH: "en-IN",
            SpeechLanguageMode.HINDI_DEVANAGARI: "hi-IN",
            SpeechLanguageMode.MARATHI_DEVANAGARI: "mr-IN",
            SpeechLanguageMode.ROMANIZED_MARATHI: "mr-IN",
            SpeechLanguageMode.MIXED_MARATHI_ENGLISH: "mr-IN",
            SpeechLanguageMode.MIXED_HINDI_ENGLISH: "hi-IN",
            SpeechLanguageMode.DEVANAGARI_UNKNOWN: "hi-IN",
            SpeechLanguageMode.MULTILINGUAL_UNKNOWN: "en-IN",
        }
        self.assertEqual({mode: sarvam_language_code(mode) for mode in expected}, expected)
        self.assertNotIn("unknown", expected.values())


class FakeSarvamProvider:
    def __init__(self):
        self.calls = []

    async def synthesize(self, **kwargs):
        self.calls.append(kwargs)
        from app.services.ai.provider import SpeechResult
        return SpeechResult(WAV, "audio/wav", "wav")


class SarvamSpeechServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_unverified_word_altering_features_fail_closed(self):
        from app.services.ai.exceptions import AIConfigurationError
        for option in ("nonverbal_cues_enabled", "discourse_markers_enabled"):
            with self.subTest(option=option), self.assertRaises(AIConfigurationError):
                SarvamSpeechService(
                    FakeSarvamProvider(), max_text_characters=2500,
                    **{option: True},
                )

    async def test_normalizes_text_and_uses_analysis_only_for_language_code(self):
        provider = FakeSarvamProvider()
        service = SarvamSpeechService(provider, max_text_characters=2500)
        result = await service.synthesize(
            text="**Hello** [Berry](https://example.com)",
            standard_voice_profile=StandardVoiceProfile.FEMALE,
            response_format="mp3",
        )
        self.assertEqual(provider.calls[0], {
            "text": "Hello Berry",
            "standard_voice_profile": StandardVoiceProfile.FEMALE,
            "language_code": "en-IN",
            "dictionary_id": None,
            "pace": 0.96,
            "temperature": 0.55,
        })
        self.assertEqual(result.file_extension, "wav")

    async def test_normalized_provider_limit_is_enforced_without_splitting(self):
        provider = FakeSarvamProvider()
        service = SarvamSpeechService(provider, max_text_characters=5)
        with self.assertRaises(ValueError):
            await service.synthesize(
                text="123456",
                standard_voice_profile=StandardVoiceProfile.MALE,
                response_format=None,
            )
        self.assertEqual(provider.calls, [])

    async def test_configured_dictionary_is_forwarded_without_changing_text(self):
        from app.services.pronunciation_dictionary_service import (
            PronunciationDictionaryResolver,
        )
        provider = FakeSarvamProvider()
        service = SarvamSpeechService(
            provider,
            max_text_characters=2500,
            dictionary_resolver=PronunciationDictionaryResolver(
                "p_global_v1", required=True
            ),
        )
        await service.synthesize(
            text="Prathamesh went to Dombivli.",
            standard_voice_profile=StandardVoiceProfile.MALE,
            response_format="wav",
        )
        self.assertEqual(
            provider.calls[0]["text"], "Prathamesh went to Dombivli."
        )
        self.assertEqual(provider.calls[0]["dictionary_id"], "p_global_v1")
        self.assertEqual(provider.calls[0]["pace"], 0.96)
        self.assertEqual(provider.calls[0]["temperature"], 0.55)

    async def test_emotion_profile_text_voice_language_and_dictionary_are_combined(self):
        from app.services.pronunciation_dictionary_service import (
            PronunciationDictionaryResolver,
        )
        cases = (
            (
                "Mala ajunhi athavta, apan sandhyakali khup gappa maraycho.",
                StandardVoiceProfile.MALE, "mr-IN", 0.87, 0.66,
            ),
            (
                "चिंता मत करो। मैं तुम्हारे साथ हूँ। सब ठीक हो जाएगा।",
                StandardVoiceProfile.FEMALE, "hi-IN", 0.88, 0.62,
            ),
            (
                "I miss you. It hurts to feel so lonely.",
                StandardVoiceProfile.FEMALE, "en-IN", 0.82, 0.52,
            ),
        )
        for text, profile, language, pace, temperature in cases:
            with self.subTest(language=language, profile=profile):
                provider = FakeSarvamProvider()
                service = SarvamSpeechService(
                    provider,
                    max_text_characters=2500,
                    dictionary_resolver=PronunciationDictionaryResolver(
                        "p_global_v1", required=True
                    ),
                )
                await service.synthesize(
                    text=text,
                    standard_voice_profile=profile,
                    response_format="wav",
                )
                call = provider.calls[0]
                self.assertEqual(call["standard_voice_profile"], profile)
                self.assertEqual(call["language_code"], language)
                self.assertEqual(call["dictionary_id"], "p_global_v1")
                self.assertEqual((call["pace"], call["temperature"]), (pace, temperature))
                self.assertNotIn("<break", call["text"])
                self.assertNotIn("[sigh", call["text"])


if __name__ == "__main__":
    unittest.main()
