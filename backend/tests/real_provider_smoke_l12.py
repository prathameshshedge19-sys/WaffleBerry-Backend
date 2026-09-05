"""Minimum real-provider smoke for L12. Run explicitly; never part of pytest."""

import asyncio

from app.services.voice import OpenAIVoiceProvider


async def main() -> None:
    provider = OpenAIVoiceProvider()
    cases = (
        ("english", "My mother loved jasmine flowers.", "marin"),
        ("marathi_mixed", "माझ्या आईला jasmine ची फुले आवडायची.", "cedar"),
    )
    for name, source, voice in cases:
        audio = await provider.synthesize(source, voice)
        transcript = await provider.transcribe(audio, f"{name}.mp3", "audio/mpeg")
        print(f"{name}: voice={voice} bytes={len(audio)} transcript={transcript}")


if __name__ == "__main__":
    asyncio.run(main())
