"""OpenAI Realtime WebSocket adapter for one-shot stored-text rendering."""

import asyncio
import base64
import binascii
import io
import json
import logging
import wave
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlencode

from app.services.ai.exceptions import (
    AIAuthenticationError,
    AIConfigurationError,
    AIConnectionError,
    AIInvalidResponseError,
    AIProviderError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)
from app.services.ai.provider import SpeechResult
from app.services.speech_fidelity_comparator import SpeechFidelityComparator


REALTIME_URL = "wss://api.openai.com/v1/realtime"
REALTIME_PCM_RATE = 24_000
REALTIME_MAX_EVENTS = 20_000
logger = logging.getLogger(__name__)


class RealtimeConnection(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


Connector = Callable[..., Awaitable[RealtimeConnection]]


class RealtimeSpeechProvider:
    """Collect one audio-only Realtime response into a playable WAV."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        timeout_seconds: float,
        max_audio_bytes: int,
        output_format: str,
        debug: bool = False,
        fidelity_comparator: SpeechFidelityComparator | None = None,
        connector: Connector | None = None,
    ) -> None:
        self._api_key = self._required(api_key, "OPENAI_API_KEY")
        self._model = self._required(model, "OPENAI_REALTIME_MODEL")
        if timeout_seconds <= 0:
            raise AIConfigurationError(
                "OPENAI_REALTIME_TIMEOUT_SECONDS must be positive."
            )
        if max_audio_bytes <= 0:
            raise AIConfigurationError(
                "OPENAI_REALTIME_MAX_AUDIO_BYTES must be positive."
            )
        if output_format.strip().lower() != "audio/pcm":
            raise AIConfigurationError(
                "OPENAI_REALTIME_OUTPUT_FORMAT must be audio/pcm."
            )
        self._timeout = timeout_seconds
        self._max_audio_bytes = max_audio_bytes
        self._debug = debug
        self._fidelity = fidelity_comparator or SpeechFidelityComparator()
        self._connector = connector or self._connect

    async def synthesize(
        self,
        *,
        text: str,
        voice: str,
        instructions: str,
    ) -> SpeechResult:
        """Render supplied text without exposing Realtime protocol objects."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Realtime speech text must not be blank.")
        connection = None
        try:
            async with asyncio.timeout(self._timeout):
                connection = await self._connector(
                    url=f"{REALTIME_URL}?{urlencode({'model': self._model})}",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout_seconds=self._timeout,
                )
                await self._await_session_created(connection)
                await self._send_request(
                    connection,
                    text=text,
                    voice=voice,
                    instructions=instructions,
                )
                pcm = await self._collect_audio(connection, expected_text=text)
        except TimeoutError:
            raise AITimeoutError("Realtime speech generation timed out.") from None
        except (AIProviderError, AIInvalidResponseError):
            raise
        except (OSError, ConnectionError):
            raise AIConnectionError("OpenAI Realtime could not be reached.") from None
        except Exception as exc:
            if exc.__class__.__module__.startswith("websockets"):
                raise AIConnectionError(
                    "OpenAI Realtime connection failed."
                ) from None
            raise AIInvalidResponseError(
                "OpenAI Realtime returned an unreadable response."
            ) from None
        finally:
            if connection is not None:
                try:
                    await connection.close()
                except Exception:
                    pass

        if not pcm:
            raise AIInvalidResponseError("OpenAI Realtime returned empty audio.")
        return SpeechResult(
            content=self._pcm_to_wav(pcm),
            media_type="audio/wav",
            file_extension="wav",
        )

    async def _send_request(
        self,
        connection: RealtimeConnection,
        *,
        text: str,
        voice: str,
        instructions: str,
    ) -> None:
        await self._send(
            connection,
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "model": self._model,
                    "output_modalities": ["audio"],
                    "audio": {
                        "output": {
                            "format": {
                                "type": "audio/pcm",
                                "rate": REALTIME_PCM_RATE,
                            },
                            "voice": voice,
                        }
                    },
                },
            },
        )
        await self._send(
            connection,
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            },
        )
        await self._send(
            connection,
            {
                "type": "response.create",
                "response": {
                    "output_modalities": ["audio"],
                    "audio": {
                        "output": {
                            "format": {
                                "type": "audio/pcm",
                                "rate": REALTIME_PCM_RATE,
                            },
                            "voice": voice,
                        }
                    },
                    "instructions": instructions,
                },
            },
        )

    async def _await_session_created(
        self,
        connection: RealtimeConnection,
    ) -> None:
        raw = await connection.recv()
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            raise AIInvalidResponseError(
                "OpenAI Realtime returned a malformed session event."
            ) from None
        if isinstance(event, dict) and event.get("type") == "error":
            self._raise_event_error(
                event,
                last_event_type="websocket.connected",
            )
        if not isinstance(event, dict) or event.get("type") != "session.created":
            raise AIInvalidResponseError(
                "OpenAI Realtime did not initialize the session."
            )

    async def _collect_audio(
        self,
        connection: RealtimeConnection,
        *,
        expected_text: str,
    ) -> bytes:
        chunks = bytearray()
        transcript = None
        last_event_type = "session.created"
        for _ in range(REALTIME_MAX_EVENTS):
            raw = await connection.recv()
            try:
                event = json.loads(raw)
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                raise AIInvalidResponseError(
                    "OpenAI Realtime returned a malformed event."
                ) from None
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise AIInvalidResponseError(
                    "OpenAI Realtime returned a malformed event."
                )
            event_type = event["type"]
            if event_type == "response.output_audio.delta":
                delta = event.get("delta")
                if not isinstance(delta, str):
                    raise AIInvalidResponseError(
                        "OpenAI Realtime returned malformed audio."
                    )
                try:
                    decoded = base64.b64decode(delta, validate=True)
                except (binascii.Error, ValueError):
                    raise AIInvalidResponseError(
                        "OpenAI Realtime returned malformed audio."
                    ) from None
                if len(chunks) + len(decoded) > self._max_audio_bytes:
                    raise AIInvalidResponseError(
                        "OpenAI Realtime audio exceeded the configured maximum."
                    )
                chunks.extend(decoded)
                last_event_type = event_type
            elif event_type == "response.output_audio_transcript.done":
                transcript = event.get("transcript")
                if not isinstance(transcript, str):
                    raise AIInvalidResponseError(
                        "OpenAI Realtime returned a malformed transcript."
                    )
                last_event_type = event_type
            elif event_type == "error":
                self._raise_event_error(
                    event,
                    sensitive_text=expected_text,
                    last_event_type=last_event_type,
                )
            elif event_type == "response.done":
                response = event.get("response")
                if not isinstance(response, dict) or response.get("status") != "completed":
                    self._log_response_failure(
                        response,
                        sensitive_text=expected_text,
                        last_event_type=last_event_type,
                    )
                    raise AIProviderError(
                        "OpenAI Realtime did not complete speech generation."
                    )
                if transcript is not None and not self._fidelity.equivalent(
                    expected_text,
                    transcript,
                ):
                    raise AIInvalidResponseError(
                        "OpenAI Realtime did not preserve the supplied text."
                    )
                return bytes(chunks)
            else:
                last_event_type = event_type
        raise AIInvalidResponseError(
            "OpenAI Realtime exceeded the event safety limit."
        )

    @staticmethod
    async def _send(connection: RealtimeConnection, event: dict[str, Any]) -> None:
        await connection.send(json.dumps(event, ensure_ascii=False))

    def _raise_event_error(
        self,
        event: dict[str, Any],
        *,
        sensitive_text: str | None = None,
        last_event_type: str,
    ) -> None:
        error = event.get("error")
        error = error if isinstance(error, dict) else {}
        code = error.get("code", "")
        self._log_debug_diagnostic(
            server_event_type=event.get("type"),
            error_type=error.get("type"),
            error_code=code,
            parameter=error.get("param"),
            provider_message=error.get("message"),
            response_status=None,
            response_status_details=None,
            last_event_type=last_event_type,
            sensitive_text=sensitive_text,
        )
        if code in {"invalid_api_key", "model_not_found", "permission_denied"}:
            raise AIAuthenticationError("OpenAI Realtime access was rejected.")
        if code in {"rate_limit_exceeded", "tokens_exceeded"}:
            raise AIRateLimitError("OpenAI Realtime rate limited the request.")
        if code in {"server_error", "service_unavailable"}:
            raise AIProviderUnavailableError(
                "OpenAI Realtime is temporarily unavailable."
            )
        raise AIProviderError("OpenAI Realtime rejected the request.")

    def _log_response_failure(
        self,
        response: object,
        *,
        sensitive_text: str,
        last_event_type: str,
    ) -> None:
        response = response if isinstance(response, dict) else {}
        self._log_debug_diagnostic(
            server_event_type="response.done",
            error_type=None,
            error_code=None,
            parameter=None,
            provider_message=None,
            response_status=response.get("status"),
            response_status_details=response.get("status_details"),
            last_event_type=last_event_type,
            sensitive_text=sensitive_text,
        )

    def _log_debug_diagnostic(
        self,
        *,
        server_event_type: object,
        error_type: object,
        error_code: object,
        parameter: object,
        provider_message: object,
        response_status: object,
        response_status_details: object,
        last_event_type: object,
        sensitive_text: str | None,
    ) -> None:
        if not self._debug:
            return
        secrets = (self._api_key, sensitive_text)
        logger.warning(
            "Realtime provider diagnostic "
            "(server_event_type=%s, error_type=%s, error_code=%s, "
            "parameter=%s, provider_message=%s, response_status=%s, "
            "response_status_details=%s, last_event_type=%s).",
            self._safe_value(server_event_type, secrets),
            self._safe_value(error_type, secrets),
            self._safe_value(error_code, secrets),
            self._safe_value(parameter, secrets),
            self._safe_value(provider_message, secrets),
            self._safe_value(response_status, secrets),
            self._safe_value(response_status_details, secrets),
            self._safe_value(last_event_type, secrets),
        )

    @staticmethod
    def _safe_value(value: object, secrets: tuple[str | None, ...]) -> str:
        if value is None:
            return "none"
        if isinstance(value, (dict, list, tuple)):
            rendered = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        else:
            rendered = str(value)
        rendered = rendered.replace("\r", " ").replace("\n", " ")
        for secret in secrets:
            if isinstance(secret, str) and secret:
                rendered = rendered.replace(secret, "[redacted]")
        return rendered[:240]

    @staticmethod
    def _pcm_to_wav(pcm: bytes) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(REALTIME_PCM_RATE)
            wav.writeframes(pcm)
        return output.getvalue()

    async def _connect(
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> RealtimeConnection:
        import websockets

        return await websockets.connect(
            url,
            additional_headers=headers,
            open_timeout=timeout_seconds,
            close_timeout=5,
            max_size=2 * 1024 * 1024,
        )

    @staticmethod
    def _required(value: str | None, name: str) -> str:
        normalized = value.strip() if isinstance(value, str) else ""
        if not normalized:
            raise AIConfigurationError(f"{name} must be configured.")
        return normalized
