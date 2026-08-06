"""Sarvam Bulbul v3 adapter for persisted-message speech."""

import asyncio
import base64
import binascii
from dataclasses import dataclass
import json
from typing import Protocol
from urllib import error, request

from app.services.ai.exceptions import (
    AIAuthenticationError,
    AIConfigurationError,
    AIConnectionError,
    AIInvalidResponseError,
    AIProviderError,
    AIProviderUnavailableError,
    AIQuotaExceededError,
    AIRateLimitError,
    AITimeoutError,
)
from app.services.ai.provider import SpeechResult
from app.services.voice_profile_resolver import StandardVoiceProfile


SARVAM_TTS_ENDPOINT = "https://api.sarvam.ai/text-to-speech"
_SUPPORTED_SPEAKERS = frozenset({
    "shubh", "aditya", "ritu", "priya", "neha", "rahul", "pooja",
    "rohan", "simran", "kavya", "amit", "dev", "ishita", "shreya",
    "ratan", "varun", "manan", "sumit", "roopa", "kabir", "aayan",
    "ashutosh", "advait", "anand", "tanya", "tarun", "sunny", "mani",
    "gokul", "vijay", "shruti", "suhani", "mohit", "kavitha", "rehan",
    "soham", "rupali",
})
_SUPPORTED_LANGUAGES = frozenset({
    "en-IN", "hi-IN", "bn-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN",
    "mr-IN", "gu-IN", "pa-IN", "od-IN",
})


@dataclass(frozen=True, slots=True)
class SarvamHTTPResponse:
    status_code: int
    body: bytes


class SarvamTransport(Protocol):
    async def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> SarvamHTTPResponse:
        ...


class UrllibSarvamTransport:
    """Small async wrapper around the standard-library HTTP client."""

    async def post(self, **kwargs) -> SarvamHTTPResponse:
        return await asyncio.to_thread(self._post_sync, **kwargs)

    @staticmethod
    def _post_sync(*, url, headers, payload, timeout_seconds):
        outbound = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(outbound, timeout=timeout_seconds) as response:
                return SarvamHTTPResponse(response.status, response.read())
        except error.HTTPError as exc:
            return SarvamHTTPResponse(exc.code, exc.read())
        except TimeoutError as exc:
            raise AITimeoutError("Speech provider timed out.") from exc
        except error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise AITimeoutError("Speech provider timed out.") from exc
            raise AIConnectionError("Speech provider connection failed.") from exc
        except OSError as exc:
            raise AIConnectionError("Speech provider connection failed.") from exc


class SarvamBulbulProvider:
    """Call Bulbul v3 and expose only provider-neutral WAV audio."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        male_speaker: str,
        female_speaker: str,
        output_format: str,
        timeout_seconds: float,
        max_audio_bytes: int,
        pace: float,
        temperature: float,
        transport: SarvamTransport | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise AIConfigurationError("SARVAM_API_KEY is required.")
        if model != "bulbul:v3":
            raise AIConfigurationError("SARVAM_MODEL must be bulbul:v3.")
        speakers = {
            StandardVoiceProfile.MALE: male_speaker,
            StandardVoiceProfile.FEMALE: female_speaker,
        }
        if any(value not in _SUPPORTED_SPEAKERS for value in speakers.values()):
            raise AIConfigurationError("A configured Sarvam speaker is invalid.")
        if output_format != "wav":
            raise AIConfigurationError("SARVAM_OUTPUT_FORMAT must be wav.")
        if not 0.5 <= pace <= 2.0:
            raise AIConfigurationError("SARVAM_PACE must be between 0.5 and 2.0.")
        if not 0.01 <= temperature <= 2.0:
            raise AIConfigurationError(
                "SARVAM_TEMPERATURE must be between 0.01 and 2.0."
            )
        self._api_key = api_key.strip()
        self._model = model
        self._speakers = speakers
        self._output_format = output_format
        self._timeout_seconds = timeout_seconds
        self._max_audio_bytes = max_audio_bytes
        self._pace = pace
        self._temperature = temperature
        self._transport = transport or UrllibSarvamTransport()

    async def synthesize(
        self,
        *,
        text: str,
        standard_voice_profile: StandardVoiceProfile,
        language_code: str,
        selected_voice: str | None = None,
        dictionary_id: str | None = None,
        pace: float | None = None,
        temperature: float | None = None,
    ) -> SpeechResult:
        if language_code not in _SUPPORTED_LANGUAGES:
            raise AIProviderError("Speech language is invalid.")
        if selected_voice is not None:
            speaker = selected_voice.strip().lower()
            if speaker not in _SUPPORTED_SPEAKERS:
                raise AIProviderError("Speech speaker is invalid.")
        else:
            try:
                speaker = self._speakers[standard_voice_profile]
            except KeyError as exc:
                raise AIProviderError("Speech speaker is invalid.") from exc
        resolved_pace = self._pace if pace is None else pace
        resolved_temperature = (
            self._temperature if temperature is None else temperature
        )
        if not 0.5 <= resolved_pace <= 2.0:
            raise AIProviderError("Speech pace is invalid.")
        if not 0.01 <= resolved_temperature <= 2.0:
            raise AIProviderError("Speech temperature is invalid.")
        payload = {
            "text": text,
            "target_language_code": language_code,
            "speaker": speaker,
            "pace": resolved_pace,
            "model": self._model,
            "output_audio_codec": self._output_format,
            "temperature": resolved_temperature,
        }
        if dictionary_id:
            payload["dict_id"] = dictionary_id
        response = await self._transport.post(
            url=SARVAM_TTS_ENDPOINT,
            headers={
                "api-subscription-key": self._api_key,
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=self._timeout_seconds,
        )
        self._raise_for_status(response.status_code)
        try:
            body = json.loads(response.body)
            encoded = body["audios"][0]
            if not isinstance(encoded, str) or not encoded:
                raise ValueError
            audio = base64.b64decode(encoded, validate=True)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError,
                binascii.Error) as exc:
            raise AIInvalidResponseError("Speech provider returned invalid audio.") from exc
        if not audio or len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise AIInvalidResponseError("Speech provider returned invalid audio.")
        if len(audio) > self._max_audio_bytes:
            raise AIInvalidResponseError("Speech provider audio exceeded its limit.")
        return SpeechResult(audio, "audio/wav", "wav")

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code in {401, 403}:
            raise AIAuthenticationError("Speech provider authentication failed.")
        if status_code == 402:
            raise AIQuotaExceededError("Speech provider quota was exceeded.")
        if status_code == 429:
            raise AIRateLimitError("Speech provider rate limit was exceeded.")
        if status_code in {408, 504}:
            raise AITimeoutError("Speech provider timed out.")
        if status_code >= 500:
            raise AIProviderUnavailableError("Speech provider is unavailable.")
        raise AIProviderError("Speech provider rejected the request.")
