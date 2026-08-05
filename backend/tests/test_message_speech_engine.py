"""Engine-selection, fidelity, and fallback tests for stored-message speech."""

import unittest

from app.services.ai.exceptions import (
    AIConfigurationError,
    AIConnectionError,
    AIInvalidResponseError,
)
from app.services.ai.message_speech_engine import ConfiguredMessageSpeechEngine
from app.services.ai.provider import SpeechResult
from app.services.ai.realtime_speech_service import (
    FIDELITY_INSTRUCTIONS,
    RealtimeSpeechService,
)
from app.services.voice_profile_resolver import StandardVoiceProfile


class FakeEngine:
    def __init__(self, error=None, extension="mp3"):
        self.calls = []
        self.error = error
        self.extension = extension

    async def synthesize(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SpeechResult(
            b"audio", "audio/wav" if self.extension == "wav" else "audio/mpeg", self.extension
        )


class MessageSpeechEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_tts_and_realtime_selection(self):
        for selected in ("tts", "realtime"):
            with self.subTest(selected=selected):
                tts, realtime = FakeEngine(), FakeEngine(extension="wav")
                engine = ConfiguredMessageSpeechEngine(
                    selected_engine=selected,
                    tts_engine=tts,
                    realtime_engine=realtime,
                )
                await engine.synthesize(
                    text="Stored", standard_voice_profile=StandardVoiceProfile.FEMALE,
                    response_format="mp3",
                )
                self.assertEqual(len(tts.calls), int(selected == "tts"))
                self.assertEqual(len(realtime.calls), int(selected == "realtime"))

    async def test_eligible_failure_falls_back_once_and_can_be_disabled(self):
        tts = FakeEngine()
        realtime = FakeEngine(AIConnectionError("offline"))
        engine = ConfiguredMessageSpeechEngine(
            selected_engine="realtime", tts_engine=tts,
            realtime_engine=realtime, fallback_to_tts=True,
        )
        await engine.synthesize(
            text="Stored", standard_voice_profile=StandardVoiceProfile.MALE,
            response_format="mp3",
        )
        self.assertEqual(len(realtime.calls), 1)
        self.assertEqual(len(tts.calls), 1)

        disabled = ConfiguredMessageSpeechEngine(
            selected_engine="realtime", tts_engine=FakeEngine(),
            realtime_engine=FakeEngine(AIConnectionError("offline")),
            fallback_to_tts=False,
        )
        with self.assertRaises(AIConnectionError):
            await disabled.synthesize(
                text="Stored", standard_voice_profile=StandardVoiceProfile.MALE,
                response_format="mp3",
            )

    async def test_invalid_response_never_falls_back(self):
        tts = FakeEngine()
        engine = ConfiguredMessageSpeechEngine(
            selected_engine="realtime", tts_engine=tts,
            realtime_engine=FakeEngine(AIInvalidResponseError("changed")),
        )
        with self.assertRaises(AIInvalidResponseError):
            await engine.synthesize(
                text="Stored", standard_voice_profile=StandardVoiceProfile.FEMALE,
                response_format="mp3",
            )
        self.assertEqual(tts.calls, [])

    def test_invalid_engine_fails_safely(self):
        with self.assertRaises(AIConfigurationError):
            ConfiguredMessageSpeechEngine(
                selected_engine="unknown", tts_engine=FakeEngine()
            )

    def test_tts_mode_does_not_require_realtime_configuration(self):
        engine = ConfiguredMessageSpeechEngine(
            selected_engine="tts",
            tts_engine=FakeEngine(),
            realtime_engine=None,
        )
        self.assertIsNotNone(engine)


class RealtimeSpeechFidelityTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalized_text_and_fidelity_instructions_are_forwarded(self):
        provider = FakeRealtimeProvider()
        service = RealtimeSpeechService(
            provider,
            standard_male_voice="cedar",
            standard_female_voice="marin",
            max_text_characters=4096,
        )
        await service.synthesize(
            text="**Hello** [site](https://example.com)",
            standard_voice_profile=StandardVoiceProfile.FEMALE,
            response_format="mp3",
        )
        call = provider.calls[0]
        self.assertEqual(call["text"], "Hello site")
        self.assertEqual(call["voice"], "marin")
        self.assertIn(FIDELITY_INSTRUCTIONS, call["instructions"])
        self.assertIn("not a question", call["instructions"])

    async def test_voice_profiles_combine_with_language_specific_guidance(self):
        cases = (
            (
                StandardVoiceProfile.MALE,
                "दादर खूप सुंदर आहे आणि मला तिथे जायचं आहे.",
                "cedar",
                "conversational Marathi",
            ),
            (
                StandardVoiceProfile.FEMALE,
                "दादर खूप सुंदर आहे आणि मला तिथे जायचं आहे.",
                "marin",
                "conversational Marathi",
            ),
            (
                StandardVoiceProfile.MALE,
                "मुझे वह शाम याद है और वहाँ बहुत लोग थे।",
                "cedar",
                "conversational Hindi",
            ),
            (
                StandardVoiceProfile.FEMALE,
                "मुझे वह शाम याद है और वहाँ बहुत लोग थे।",
                "marin",
                "conversational Hindi",
            ),
            (
                StandardVoiceProfile.FEMALE,
                "आज सुंदर दिवस आहे",
                "marin",
                "Do not assume all Devanagari text is Hindi",
            ),
            (
                StandardVoiceProfile.MALE,
                "Mala ajunhi athavta, apan sandhyakali khup gappa maraycho.",
                "cedar",
                "not ordinary English",
            ),
        )
        for profile, text, voice, guidance in cases:
            with self.subTest(profile=profile, guidance=guidance):
                provider = FakeRealtimeProvider()
                service = RealtimeSpeechService(
                    provider,
                    standard_male_voice="cedar",
                    standard_female_voice="marin",
                    max_text_characters=4096,
                )
                await service.synthesize(
                    text=text,
                    standard_voice_profile=profile,
                    response_format="mp3",
                )
                call = provider.calls[0]
                self.assertEqual(call["text"], text)
                self.assertEqual(call["voice"], voice)
                self.assertTrue(call["instructions"].startswith(FIDELITY_INSTRUCTIONS))
                self.assertIn(guidance, call["instructions"])
                self.assertIn("warm", call["instructions"].lower())


class FakeRealtimeProvider:
    def __init__(self):
        self.calls = []

    async def synthesize(self, **kwargs):
        self.calls.append(kwargs)
        return SpeechResult(b"wav", "audio/wav", "wav")


if __name__ == "__main__":
    unittest.main()
