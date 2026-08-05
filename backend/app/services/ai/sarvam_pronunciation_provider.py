"""Sarvam pronunciation-dictionary API adapter."""

import asyncio
from dataclasses import dataclass
import json
from urllib import error, parse, request

from app.services.ai.exceptions import (
    AIAuthenticationError,
    AIConnectionError,
    AIInvalidResponseError,
    AIProviderError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)


SARVAM_DICTIONARY_ENDPOINT = (
    "https://api.sarvam.ai/text-to-speech/pronunciation-dictionary"
)
_BOUNDARY = "WaffleBerrySarvamDictionaryBoundary"


@dataclass(frozen=True, slots=True)
class PronunciationHTTPResponse:
    status_code: int
    body: bytes


class UrllibPronunciationTransport:
    async def request(self, **kwargs) -> PronunciationHTTPResponse:
        return await asyncio.to_thread(self._request_sync, **kwargs)

    @staticmethod
    def _request_sync(*, method, url, headers, body, timeout_seconds):
        outbound = request.Request(
            url, data=body, headers=headers, method=method
        )
        try:
            with request.urlopen(outbound, timeout=timeout_seconds) as response:
                return PronunciationHTTPResponse(response.status, response.read())
        except error.HTTPError as exc:
            return PronunciationHTTPResponse(exc.code, exc.read())
        except TimeoutError as exc:
            raise AITimeoutError("Dictionary provider timed out.") from exc
        except error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise AITimeoutError("Dictionary provider timed out.") from exc
            raise AIConnectionError("Dictionary provider connection failed.") from exc
        except OSError as exc:
            raise AIConnectionError("Dictionary provider connection failed.") from exc


class SarvamPronunciationProvider:
    """Create and inspect Bulbul v3 pronunciation dictionaries."""

    def __init__(self, *, api_key: str, timeout_seconds: float, transport=None):
        if not isinstance(api_key, str) or not api_key.strip():
            from app.services.ai.exceptions import AIConfigurationError
            raise AIConfigurationError("SARVAM_API_KEY is required.")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds
        self._transport = transport or UrllibPronunciationTransport()

    async def create(self, payload: dict[str, object]) -> str:
        response = await self._upload("POST", payload)
        return self._dictionary_id(response)

    async def update(self, dictionary_id: str, payload: dict[str, object]) -> str:
        identifier = self._safe_identifier(dictionary_id)
        response = await self._upload(
            "PUT", payload, query={"dict_id": identifier}
        )
        return self._dictionary_id(response)

    async def list(self) -> dict[str, object]:
        response = await self._send("GET", SARVAM_DICTIONARY_ENDPOINT)
        document = self._json(response)
        count = document.get("dictionary_count")
        dictionaries = document.get("dictionaries")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not isinstance(dictionaries, list)
            or not all(isinstance(item, str) and item for item in dictionaries)
        ):
            raise AIInvalidResponseError("Dictionary provider returned invalid data.")
        return {"dictionary_count": count, "dictionaries": dictionaries}

    async def get(self, dictionary_id: str) -> dict[str, object]:
        identifier = self._safe_identifier(dictionary_id)
        response = await self._send(
            "GET",
            f"{SARVAM_DICTIONARY_ENDPOINT}/{parse.quote(identifier, safe='')}",
        )
        document = self._json(response)
        if not isinstance(document.get("pronunciations"), dict):
            raise AIInvalidResponseError("Dictionary provider returned invalid data.")
        return {"pronunciations": document["pronunciations"]}

    async def _upload(self, method, payload, query=None):
        serialized = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        body = (
            f"--{_BOUNDARY}\r\n"
            'Content-Disposition: form-data; name="file"; '
            'filename="waffleberry_pronunciations.json"\r\n'
            "Content-Type: application/json\r\n\r\n"
        ).encode("ascii") + serialized + f"\r\n--{_BOUNDARY}--\r\n".encode("ascii")
        url = SARVAM_DICTIONARY_ENDPOINT
        if query:
            url = f"{url}?{parse.urlencode(query)}"
        return await self._send(
            method,
            url,
            body=body,
            content_type=f"multipart/form-data; boundary={_BOUNDARY}",
        )

    async def _send(self, method, url, *, body=None, content_type=None):
        headers = {"api-subscription-key": self._api_key}
        if content_type:
            headers["Content-Type"] = content_type
        response = await self._transport.request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=self._timeout_seconds,
        )
        self._raise_for_status(response.status_code)
        return response

    def _dictionary_id(self, response):
        identifier = self._json(response).get("dictionary_id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise AIInvalidResponseError(
                "Dictionary provider did not return a dictionary ID."
            )
        return self._safe_identifier(identifier)

    @staticmethod
    def _json(response):
        try:
            document = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AIInvalidResponseError(
                "Dictionary provider returned malformed data."
            ) from exc
        if not isinstance(document, dict):
            raise AIInvalidResponseError("Dictionary provider returned invalid data.")
        return document

    @staticmethod
    def _safe_identifier(value):
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value.strip()) > 256
            or any(character.isspace() for character in value.strip())
        ):
            raise AIProviderError("Dictionary identifier is invalid.")
        return value.strip()

    @staticmethod
    def _raise_for_status(status_code):
        if 200 <= status_code < 300:
            return
        if status_code in {401, 403}:
            raise AIAuthenticationError("Dictionary authentication failed.")
        if status_code == 429:
            raise AIRateLimitError("Dictionary rate limit was exceeded.")
        if status_code in {408, 504}:
            raise AITimeoutError("Dictionary provider timed out.")
        if status_code >= 500:
            raise AIProviderUnavailableError("Dictionary provider is unavailable.")
        raise AIProviderError("Dictionary provider rejected the request.")
