"""OpenAI Realtime adapter for one explicit-VAD transcription turn."""

import asyncio
import base64
import json

from app.services.ai.exceptions import AIConnectionError, AIInvalidResponseError


REALTIME_TRANSCRIPTION_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
REALTIME_TRANSCRIPTION_SAMPLE_RATE = 24_000
MAX_REALTIME_TRANSCRIPTION_EVENTS = 20_000
SAFE_REALTIME_CONNECTION_ERRORS = frozenset({
    "auth_failed", "endpoint_not_found", "model_not_supported",
    "handshake_rejected", "session_config_rejected", "protocol_error",
    "timeout", "sdk_incompatible", "unknown_connection_error",
})


class RealtimeTranscriptionConnectionError(AIConnectionError):
    """Realtime setup failure containing only allowlisted diagnostic metadata."""

    def __init__(self, category: str, status_code: int | None = None) -> None:
        self.safe_category = (
            category if category in SAFE_REALTIME_CONNECTION_ERRORS
            else "unknown_connection_error"
        )
        self.status_code = status_code if isinstance(status_code, int) else None
        super().__init__("Realtime transcription could not be started.")


def classify_realtime_connection_error(exc: Exception) -> tuple[str, int | None]:
    """Map provider/transport exceptions without exposing messages or response bodies."""
    if isinstance(exc, RealtimeTranscriptionConnectionError):
        return exc.safe_category, exc.status_code
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout", None
    if isinstance(exc, (ImportError, AttributeError, TypeError)):
        return "sdk_incompatible", None
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "status_code", None)
    if status in {401, 403}:
        return "auth_failed", status
    if status == 404:
        return "endpoint_not_found", status
    if isinstance(status, int):
        return "handshake_rejected", status
    if isinstance(exc, (ConnectionError, OSError)):
        return "handshake_rejected", None
    return "unknown_connection_error", None


class RealtimeTranscriptionSession:
    def __init__(self, connection, *, model: str) -> None:
        self._connection = connection
        self._model = model
        self._partial = ""
        self._closed = False

    @classmethod
    async def create(cls, *, api_key: str, model: str, connector=None):
        connect = connector or cls._connect
        try:
            connection = await connect(
                url=REALTIME_TRANSCRIPTION_URL,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            session = cls(connection, model=model)
            await session._await_created()
            await session._send({
                "type": "session.update",
                "session": {
                    "type": "transcription",
                    "audio": {"input": {
                        "format": {"type": "audio/pcm", "rate": REALTIME_TRANSCRIPTION_SAMPLE_RATE},
                        "transcription": {"model": model, "delay": "low"},
                        "turn_detection": None,
                    }},
                },
            })
            await session._await_configured()
            return session
        except Exception as exc:
            category, status_code = classify_realtime_connection_error(exc)
            raise RealtimeTranscriptionConnectionError(category, status_code) from None

    async def append_audio(self, chunk: bytes) -> str | None:
        if self._closed or not chunk or len(chunk) % 2:
            raise AIInvalidResponseError("Realtime transcription audio was invalid.")
        await self._send({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(chunk).decode("ascii"),
        })
        return await self._drain_available_delta()

    async def finalize(self) -> str:
        await self._send({"type": "input_audio_buffer.commit"})
        for _ in range(MAX_REALTIME_TRANSCRIPTION_EVENTS):
            event = await self._receive()
            event_type = event.get("type")
            if event_type == "conversation.item.input_audio_transcription.delta":
                delta = event.get("delta")
                if isinstance(delta, str):
                    self._partial += delta
            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = event.get("transcript")
                if isinstance(transcript, str) and transcript.strip():
                    return transcript.strip()
                raise AIInvalidResponseError("Realtime transcription returned no final text.")
            elif event_type in {"error", "conversation.item.input_audio_transcription.failed"}:
                raise AIInvalidResponseError("Realtime transcription failed.")
        raise AIInvalidResponseError("Realtime transcription did not complete.")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._connection.close()
        except Exception:
            pass

    async def _drain_available_delta(self) -> str | None:
        combined = ""
        while True:
            try:
                async with asyncio.timeout(0.001):
                    event = await self._receive()
            except TimeoutError:
                break
            if event.get("type") == "conversation.item.input_audio_transcription.delta":
                delta = event.get("delta")
                if isinstance(delta, str):
                    self._partial += delta
                    combined += delta
            elif event.get("type") in {"error", "conversation.item.input_audio_transcription.failed"}:
                raise AIInvalidResponseError("Realtime transcription failed.")
        return combined or None

    async def _await_created(self) -> None:
        for _ in range(20):
            event = await self._receive()
            if event.get("type") == "session.created":
                return
            if event.get("type") == "error":
                error = event.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                category = "model_not_supported" if code in {
                    "model_not_found", "invalid_model", "model_not_supported",
                } else "protocol_error"
                raise RealtimeTranscriptionConnectionError(category)
        raise RealtimeTranscriptionConnectionError("protocol_error")

    async def _await_configured(self) -> None:
        for _ in range(20):
            event = await self._receive()
            if event.get("type") == "session.updated":
                return
            if event.get("type") == "error":
                error = event.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                category = "model_not_supported" if code in {
                    "model_not_found", "invalid_model", "model_not_supported",
                } else "session_config_rejected"
                raise RealtimeTranscriptionConnectionError(category)
        raise RealtimeTranscriptionConnectionError("protocol_error")

    async def _send(self, event: dict) -> None:
        await self._connection.send(json.dumps(event, separators=(",", ":")))

    async def _receive(self) -> dict:
        payload = await self._connection.recv()
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        event = json.loads(payload)
        if not isinstance(event, dict):
            raise AIInvalidResponseError("Realtime transcription event was invalid.")
        return event

    @staticmethod
    async def _connect(*, url: str, headers: dict[str, str]):
        import websockets
        return await websockets.connect(
            url, additional_headers=headers, open_timeout=10,
            close_timeout=5, max_size=2 * 1024 * 1024,
        )
