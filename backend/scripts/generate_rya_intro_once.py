"""Development-only generator for the static homepage Rya introduction."""

import argparse
import hashlib
from pathlib import Path

from openai import OpenAI

from app.config import get_settings
from app.services.speech_pronunciation import normalize_for_companion_speech


DISPLAY_TEXT = "I'm Rya. Tell me what should never be forgotten, and together, we'll preserve it."
SPEECH_TEXT = normalize_for_companion_speech(DISPLAY_TEXT)
GENERATION_TEXT = SPEECH_TEXT.replace("Riya", "Ree-yah", 1)
INSTRUCTIONS = (
    "Speak calmly, warmly, and intimately with premium, slightly mysterious, emotionally grounded delivery. "
    "Pronounce the name Riya clearly as two syllables: REE-yah, with an audible consonant y glide before 'ah'. "
    "It must sound like the name Riya, not Rhea, Ria, Raya, or a single-syllable word. "
    "Make the transition from 'I'm' to 'Riya' natural. "
    "Use natural pacing and soft confidence. Do not sound theatrical, overly cheerful, robotic, or rushed. "
    "Speak like a timeless AI presence speaking quietly to one person."
)
OUTPUT = Path(__file__).resolve().parents[3] / "Legarya Frontend" / "assets" / "audio" / "rya-intro.mp3"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Replace the existing static intro asset.")
    args = parser.parse_args()
    if OUTPUT.exists() and not args.force:
        raise RuntimeError("Refusing to regenerate: the static Rya intro already exists. Pass --force intentionally.")
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OpenAI is not configured for the one-time development generation.")
    response = OpenAI(api_key=settings.openai_api_key).audio.speech.create(
        model="gpt-4o-mini-tts",
        voice="marin",
        input=GENERATION_TEXT,
        instructions=INSTRUCTIONS,
        response_format="mp3",
    )
    temporary = OUTPUT.with_suffix(".replacement.mp3")
    response.write_to_file(temporary)
    temporary.replace(OUTPUT)
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"generated={OUTPUT} bytes={OUTPUT.stat().st_size} sha256={digest}")
    print(f"hidden_tts_text={GENERATION_TEXT}")


if __name__ == "__main__":
    main()
