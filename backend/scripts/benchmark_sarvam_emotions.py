"""Developer-only listening benchmark for Bulbul v3 emotion profiles."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

BACKEND_DIRECTORY = Path(__file__).resolve().parents[1]
if str(BACKEND_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIRECTORY))

from app.config import get_settings
from app.services.ai.sarvam_speech_provider import SarvamBulbulProvider
from app.services.pronunciation_dictionary_service import (
    PronunciationDictionaryResolver,
)
from app.services.speech_emotion_analyzer import (
    EmotionConfidence,
    SpeechEmotion,
    SpeechEmotionAnalysis,
)
from app.services.speech_language_analyzer import SpeechLanguageMode
from app.services.speech_prosody_planner import SpeechProsodyPlanner
from app.services.voice_profile_resolver import StandardVoiceProfile


BENCHMARK_EMOTIONS = (
    SpeechEmotion.NEUTRAL,
    SpeechEmotion.WARM,
    SpeechEmotion.NOSTALGIC,
    SpeechEmotion.SAD,
    SpeechEmotion.JOYFUL,
    SpeechEmotion.EXCITED,
    SpeechEmotion.SERIOUS,
    SpeechEmotion.ANGRY,
)
LANGUAGE_MODES = {
    "en-IN": SpeechLanguageMode.ENGLISH,
    "hi-IN": SpeechLanguageMode.HINDI_DEVANAGARI,
    "mr-IN": SpeechLanguageMode.MARATHI_DEVANAGARI,
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate local Sarvam emotion-profile WAV comparisons."
    )
    parser.add_argument("--language", choices=tuple(LANGUAGE_MODES), required=True)
    parser.add_argument(
        "--voice-profile",
        choices=tuple(profile.value for profile in StandardVoiceProfile),
        required=True,
    )
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


async def run(args):
    output = Path(args.output_dir).resolve()
    local_root = (BACKEND_DIRECTORY / ".local").resolve()
    if output != local_root and local_root not in output.parents:
        raise ValueError("Benchmark output must be inside backend/.local.")
    fixture = json.loads(Path(args.text_file).read_text(encoding="utf-8"))
    text = fixture.get("text")
    if fixture.get("language_code") != args.language or not isinstance(text, str):
        raise ValueError("Benchmark fixture language or text is invalid.")
    settings = get_settings()
    provider = SarvamBulbulProvider(
        api_key=settings.sarvam_api_key,
        model=settings.sarvam_model,
        male_speaker=settings.sarvam_speaker_male,
        female_speaker=settings.sarvam_speaker_female,
        output_format="wav",
        timeout_seconds=settings.sarvam_timeout_seconds,
        max_audio_bytes=settings.sarvam_max_audio_bytes,
        pace=settings.sarvam_pace,
        temperature=settings.sarvam_temperature,
    )
    dictionary_id = PronunciationDictionaryResolver(
        settings.sarvam_pronunciation_dictionary_id,
        required=False,
    ).resolve(language_code=args.language)
    planner = SpeechProsodyPlanner()
    output.mkdir(parents=True, exist_ok=True)
    for emotion in BENCHMARK_EMOTIONS:
        plan = planner.plan(
            canonical_text=text,
            language_mode=LANGUAGE_MODES[args.language],
            analysis=SpeechEmotionAnalysis(emotion, EmotionConfidence.MEDIUM),
            enabled=True,
        )
        started = time.monotonic()
        result = await provider.synthesize(
            text=plan.provider_text,
            standard_voice_profile=StandardVoiceProfile(args.voice_profile),
            language_code=args.language,
            dictionary_id=dictionary_id,
            pace=plan.pace,
            temperature=plan.temperature,
        )
        destination = output / f"{emotion.value}.wav"
        destination.write_bytes(result.content)
        print(
            f"{emotion.value}: elapsed_ms="
            f"{int((time.monotonic() - started) * 1000)}, "
            f"audio_bytes={len(result.content)}"
        )


def main():
    try:
        asyncio.run(run(parse_args()))
    except Exception as exc:
        print(f"Emotion benchmark failed: {getattr(exc, 'code', type(exc).__name__)}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
