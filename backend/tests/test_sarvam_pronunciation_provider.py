"""Mock transport tests for Sarvam pronunciation dictionary operations."""

import json
import unittest

from app.services.ai.exceptions import (
    AIAuthenticationError,
    AIConnectionError,
    AIInvalidResponseError,
    AIProviderError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)
from app.services.ai.sarvam_pronunciation_provider import (
    SARVAM_DICTIONARY_ENDPOINT,
    PronunciationHTTPResponse,
    SarvamPronunciationProvider,
)


class FakeTransport:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.calls = []

    async def request(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.responses.pop(0)


def provider(transport):
    return SarvamPronunciationProvider(
        api_key="secret", timeout_seconds=60, transport=transport
    )


class SarvamPronunciationProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_uses_official_endpoint_auth_and_multipart_file(self):
        transport = FakeTransport([
            PronunciationHTTPResponse(200, b'{"dictionary_id":"p_123"}')
        ])
        payload = {"pronunciations": {"mr-IN": {"Pune": "पुणे"}}}
        self.assertEqual(await provider(transport).create(payload), "p_123")
        call = transport.calls[0]
        self.assertEqual((call["method"], call["url"]), (
            "POST", SARVAM_DICTIONARY_ENDPOINT
        ))
        self.assertEqual(call["headers"]["api-subscription-key"], "secret")
        self.assertIn("multipart/form-data; boundary=", call["headers"]["Content-Type"])
        self.assertIn(b'name="file"', call["body"])
        self.assertIn(b"filename=\"waffleberry_pronunciations.json\"", call["body"])
        self.assertIn(b"Content-Type: application/json", call["body"])
        self.assertIn(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), call["body"])

    async def test_update_list_and_get_follow_official_contract(self):
        transport = FakeTransport([
            PronunciationHTTPResponse(200, b'{"dictionary_id":"p_123"}'),
            PronunciationHTTPResponse(200, b'{"dictionary_count":1,"dictionaries":["p_123"]}'),
            PronunciationHTTPResponse(200, b'{"pronunciations":{"en-IN":{"AI":"A I"}}}'),
        ])
        adapter = provider(transport)
        self.assertEqual(await adapter.update("p_123", {"pronunciations": {"en-IN": {"AI": "A I"}}}), "p_123")
        self.assertEqual(await adapter.list(), {"dictionary_count": 1, "dictionaries": ["p_123"]})
        self.assertEqual(await adapter.get("p_123"), {"pronunciations": {"en-IN": {"AI": "A I"}}})
        self.assertEqual(transport.calls[0]["method"], "PUT")
        self.assertTrue(transport.calls[0]["url"].endswith("?dict_id=p_123"))
        self.assertEqual(transport.calls[1]["method"], "GET")
        self.assertEqual(transport.calls[2]["url"], f"{SARVAM_DICTIONARY_ENDPOINT}/p_123")

    async def test_status_and_transport_failures_are_safe(self):
        cases = (
            (403, AIAuthenticationError), (429, AIRateLimitError),
            (500, AIProviderUnavailableError), (400, AIProviderError),
            (413, AIProviderError), (422, AIProviderError), (404, AIProviderError),
        )
        for status, expected in cases:
            with self.subTest(status=status), self.assertRaises(expected):
                await provider(FakeTransport([
                    PronunciationHTTPResponse(status, b"provider secret")
                ])).list()
        for error in (AITimeoutError("secret"), AIConnectionError("secret")):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                await provider(FakeTransport(error=error)).list()

    async def test_malformed_and_missing_response_fields_are_rejected(self):
        responses = (
            PronunciationHTTPResponse(200, b"not json"),
            PronunciationHTTPResponse(200, b"{}"),
            PronunciationHTTPResponse(200, b'{"dictionary_id":""}'),
        )
        for response in responses:
            with self.subTest(response=response), self.assertRaises(AIInvalidResponseError):
                await provider(FakeTransport([response])).create(
                    {"pronunciations": {"en-IN": {"AI": "A I"}}}
                )


if __name__ == "__main__":
    unittest.main()
