"""Phase 10.2 deterministic emotional Live Call tests."""

import asyncio
from datetime import datetime, timedelta, timezone
import unittest

from app.services.ai.context_builder import ContextBuilder
from app.services.ai.provider import AIMessage, SpeechResult
from app.services.live_call import LiveCallSession, LiveCallTurnService
from app.services.live_call_emotion import LiveCallTone, LiveCallToneResolver
from app.services.chat_service import PreparedCompanionInput


class FakeTranscription:
    def __init__(self, text): self.text = text
    async def transcribe(self, audio): return self.text


class FakeAI:
    def __init__(self, response="I am here with you."):
        self.response, self.calls = response, []
    async def generate_response(self, messages):
        self.calls.append(messages)
        return self.response


class FakeSpeech:
    def __init__(self, fail_tone=None): self.calls, self.fail_tone = [], fail_tone
    async def synthesize(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("conversational_tone") == self.fail_tone:
            raise RuntimeError("optional delivery unsupported")
        return SpeechResult(b"audio", "audio/mpeg", "mp3")


class FakeCompanionContext:
    def __init__(self): self.calls = []
    def prepare_live_call_input(self, db, **kwargs):
        self.calls.append((db, kwargs))
        return PreparedCompanionInput(messages=[
            AIMessage("system", "CENTRAL PERSONA\nAPPROVED LEGACY MEMORIES: trusted fact"),
            *kwargs["history"],
            AIMessage("user", kwargs["user_message"]),
        ])


class LiveCallEmotionTests(unittest.TestCase):
    def setUp(self):
        self.resolver = LiveCallToneResolver()

    def test_compact_multilingual_tone_categories(self):
        cases = (
            ("What time is the train?", LiveCallTone.NEUTRAL),
            ("I got the job!", LiveCallTone.EXCITED),
            ("I had a terrible day.", LiveCallTone.COMFORTING),
            ("Do you remember our old house?", LiveCallTone.NOSTALGIC),
            ("मला नोकरी मिळाली!", LiveCallTone.EXCITED),
            ("मुझे तुम्हारी याद आती है।", LiveCallTone.COMFORTING),
            ("Mala job milali, I am so happy!", LiveCallTone.EXCITED),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(self.resolver.resolve(text, ()), expected)

    def test_nearby_gentle_tone_continues_then_can_change(self):
        history = (AIMessage("user", "I had a terrible day."), AIMessage("assistant", "Tell me."))
        self.assertEqual(
            self.resolver.resolve("And then my manager called me.", history),
            LiveCallTone.COMFORTING,
        )
        self.assertEqual(
            self.resolver.resolve("But then I got promoted!", history),
            LiveCallTone.EXCITED,
        )

    def test_generation_uses_tone_without_extra_ai_call_and_preserves_voice(self):
        ai, speech = FakeAI(), FakeSpeech()
        service = LiveCallTurnService(
            FakeTranscription("I got the job!"), ai, ContextBuilder(10), speech, speech
        )
        result = asyncio.run(service.process(
            session=self.session("standard_female"), audio=b"voice", content_type="audio/webm", history=(),
        ))
        self.assertEqual(result[0], "I got the job!")
        self.assertEqual(len(ai.calls), 1)
        prompt = ai.calls[0][0].content
        self.assertIn("lively but controlled energy", prompt)
        self.assertIn("Never invent, infer, embellish, or fill gaps", prompt)
        self.assertEqual(speech.calls[0]["standard_voice_profile"].value, "standard_female")
        self.assertEqual(speech.calls[0]["conversational_tone"], LiveCallTone.NEUTRAL)

    def test_greeting_and_emotional_response_share_identity_delivery_profile(self):
        ai, speech = FakeAI(), FakeSpeech()
        service = LiveCallTurnService(
            FakeTranscription("I got the job!"), ai, ContextBuilder(10), speech, speech
        )
        session = self.session("standard_female")
        asyncio.run(service.greeting(session=session))
        asyncio.run(service.process(
            session=session, audio=b"voice", content_type="audio/webm", history=(),
        ))
        greeting, response = speech.calls
        self.assertEqual(session.base_delivery_profile, "identity_neutral_v1")
        self.assertEqual(greeting["standard_voice_profile"], response["standard_voice_profile"])
        self.assertEqual(greeting["conversational_tone"], LiveCallTone.NEUTRAL)
        self.assertEqual(response["conversational_tone"], LiveCallTone.NEUTRAL)

    def test_identity_neutral_delivery_failure_retries_same_voice_and_profile(self):
        ai = FakeAI("That is wonderful news.")
        class FailOnceSpeech(FakeSpeech):
            async def synthesize(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise RuntimeError("transient delivery failure")
                return SpeechResult(b"audio", "audio/mpeg", "mp3")
        speech = FailOnceSpeech()
        service = LiveCallTurnService(
            FakeTranscription("I passed!"), ai, ContextBuilder(10), speech, speech
        )
        asyncio.run(service.process(
            session=self.session("standard_male"), audio=b"voice", content_type="audio/webm", history=(),
        ))
        self.assertEqual(len(speech.calls), 2)
        self.assertEqual(speech.calls[0]["standard_voice_profile"], speech.calls[1]["standard_voice_profile"])
        self.assertEqual(speech.calls[0]["conversational_tone"], LiveCallTone.NEUTRAL)
        self.assertEqual(speech.calls[1]["conversational_tone"], LiveCallTone.NEUTRAL)

    def test_turn_uses_session_scoped_shared_companion_context(self):
        ai, speech, context = FakeAI(), FakeSpeech(), FakeCompanionContext()
        service = LiveCallTurnService(
            FakeTranscription("Who is Meenakshi?"), ai, ContextBuilder(10), speech,
            speech, companion_context=context,
        )
        db = type("ReadOnlyDB", (), {"rollback": lambda self: None})()
        history = (AIMessage("user", "Tell me about your sister."),)
        session = self.session("standard_female")
        asyncio.run(service.process(
            session=session, audio=b"voice", content_type="audio/webm",
            history=history, db=db,
        ))
        call = context.calls[0][1]
        self.assertEqual(call["user_id"], session.user_id)
        self.assertEqual(call["legacy_id"], session.legacy_id)
        self.assertEqual(call["legacy_name"], session.legacy_name)
        self.assertEqual(call["relationship"], session.relationship)
        self.assertEqual(call["history"], history)
        self.assertIn("APPROVED LEGACY MEMORIES", ai.calls[0][0].content)

    def test_style_and_length_modify_delivery_but_not_grounding_or_emotion(self):
        ai, speech, context = FakeAI(), FakeSpeech(), FakeCompanionContext()
        service = LiveCallTurnService(
            FakeTranscription("I had a terrible day."), ai, ContextBuilder(10), speech,
            speech, companion_context=context,
        )
        db = type("ReadOnlyDB", (), {"rollback": lambda self: None})()
        session = self.session("standard_female")
        session = session.__class__(
            session.session_id, session.transport_token, session.user_id,
            session.legacy_id, session.legacy_name, session.relationship,
            session.effective_voice, session.state, session.created_at,
            session.expires_at, session.ended_at, "expressive", "detailed",
        )
        asyncio.run(service.process(
            session=session, audio=b"voice", content_type="audio/webm", history=(), db=db,
        ))
        prompt = ai.calls[0][0].content
        self.assertIn("APPROVED LEGACY MEMORIES: trusted fact", prompt)
        self.assertIn("Continue with supported detail after the short direct first sentence", prompt)
        self.assertIn("Emotional safety overrides style", prompt)
        self.assertEqual(speech.calls[0]["conversational_tone"], LiveCallTone.NEUTRAL)

    @staticmethod
    def session(voice):
        now = datetime.now(timezone.utc)
        return LiveCallSession(
            "session", "token", 1, 1, "Aaji", "grandmother", voice,
            "connected", now, now + timedelta(minutes=15),
        )


if __name__ == "__main__":
    unittest.main()
