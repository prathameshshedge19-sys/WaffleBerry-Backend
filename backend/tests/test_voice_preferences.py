"""Provider-isolated tests for personal voice preferences."""

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.dependencies.auth import get_current_user
from app.main import app
from app.models.user import User
from app.services.personal_voice_speech_service import PersonalVoiceSpeechService
from app.services.voice_catalogue import (
    INDIAN_RECOMMENDATION,
    NATURAL_ENGLISH_RECOMMENDATION,
    VOICE_CATALOGUE,
    VoiceProvider,
    public_catalogue,
)


class VoiceCatalogueTests(unittest.TestCase):
    def test_exact_order_names_recommendations_and_internal_routes(self):
        self.assertEqual(
            [voice.id for voice in VOICE_CATALOGUE],
            ["rohan", "mani", "shubh", "varun", "cedar", "rupali", "simran", "ritu", "suhani", "marin"],
        )
        public = public_catalogue()
        self.assertEqual([item["name"] for item in public["male"]], ["Rohan", "Mani", "Shubh", "Varun", "Cedar"])
        self.assertEqual([item["name"] for item in public["female"]], ["Rupali", "Simran", "Ritu", "Suhani", "Marin"])
        for group in public.values():
            for item in group:
                self.assertEqual(set(item), {"id", "name", "recommendation"})
                expected = NATURAL_ENGLISH_RECOMMENDATION if item["id"] in {"cedar", "marin"} else INDIAN_RECOMMENDATION
                self.assertEqual(item["recommendation"], expected)
        for voice_id in ("cedar", "marin"):
            item = next(
                voice
                for group in public.values()
                for voice in group
                if voice["id"] == voice_id
            )
            self.assertEqual(
                item["recommendation"],
                "Best suited for natural English and international languages",
            )
        for voice in VOICE_CATALOGUE:
            expected = VoiceProvider.OPENAI if voice.id in {"cedar", "marin"} else VoiceProvider.SARVAM
            self.assertEqual(voice.provider, expected)
            self.assertEqual(voice.provider_voice, voice.provider_voice.lower())


class VoicePreferenceAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self.user = User(full_name="One", email="one@example.com", password_hash="x")
        self.other = User(full_name="Two", email="two@example.com", password_hash="x")
        self.session.add_all([self.user, self.other])
        self.session.commit()
        app.dependency_overrides[get_db] = lambda: self.session
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.session.close()
        self.engine.dispose()

    def test_get_save_every_allowed_clear_and_user_isolation(self):
        response = self.client.get("/api/v1/user/voice-preference")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["selected_voice"])
        for voice in (item.id for item in VOICE_CATALOGUE):
            response = self.client.put("/api/v1/user/voice-preference", json={"voice": voice})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["selected_voice"], voice)
        app.dependency_overrides[get_current_user] = lambda: self.other
        self.assertIsNone(self.client.get("/api/v1/user/voice-preference").json()["selected_voice"])
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.assertEqual(self.client.get("/api/v1/user/voice-preference").json()["selected_voice"], "marin")
        self.assertIsNone(self.client.put("/api/v1/user/voice-preference", json={"voice": None}).json()["selected_voice"])

    def test_invalid_and_unauthenticated_requests_are_rejected(self):
        self.assertEqual(self.client.put("/api/v1/user/voice-preference", json={"voice": "altered"}).status_code, 422)
        app.dependency_overrides.pop(get_current_user)
        self.assertEqual(self.client.get("/api/v1/user/voice-preference").status_code, 401)


class FakeSelectedEngine:
    def __init__(self):
        self.calls = []

    async def synthesize(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


class PersonalVoiceRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_voice_routes_only_to_its_internal_engine(self):
        sarvam = FakeSelectedEngine()
        openai = FakeSelectedEngine()
        service = PersonalVoiceSpeechService(sarvam_service=sarvam, openai_service=openai)
        for definition in VOICE_CATALOGUE:
            sarvam.calls.clear()
            openai.calls.clear()
            await service.synthesize(text="Stored assistant text", voice=definition, response_format=None)
            if definition.provider == VoiceProvider.SARVAM:
                self.assertEqual(sarvam.calls[0]["selected_voice"], definition.id)
                self.assertEqual(openai.calls, [])
            else:
                self.assertEqual(openai.calls[0]["voice"], definition.id)
                self.assertEqual(sarvam.calls, [])


if __name__ == "__main__":
    unittest.main()
