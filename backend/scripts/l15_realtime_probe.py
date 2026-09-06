"""Explicit, disposable real-provider acceptance. Never connects to a database.

Run from backend: python -B scripts/l15_realtime_probe.py --audio <PCM WAV>
Only sanitized event names, counts, configuration and numerical usage are saved.
"""
import argparse
import asyncio
import json
import sys
import wave
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.services.realtime_provider import RealOpenAIRealtimeProvider
from app.services.realtime_sessions import RealtimeError


async def probe(voice, vad, pcm):
    provider = RealOpenAIRealtimeProvider()
    result = {"voice": voice, "vad": vad, "model": provider.settings.realtime_model,
              "transcription_model": provider.settings.realtime_transcription_model,
              "connected": False, "events": {}, "stages": {}, "usage": []}
    counts = Counter()

    async def collect(stage, until, timeout=20, cancel_on_created=False):
        events = []
        async with asyncio.timeout(timeout):
            while True:
                event = await provider.receive()
                if not event:
                    continue
                counts[event.kind] += 1
                events.append(event.kind)
                if event.kind == "error" or event.kind == "input_transcript_failed":
                    raise RealtimeError("realtime_provider_failed")
                if event.kind == "response_created" and cancel_on_created:
                    await provider.cancel()
                if event.kind == "response_done":
                    response = event.payload.get("response", {})
                    result["usage"].append({"stage": stage, "status": response.get("status"),
                        "usage": safe_numbers(response.get("usage", {}))})
                if event.kind == until:
                    result["stages"][stage] = events
                    return

    try:
        await provider.connect(voice, 1, vad=vad)
        result["connected"] = True
        config = provider.accepted_configuration
        result["configuration"] = {"model": config.get("model"), "audio": config.get("audio"),
                                    "reasoning": config.get("reasoning"), "output_modalities": config.get("output_modalities")}
        # The fixture contains only a disposable connection-test sentence.
        async def stream_input():
            for offset in range(0, len(pcm), 2400):
                await provider.append_audio(pcm[offset:offset+2400])
                await asyncio.sleep(.05)
            # Semantic detection measures the continuing input stream. A stopped
            # microphone is not equivalent to receiving continued silence.
            for _ in range(240):
                await provider.append_audio(bytes(2400))
                await asyncio.sleep(.05)
        pump = asyncio.create_task(stream_input())
        try:
            await collect("transcription", "input_transcript_done", timeout=30)
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        result["autonomous_response"] = counts["response_created"] > 0
        await provider.probe_response()
        await collect("response", "response_done")
        await provider.probe_response()
        await collect("cancel", "response_done", cancel_on_created=True)
        await provider.probe_response(tool=True)
        await collect("function", "response_done")
        result["passed"] = (not result["autonomous_response"] and counts["audio"] > 0
            and counts["output_transcript_done"] > 0 and counts["function_done"] > 0
            and any(u["stage"] == "cancel" and u["status"] == "cancelled" for u in result["usage"]))
    except Exception as error:
        result["passed"] = False
        result["error"] = error.code if isinstance(error, RealtimeError) else "probe_timeout" if isinstance(error, TimeoutError) else "probe_failed"
    finally:
        await provider.close()
    result["events"] = dict(counts)
    return result


def safe_numbers(value):
    if isinstance(value, dict):
        allowed = {"input_tokens", "output_tokens", "total_tokens", "input_token_details", "output_token_details",
                   "audio_tokens", "text_tokens", "cached_tokens", "cached_tokens_details", "reasoning_tokens", "image_tokens"}
        return {k: safe_numbers(v) for k, v in value.items() if k in allowed}
    return value if type(value) in {int, float} else None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", default="docs/L15_PROVIDER_ACCEPTANCE.json")
    parser.add_argument("--voice", choices=["marin", "cedar"])
    args = parser.parse_args()
    with wave.open(args.audio, "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 24000:
            raise ValueError("Probe requires mono PCM16 WAV at 24 kHz")
        # Streaming WAV may advertise an unknown RIFF length; bound actual bytes.
        pcm = wav.readframes(24000 * 15 + 1)
        if not pcm or len(pcm) > 24000 * 15 * 2:
            raise ValueError("Probe audio must be nonempty and at most 15 seconds")
    results = []
    for voice, vad in (("marin", "semantic_vad"), ("cedar", "server_vad")):
        if args.voice and voice != args.voice:
            continue
        result = await probe(voice, vad, pcm)
        results.append(result)
        print(json.dumps(result), flush=True)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0 if all(x.get("passed") for x in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
