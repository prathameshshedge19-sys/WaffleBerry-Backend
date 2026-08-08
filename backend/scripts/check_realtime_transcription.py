"""Safely probe OpenAI realtime transcription setup without sending user audio."""

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services.ai.realtime_transcription_provider import (
    RealtimeTranscriptionConnectionError,
    RealtimeTranscriptionSession,
)


async def probe() -> int:
    settings = get_settings()
    configured = bool(settings.openai_api_key and settings.live_call_transcription_model)
    print(f"configured={str(configured).lower()}")
    if not configured:
        print("connection=false")
        print("session_ready=false")
        print("safe_error_category=auth_failed")
        print("status_code=na")
        return 1
    try:
        session = await RealtimeTranscriptionSession.create(
            api_key=settings.openai_api_key,
            model=settings.live_call_transcription_model,
        )
    except RealtimeTranscriptionConnectionError as exc:
        print("connection=false")
        print("session_ready=false")
        print(f"safe_error_category={exc.safe_category}")
        print(f"status_code={exc.status_code if exc.status_code is not None else 'na'}")
        return 1
    print("connection=true")
    print("session_ready=true")
    print("safe_error_category=none")
    print("status_code=na")
    await session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(probe()))
