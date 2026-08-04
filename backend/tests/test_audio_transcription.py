"""Focused tests for authenticated transient audio transcription."""

import io
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.dependencies.ai import get_transcription_service
from app.dependencies.auth import get_current_user
from app.main import app
from app.services.ai.exceptions import AIProviderUnavailableError
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.transcription_service import (
    MAX_AUDIO_UPLOAD_BYTES,
    AudioValidationError,
    TranscriptionService,
    validate_audio_upload,
)


class FakeTranscriptionService:
    def __init__(self, outcome="A remembered story."):
        self.outcome = outcome
        self.calls = []

    async def transcribe(self, data, content_type):
        self.calls.append((data, content_type))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class AudioTranscriptionEndpointTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeTranscriptionService()
        app.dependency_overrides[get_transcription_service] = lambda: self.service
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            user_id=1
        )
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_unauthenticated_request_is_rejected(self):
        app.dependency_overrides.pop(get_current_user)
        response = self.client.post(
            "/api/v1/audio/transcribe",
            files={"file": ("voice.webm", b"audio", "audio/webm")},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.service.calls, [])

    def test_missing_file_is_rejected(self):
        response = self.client.post("/api/v1/audio/transcribe")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "audio_missing")

    def test_empty_file_is_rejected(self):
        response = self.client.post(
            "/api/v1/audio/transcribe",
            files={"file": ("voice.webm", b"", "audio/webm")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "audio_empty")

    def test_unsupported_mime_type_is_rejected(self):
        response = self.client.post(
            "/api/v1/audio/transcribe",
            files={"file": ("voice.txt", b"not audio", "text/plain")},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"]["code"],
            "audio_format_unsupported",
        )

    def test_supported_browser_formats_are_accepted(self):
        for mime_type, extension in (
            ("audio/webm", "webm"),
            ("audio/mp4", "mp4"),
            ("audio/ogg", "ogg"),
        ):
            with self.subTest(mime_type=mime_type):
                response = self.client.post(
                    "/api/v1/audio/transcribe",
                    files={
                        "file": (
                            f"voice.{extension}",
                            b"browser audio",
                            mime_type,
                        )
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"text": "A remembered story."})

    def test_provider_failure_maps_to_stable_safe_error(self):
        self.service.outcome = AIProviderUnavailableError("provider secret")
        response = self.client.post(
            "/api/v1/audio/transcribe",
            files={"file": ("voice.webm", b"audio", "audio/webm")},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "transcription_provider_unavailable",
                "message": "Transcription is temporarily unavailable.",
            },
        )
        self.assertNotIn("provider secret", response.text)


class AudioTranscriptionServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_oversized_file_is_rejected(self):
        with self.assertRaises(AudioValidationError) as raised:
            validate_audio_upload(
                b"x" * (MAX_AUDIO_UPLOAD_BYTES + 1),
                "audio/webm",
            )
        self.assertEqual(raised.exception.code, "audio_too_large")
        self.assertEqual(raised.exception.status_code, 413)

    def test_codec_parameter_is_normalized(self):
        audio = validate_audio_upload(
            b"audio",
            "audio/webm;codecs=opus",
        )
        self.assertEqual(audio.content_type, "audio/webm")
        self.assertEqual(audio.filename, "voice-message.webm")

    async def test_provider_receives_file_like_audio_and_configured_model(self):
        class Provider:
            async def transcribe_audio(
                provider_self,
                audio,
                *,
                filename,
                content_type,
                model,
            ):
                provider_self.stream = audio
                provider_self.received = (
                    audio.read(),
                    filename,
                    content_type,
                    model,
                )
                return "  Transcript text.  "

        provider = Provider()
        service = TranscriptionService(
            provider,
            model="configured-transcription-model",
        )
        result = await service.transcribe(b"audio bytes", "audio/ogg")

        self.assertEqual(result, "Transcript text.")
        self.assertEqual(
            provider.received,
            (
                b"audio bytes",
                "voice-message.ogg",
                "audio/ogg",
                "configured-transcription-model",
            ),
        )
        self.assertTrue(provider.stream.closed)

    async def test_openai_provider_uses_audio_api_and_json_response(self):
        class Transcriptions:
            async def create(transcriptions_self, **kwargs):
                transcriptions_self.kwargs = kwargs
                return SimpleNamespace(text="Provider transcript")

        transcriptions = Transcriptions()
        provider = object.__new__(OpenAIProvider)
        provider._client = SimpleNamespace(
            audio=SimpleNamespace(transcriptions=transcriptions)
        )
        result = await provider.transcribe_audio(
            io.BytesIO(b"audio"),
            filename="voice-message.mp4",
            content_type="audio/mp4",
            model="gpt-4o-mini-transcribe",
        )

        self.assertEqual(result, "Provider transcript")
        self.assertEqual(
            transcriptions.kwargs["model"],
            "gpt-4o-mini-transcribe",
        )
        self.assertEqual(transcriptions.kwargs["response_format"], "json")
        filename, stream, content_type = transcriptions.kwargs["file"]
        self.assertEqual(filename, "voice-message.mp4")
        self.assertEqual(stream.read(), b"audio")
        self.assertEqual(content_type, "audio/mp4")

    def test_transcription_implementation_has_no_persistence_or_content_logging(self):
        root = Path(__file__).resolve().parents[1]
        service_source = (root / "app/services/ai/transcription_service.py").read_text()
        route_source = (root / "app/api/v1/audio.py").read_text()
        combined = f"{service_source}\n{route_source}"

        self.assertNotIn("get_db", combined)
        self.assertNotIn("Session", combined)
        self.assertNotIn("tempfile", combined)
        self.assertNotIn("open(", combined)
        self.assertNotIn("transcript=%", combined)
        self.assertNotIn("transcript=", combined)
        self.assertNotIn("filename=upload.filename", combined)


if __name__ == "__main__":
    unittest.main()
