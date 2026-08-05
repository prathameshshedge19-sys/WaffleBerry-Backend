"""Contract tests for the documented OpenAI Realtime event flow."""

import asyncio
import base64
import json
import unittest

from app.services.ai.exceptions import (
    AIAuthenticationError,
    AIInvalidResponseError,
    AITimeoutError,
)
from app.services.ai.realtime_speech_provider import RealtimeSpeechProvider


class FakeConnection:
    def __init__(self, events):
        self.events = list(events)
        self.sent = []
        self.closed = False

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        if not self.events:
            await asyncio.sleep(3600)
        event = self.events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event if isinstance(event, str) else json.dumps(event)

    async def close(self):
        self.closed = True


class RealtimeSpeechProviderTests(unittest.IsolatedAsyncioTestCase):
    def provider(self, connection, **overrides):
        async def connect(**kwargs):
            self.connect_args = kwargs
            return connection

        values = {
            "api_key": "secret-key",
            "model": "gpt-realtime-2.1",
            "timeout_seconds": 1,
            "max_audio_bytes": 1024,
            "output_format": "audio/pcm",
            "connector": connect,
        }
        values.update(overrides)
        return RealtimeSpeechProvider(**values)

    async def test_documented_events_forward_text_voice_and_instructions(self):
        pcm = b"\x01\x02\x03\x04"
        connection = FakeConnection(
            [
                {"type": "session.created"},
                {
                    "type": "response.output_audio.delta",
                    "delta": base64.b64encode(pcm[:2]).decode(),
                },
                {
                    "type": "response.output_audio.delta",
                    "delta": base64.b64encode(pcm[2:]).decode(),
                },
                {
                    "type": "response.output_audio_transcript.done",
                    "transcript": "Exact stored text",
                },
                {"type": "response.done", "response": {"status": "completed"}},
            ]
        )
        result = await self.provider(connection).synthesize(
            text="Exact stored text",
            voice="marin",
            instructions="Speak faithfully.",
        )

        self.assertEqual(
            [event["type"] for event in connection.sent],
            ["session.update", "conversation.item.create", "response.create"],
        )
        session = connection.sent[0]["session"]
        self.assertEqual(session["model"], "gpt-realtime-2.1")
        self.assertEqual(session["output_modalities"], ["audio"])
        self.assertEqual(session["audio"]["output"]["voice"], "marin")
        self.assertEqual(
            session["audio"]["output"]["format"],
            {"type": "audio/pcm", "rate": 24000},
        )
        item = connection.sent[1]["item"]
        self.assertEqual(item["content"], [{"type": "input_text", "text": "Exact stored text"}])
        self.assertEqual(connection.sent[2]["response"]["instructions"], "Speak faithfully.")
        self.assertEqual(
            connection.sent[2]["response"]["audio"]["output"]["format"],
            {"type": "audio/pcm", "rate": 24000},
        )
        self.assertEqual(result.content[:4], b"RIFF")
        self.assertEqual(result.content[8:12], b"WAVE")
        self.assertEqual(result.content[44:], pcm)
        self.assertEqual(result.media_type, "audio/wav")
        self.assertEqual(result.file_extension, "wav")
        self.assertTrue(connection.closed)
        self.assertIn("model=gpt-realtime-2.1", self.connect_args["url"])
        self.assertEqual(
            self.connect_args["headers"]["Authorization"], "Bearer secret-key"
        )

    async def test_malformed_base64_empty_audio_and_fidelity_change_fail(self):
        cases = (
            [
                {"type": "session.created"},
                {"type": "response.output_audio.delta", "delta": "%%%"},
            ],
            [
                {"type": "session.created"},
                {"type": "response.done", "response": {"status": "completed"}},
            ],
            [
                {"type": "session.created"},
                {
                    "type": "response.output_audio.delta",
                    "delta": base64.b64encode(b"12").decode(),
                },
                {
                    "type": "response.output_audio_transcript.done",
                    "transcript": "Changed text",
                },
                {"type": "response.done", "response": {"status": "completed"}},
            ],
        )
        for events in cases:
            with self.subTest(events=events):
                connection = FakeConnection(events)
                with self.assertRaises(AIInvalidResponseError):
                    await self.provider(connection).synthesize(
                        text="Exact text", voice="cedar", instructions="Exact"
                    )
                self.assertTrue(connection.closed)

    async def test_size_limit_provider_error_and_timeout_close_connection(self):
        too_large = FakeConnection(
            [{"type": "session.created"}, {
                "type": "response.output_audio.delta",
                "delta": base64.b64encode(b"123").decode(),
            }]
        )
        with self.assertRaises(AIInvalidResponseError):
            await self.provider(too_large, max_audio_bytes=2).synthesize(
                text="Text", voice="cedar", instructions="Exact"
            )
        self.assertTrue(too_large.closed)

        rejected = FakeConnection(
            [{"type": "error", "error": {"code": "model_not_found"}}]
        )
        with self.assertRaises(AIAuthenticationError):
            await self.provider(rejected).synthesize(
                text="Text", voice="cedar", instructions="Exact"
            )
        self.assertTrue(rejected.closed)

        stalled = FakeConnection([])
        with self.assertRaises(AITimeoutError):
            await self.provider(stalled, timeout_seconds=0.01).synthesize(
                text="Text", voice="cedar", instructions="Exact"
            )
        self.assertTrue(stalled.closed)

    async def test_debug_logs_sanitized_error_fields_without_secrets(self):
        message = "PRIVATE STORED MESSAGE"
        api_key = "sk-private-key"
        connection = FakeConnection(
            [
                {"type": "session.created"},
                {"type": "session.updated"},
                {
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "code": "invalid_value",
                        "param": "session.audio.output.format.type",
                        "message": (
                            "Unsupported value; do not reveal "
                            f"{api_key} or {message}"
                        ),
                    },
                },
            ]
        )
        with self.assertLogs(
            "app.services.ai.realtime_speech_provider",
            level="DEBUG",
        ) as captured:
            with self.assertRaises(Exception):
                await self.provider(
                    connection,
                    api_key=api_key,
                    debug=True,
                ).synthesize(
                    text=message,
                    voice="marin",
                    instructions="Speak exactly.",
                )
        output = " ".join(captured.output)
        self.assertIn("server_event_type=error", output)
        self.assertIn("error_type=invalid_request_error", output)
        self.assertIn("error_code=invalid_value", output)
        self.assertIn("parameter=session.audio.output.format.type", output)
        self.assertIn("last_event_type=session.updated", output)
        self.assertIn("[redacted]", output)
        self.assertNotIn(api_key, output)
        self.assertNotIn(message, output)

    async def test_non_debug_does_not_log_provider_details(self):
        connection = FakeConnection(
            [
                {"type": "session.created"},
                {
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "code": "secret-code",
                        "param": "secret-param",
                        "message": "secret-provider-message",
                    },
                },
            ]
        )
        with self.assertNoLogs(
            "app.services.ai.realtime_speech_provider",
            level="DEBUG",
        ):
            with self.assertRaises(Exception):
                await self.provider(connection, debug=False).synthesize(
                    text="private message",
                    voice="marin",
                    instructions="Speak exactly.",
                )

    async def test_debug_logs_sanitized_unsuccessful_response_status(self):
        connection = FakeConnection(
            [
                {"type": "session.created"},
                {"type": "response.created"},
                {
                    "type": "response.done",
                    "response": {
                        "status": "failed",
                        "status_details": {
                            "type": "failed",
                            "error": {"code": "audio_generation_failed"},
                        },
                    },
                },
            ]
        )
        with self.assertLogs(
            "app.services.ai.realtime_speech_provider",
            level="DEBUG",
        ) as captured:
            with self.assertRaises(Exception):
                await self.provider(connection, debug=True).synthesize(
                    text="private message",
                    voice="marin",
                    instructions="Speak exactly.",
                )
        output = " ".join(captured.output)
        self.assertIn("server_event_type=response.done", output)
        self.assertIn("response_status=failed", output)
        self.assertIn("audio_generation_failed", output)
        self.assertIn("last_event_type=response.created", output)
        self.assertNotIn("private message", output)


if __name__ == "__main__":
    unittest.main()
