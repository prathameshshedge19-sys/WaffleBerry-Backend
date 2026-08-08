"""Focused tests for authenticated transient text-to-speech generation."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import APITimeoutError
from pydantic import ValidationError

from app.dependencies.auth import get_current_user
from app.api.v1.audio import get_speech_service_for_request
from app.main import app
from app.schemas.audio import SpeechSynthesisRequest
from app.services.ai.exceptions import (
    AIConfigurationError,
    AIInvalidResponseError,
    AIProviderUnavailableError,
    AITimeoutError,
)
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.provider import SPEECH_MEDIA_TYPES, SpeechResult
from app.services.ai.speech_service import SpeechService
from app.services.speech_delivery_resolver import (
    FEMALE_DELIVERY_INSTRUCTIONS,
    FINAL_FIDELITY_INSTRUCTIONS,
    LANGUAGE_INSTRUCTIONS,
    MALE_DELIVERY_INSTRUCTIONS,
    NEUTRAL_DELIVERY_INSTRUCTIONS,
    SpeechLanguageMode,
)
from app.services.voice_profile_resolver import StandardVoiceProfile


class SpeechSchemaTests(unittest.TestCase):
    def test_valid_text_is_normalized(self):
        request = SpeechSynthesisRequest(text="  Hello Berry.  ")
        self.assertEqual(request.text, "Hello Berry.")

    def test_blank_and_whitespace_only_text_are_rejected(self):
        for value in ("", "   \n\t"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                SpeechSynthesisRequest(text=value)

    def test_over_limit_text_is_rejected_without_truncation(self):
        settings = SimpleNamespace(tts_max_text_characters=5)
        with patch("app.schemas.audio.get_settings", return_value=settings):
            with self.assertRaises(ValidationError):
                SpeechSynthesisRequest(text="123456")

    def test_supported_formats_are_normalized_and_accepted(self):
        for response_format in SPEECH_MEDIA_TYPES:
            with self.subTest(response_format=response_format):
                request = SpeechSynthesisRequest(
                    text="Hello",
                    response_format=response_format.upper(),
                )
                self.assertEqual(request.response_format, response_format)

    def test_unsupported_format_is_rejected(self):
        with self.assertRaises(ValidationError):
            SpeechSynthesisRequest(text="Hello", response_format="ogg")


class RecordingSpeechProvider:
    def __init__(self, content=b"speech"):
        self.content = content
        self.calls = []

    async def synthesize_speech(self, **kwargs):
        self.calls.append(kwargs)
        response_format = kwargs["response_format"]
        return SpeechResult(
            content=self.content,
            media_type=SPEECH_MEDIA_TYPES[response_format],
            file_extension=response_format,
        )


class SpeechServiceTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, provider):
        return SpeechService(
            provider,
            model="configured-tts-model",
            default_voice="alloy",
            standard_male_voice="cedar",
            standard_female_voice="marin",
            default_format="mp3",
            max_text_characters=100,
            timeout_seconds=12.5,
        )

    async def test_defaults_and_provider_arguments_are_resolved(self):
        provider = RecordingSpeechProvider()
        result = await self.make_service(provider).synthesize(text="  Hello  ")

        self.assertEqual(result.content, b"speech")
        self.assertEqual(
            provider.calls,
            [{
                "text": "Hello",
                "model": "configured-tts-model",
                "voice": "alloy",
                "response_format": "mp3",
                "timeout_seconds": 12.5,
                "instructions": None,
            }],
        )

    def test_invalid_speech_configuration_is_rejected_lazily(self):
        with self.assertRaises(AIConfigurationError):
            SpeechService(
                RecordingSpeechProvider(),
                model="",
                default_voice="alloy",
                standard_male_voice="cedar",
                standard_female_voice="marin",
                default_format="mp3",
                max_text_characters=100,
                timeout_seconds=12.5,
            )
        with self.assertRaises(AIConfigurationError):
            SpeechService(
                RecordingSpeechProvider(),
                model="tts-model",
                default_voice="alloy",
                standard_male_voice="cedar",
                standard_female_voice="marin",
                default_format="ogg",
                max_text_characters=100,
                timeout_seconds=12.5,
            )

    async def test_request_voice_and_format_overrides_are_used(self):
        provider = RecordingSpeechProvider()
        await self.make_service(provider).synthesize(
            text="Hello",
            voice="nova",
            response_format="wav",
        )
        self.assertEqual(provider.calls[0]["voice"], "nova")
        self.assertEqual(provider.calls[0]["response_format"], "wav")

    async def test_standard_profiles_use_configured_provider_voices(self):
        provider = RecordingSpeechProvider()
        service = self.make_service(provider)
        for profile, expected in (
            (StandardVoiceProfile.MALE, "cedar"),
            (StandardVoiceProfile.FEMALE, "marin"),
        ):
            with self.subTest(profile=profile):
                await service.synthesize(
                    text="Hello",
                    standard_voice_profile=profile,
                )
                self.assertEqual(provider.calls[-1]["voice"], expected)

    async def test_invalid_standard_profile_fails_safely(self):
        with self.assertRaises(AIConfigurationError):
            await self.make_service(RecordingSpeechProvider()).synthesize(
                text="Hello",
                standard_voice_profile="provider-voice-name",
            )

    async def test_supported_model_forwards_central_delivery_instructions(self):
        provider = RecordingSpeechProvider()
        service = SpeechService(
            provider,
            model="gpt-4o-mini-tts",
            default_voice="alloy",
            standard_male_voice="cedar",
            standard_female_voice="marin",
            default_format="mp3",
            max_text_characters=100,
            timeout_seconds=12.5,
        )
        await service.synthesize(text="Hello")
        self.assertEqual(
            provider.calls[0]["instructions"],
            f"{LANGUAGE_INSTRUCTIONS[SpeechLanguageMode.ENGLISH]}\n"
            f"{NEUTRAL_DELIVERY_INSTRUCTIONS}\n"
            f"{FINAL_FIDELITY_INSTRUCTIONS}",
        )

    async def test_normalized_text_and_profile_delivery_reach_provider(self):
        provider = RecordingSpeechProvider()
        service = SpeechService(
            provider,
            model="gpt-4o-mini-tts",
            default_voice="alloy",
            standard_male_voice="cedar",
            standard_female_voice="marin",
            default_format="mp3",
            max_text_characters=100,
            timeout_seconds=12.5,
        )
        for profile, expected_voice, expected_instructions in (
            (StandardVoiceProfile.MALE, "cedar", MALE_DELIVERY_INSTRUCTIONS),
            (StandardVoiceProfile.FEMALE, "marin", FEMALE_DELIVERY_INSTRUCTIONS),
        ):
            with self.subTest(profile=profile):
                await service.synthesize(
                    text="## Memory\n\n**Hello**, Asha!",
                    standard_voice_profile=profile,
                    preserve_text=True,
                )
                call = provider.calls[-1]
                self.assertEqual(call["text"], "Memory.\n\nHello, Asha!")
                self.assertEqual(call["voice"], expected_voice)
                self.assertIn(expected_instructions, call["instructions"])
                self.assertTrue(
                    call["instructions"].startswith(
                        LANGUAGE_INSTRUCTIONS[SpeechLanguageMode.ENGLISH]
                    )
                )
                self.assertTrue(
                    call["instructions"].endswith(FINAL_FIDELITY_INSTRUCTIONS)
                )

    async def test_source_limit_is_checked_before_normalization(self):
        service = SpeechService(
            RecordingSpeechProvider(),
            model="gpt-4o-mini-tts",
            default_voice="alloy",
            standard_male_voice="cedar",
            standard_female_voice="marin",
            default_format="mp3",
            max_text_characters=5,
            timeout_seconds=12.5,
        )
        with self.assertRaises(ValueError):
            await service.synthesize(text="**Hi**")

    async def test_empty_normalized_text_is_rejected_before_provider(self):
        provider = RecordingSpeechProvider()
        with self.assertRaises(ValueError):
            await self.make_service(provider).synthesize(
                text="https://example.com"
            )
        self.assertEqual(provider.calls, [])

    async def test_empty_provider_audio_is_rejected(self):
        provider = RecordingSpeechProvider(content=b"")
        with self.assertRaises(AIInvalidResponseError):
            await self.make_service(provider).synthesize(text="Hello")

    async def test_openai_adapter_returns_provider_neutral_audio(self):
        class SpeechAPI:
            async def create(api_self, **kwargs):
                api_self.kwargs = kwargs
                return SimpleNamespace(content=b"openai audio")

        speech_api = SpeechAPI()
        provider = object.__new__(OpenAIProvider)
        provider._client = SimpleNamespace(
            audio=SimpleNamespace(speech=speech_api)
        )
        result = await provider.synthesize_speech(
            text="Hello",
            model="gpt-4o-mini-tts",
            voice="alloy",
            response_format="opus",
            timeout_seconds=15,
            instructions="central instructions",
        )

        self.assertIsInstance(result, SpeechResult)
        self.assertEqual(result.content, b"openai audio")
        self.assertEqual(result.media_type, "audio/ogg")
        self.assertEqual(speech_api.kwargs["input"], "Hello")
        self.assertEqual(speech_api.kwargs["model"], "gpt-4o-mini-tts")
        self.assertEqual(speech_api.kwargs["voice"], "alloy")
        self.assertEqual(speech_api.kwargs["response_format"], "opus")
        self.assertEqual(speech_api.kwargs["timeout"], 15)
        self.assertEqual(
            speech_api.kwargs["instructions"],
            "central instructions",
        )

    async def test_empty_openai_audio_is_rejected(self):
        class SpeechAPI:
            async def create(self, **_kwargs):
                return SimpleNamespace(content=b"")

        provider = object.__new__(OpenAIProvider)
        provider._client = SimpleNamespace(
            audio=SimpleNamespace(speech=SpeechAPI())
        )
        with self.assertRaises(AIInvalidResponseError):
            await provider.synthesize_speech(
                text="Hello",
                model="tts-model",
                voice="alloy",
                response_format="mp3",
                timeout_seconds=15,
            )

    async def test_openai_timeout_is_translated(self):
        class SpeechAPI:
            async def create(self, **_kwargs):
                raise APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))

        provider = object.__new__(OpenAIProvider)
        provider._client = SimpleNamespace(
            audio=SimpleNamespace(speech=SpeechAPI())
        )
        with self.assertRaises(AITimeoutError):
            await provider.synthesize_speech(
                text="Hello",
                model="tts-model",
                voice="alloy",
                response_format="mp3",
                timeout_seconds=15,
            )


class FakeSpeechService:
    def __init__(self):
        self.calls = []
        self.error = None

    async def synthesize(self, *, text, voice=None, response_format=None):
        self.calls.append((text, voice, response_format))
        if self.error:
            raise self.error
        resolved_format = response_format or "mp3"
        return SpeechResult(
            content=b"mock speech bytes",
            media_type=SPEECH_MEDIA_TYPES[resolved_format],
            file_extension=resolved_format,
        )


class SpeechEndpointTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeSpeechService()
        app.dependency_overrides[get_speech_service_for_request] = lambda: self.service
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=1)
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_authentication_is_required(self):
        app.dependency_overrides.pop(get_current_user)
        response = self.client.post("/api/v1/audio/speech", json={"text": "Hello"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.service.calls, [])

    def test_valid_request_returns_binary_audio(self):
        response = self.client.post(
            "/api/v1/audio/speech",
            json={"text": "  Hello Berry.  ", "voice": None, "response_format": None},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"mock speech bytes")
        self.assertEqual(response.headers["content-type"], "audio/mpeg")
        self.assertEqual(self.service.calls, [("Hello Berry.", None, None)])

    def test_every_supported_format_has_the_correct_content_type(self):
        for response_format, media_type in SPEECH_MEDIA_TYPES.items():
            with self.subTest(response_format=response_format):
                response = self.client.post(
                    "/api/v1/audio/speech",
                    json={"text": "Hello", "response_format": response_format},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], media_type)

    def test_invalid_input_uses_request_validation(self):
        for payload in ({}, {"text": "   "}, {"text": "Hello", "response_format": "ogg"}):
            with self.subTest(payload=payload):
                response = self.client.post("/api/v1/audio/speech", json=payload)
                self.assertEqual(response.status_code, 422)

    def test_provider_failure_is_safe(self):
        self.service.error = AIProviderUnavailableError("provider secret")
        response = self.client.post(
            "/api/v1/audio/speech",
            json={"text": "Hello"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"]["code"],
            "speech_provider_unavailable",
        )
        self.assertNotIn("provider secret", response.text)

    def test_implementation_has_no_persistence_or_migration_dependency(self):
        self.assertEqual(self.service.calls, [])
        with open("app/api/v1/audio.py", encoding="utf-8") as route_file:
            route_source = route_file.read()
        with open("app/services/ai/speech_service.py", encoding="utf-8") as service_file:
            service_source = service_file.read()
        combined = route_source + service_source
        self.assertNotIn("get_db", combined)
        self.assertNotIn("Session", combined)
        self.assertNotIn("tempfile", combined)


if __name__ == "__main__":
    unittest.main()
