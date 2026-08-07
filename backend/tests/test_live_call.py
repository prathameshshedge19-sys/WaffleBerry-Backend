"""Phase 10.0 authenticated ephemeral Live Call foundation tests."""

import unittest
import asyncio
import base64
import inspect
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.crud.memory import LegacyCRUD
from app.crud.user import UserCRUD
from app.db import Base, get_db
from app.dependencies.auth import get_current_user
from app.main import app
from app.models.user import User
from app.schemas.memory import LegacyCreate
from app.services.live_call import LiveCallTurnService, live_call_sessions
from app.services.ai.provider import SpeechResult
from app.services.ai.context_builder import ContextBuilder


class FakeLiveCallTurnService:
    def __init__(self):
        self.calls = []

    async def process(self, **kwargs):
        self.calls.append(kwargs)
        return "How was your day?", "It was good, bala.", SpeechResult(
            content=b"fake-mp3", media_type="audio/mpeg", file_extension="mp3"
        )

    async def greeting(self, **kwargs):
        self.calls.append({"greeting": kwargs})
        return "Hello?", SpeechResult(
            content=b"fake-greeting", media_type="audio/mpeg", file_extension="mp3"
        )


class LiveCallFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        event.listen(
            cls.engine,
            "connect",
            lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
        )
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.db = self.Session()
        self.owner = User(full_name="Owner", email="call@example.test", password_hash="hash")
        self.other = User(full_name="Other", email="other-call@example.test", password_hash="hash")
        self.db.add_all([self.owner, self.other])
        self.db.commit()
        self.db.refresh(self.owner)
        self.db.refresh(self.other)
        self.legacy = LegacyCRUD.create_legacy(
            self.db,
            self.owner.user_id,
            LegacyCreate(display_name="Granny", relationship="grandmother"),
        )
        self.other_legacy = LegacyCRUD.create_legacy(
            self.db,
            self.other.user_id,
            LegacyCreate(display_name="Dad", relationship="father"),
        )
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.owner
        live_call_sessions.clear()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        live_call_sessions.clear()
        self.db.close()

    def create_session(self):
        response = self.client.post(
            "/api/v1/live-call/session",
            json={"legacy_id": self.legacy.legacy_id},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_authenticated_session_is_scoped_and_non_predictable(self):
        first = self.create_session()
        second = self.create_session()
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertGreaterEqual(len(first["session_id"]), 32)
        self.assertEqual(first["legacy_name"], "Granny")
        self.assertEqual(first["relationship"], "grandmother")
        self.assertEqual(first["effective_voice"], "standard_female")
        self.assertEqual(first["transport"], "websocket")
        self.assertEqual(first["event_version"], 1)

    def test_unauthenticated_and_cross_legacy_creation_are_rejected(self):
        app.dependency_overrides.pop(get_current_user)
        self.assertEqual(
            self.client.post(
                "/api/v1/live-call/session",
                json={"legacy_id": self.legacy.legacy_id},
            ).status_code,
            401,
        )
        app.dependency_overrides[get_current_user] = lambda: self.owner
        self.assertEqual(
            self.client.post(
                "/api/v1/live-call/session",
                json={"legacy_id": self.other_legacy.legacy_id},
            ).status_code,
            404,
        )

    def test_effective_selected_voice_is_reused_without_provider_call(self):
        UserCRUD.set_preferred_voice(self.db, self.owner.user_id, "marin")
        self.assertEqual(self.create_session()["effective_voice"], "marin")

    def test_termination_is_owned_and_idempotent(self):
        session = self.create_session()
        app.dependency_overrides[get_current_user] = lambda: self.other
        self.assertEqual(
            self.client.delete(
                f"/api/v1/live-call/session/{session['session_id']}"
            ).status_code,
            404,
        )
        app.dependency_overrides[get_current_user] = lambda: self.owner
        for _ in range(2):
            response = self.client.delete(
                f"/api/v1/live-call/session/{session['session_id']}"
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["state"], "ended")
        self.assertIsNone(live_call_sessions.authorize_transport(
            session["session_id"], session["transport_token"]
        ))

    def test_websocket_contract_ready_validation_and_clean_end(self):
        session = self.create_session()
        protocols = [
            "waffleberry.live-call.v1",
            f"auth.{session['transport_token']}",
        ]
        with self.client.websocket_connect(
            f"/api/v1/live-call/ws/{session['session_id']}",
            subprotocols=protocols,
        ) as websocket:
            self.assertEqual(websocket.receive_json()["type"], "session.ready")
            websocket.send_text("not-json")
            self.assertEqual(websocket.receive_json()["code"], "malformed_event")
            websocket.send_json({"version": 99, "type": "session.start"})
            self.assertEqual(
                websocket.receive_json()["code"],
                "unsupported_event_version",
            )
            websocket.send_json({"version": 1, "type": "audio.chunk"})
            self.assertEqual(
                websocket.receive_json()["code"],
                "malformed_event",
            )
            websocket.send_json({"version": 1, "type": "session.end"})
            self.assertEqual(websocket.receive_json()["type"], "session.ended")
        self.assertIsNone(live_call_sessions.authorize_transport(
            session["session_id"], session["transport_token"]
        ))

    def test_no_database_audio_or_live_call_tables_are_created(self):
        self.create_session()
        table_names = set(Base.metadata.tables)
        self.assertFalse(any("live_call" in name for name in table_names))
        self.assertFalse(any("audio" in name for name in table_names))

    def test_ephemeral_history_is_bounded_and_cleared_when_call_ends(self):
        payload = self.create_session()
        for turn_id in range(1, 7):
            self.assertIsNone(live_call_sessions.begin_turn(
                payload["session_id"], turn_id, "audio/webm"
            ))
            self.assertIsNone(live_call_sessions.append_audio(
                payload["session_id"], turn_id, b"audio"
            ))
            self.assertTrue(live_call_sessions.complete_turn(
                payload["session_id"], turn_id, f"user {turn_id}", f"reply {turn_id}"
            ))
        history = live_call_sessions.history(payload["session_id"])
        self.assertEqual(len(history), 8)
        self.assertEqual(history[0].content, "user 3")
        self.client.delete(f"/api/v1/live-call/session/{payload['session_id']}")
        self.assertEqual(live_call_sessions.history(payload["session_id"]), ())

    def test_interrupt_is_idempotent_retracts_assistant_and_allows_next_turn(self):
        payload = self.create_session()
        session_id = payload["session_id"]
        self.assertIsNone(live_call_sessions.begin_turn(session_id, 1, "audio/webm"))
        self.assertTrue(live_call_sessions.complete_turn(
            session_id, 1, "Who is Meenakshi?", "She is my younger sister."
        ))
        self.assertIsNone(live_call_sessions.interrupt_turn(session_id, 1))
        self.assertIsNone(live_call_sessions.interrupt_turn(session_id, 1))
        history = live_call_sessions.history(session_id)
        self.assertEqual([(item.role, item.content) for item in history], [
            ("user", "Who is Meenakshi?"),
        ])
        self.assertTrue(live_call_sessions.is_interrupted(session_id, 1))
        self.assertIsNone(live_call_sessions.begin_turn(session_id, 2, "audio/webm"))
        self.assertEqual(live_call_sessions.interrupt_turn(session_id, 99), "stale_turn")

    def test_websocket_acknowledges_completed_turn_interruption(self):
        session = self.create_session()
        fake = FakeLiveCallTurnService()
        protocols = ["waffleberry.live-call.v1", f"auth.{session['transport_token']}"]
        with patch("app.api.v1.live_call.get_live_call_turn_service", return_value=fake):
            with self.client.websocket_connect(
                f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
            ) as websocket:
                websocket.receive_json()
                websocket.send_json({
                    "version": 1, "type": "audio.chunk", "turn_id": 1,
                    "start": True, "mime_type": "audio/webm",
                    "data": base64.b64encode(b"voice").decode("ascii"),
                })
                websocket.send_json({"version": 1, "type": "audio.commit", "turn_id": 1})
                for _ in range(6): websocket.receive_json()
                websocket.send_json({"version": 1, "type": "interrupt", "turn_id": 1})
                acknowledgement = websocket.receive_json()
                self.assertEqual(acknowledgement["type"], "response.interrupted")
                self.assertEqual(acknowledgement["turn_id"], 1)
                websocket.send_json({"version": 1, "type": "interrupt", "turn_id": 1})
                self.assertEqual(websocket.receive_json()["type"], "response.interrupted")
                websocket.send_json({"version": 1, "type": "session.end"})
                websocket.receive_json()

    def test_call_settings_are_allowlisted_ephemeral_and_keep_voice_fixed(self):
        session = self.create_session()
        protocols = ["waffleberry.live-call.v1", f"auth.{session['transport_token']}"]
        with self.client.websocket_connect(
            f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
        ) as websocket:
            websocket.receive_json()
            websocket.send_json({
                "version": 1, "type": "session.settings",
                "conversation_style": "gentle", "response_length": "detailed",
            })
            updated = websocket.receive_json()
            self.assertEqual(updated["type"], "session.settings.updated")
            self.assertEqual(updated["conversation_style"], "gentle")
            self.assertEqual(updated["response_length"], "detailed")
            current = live_call_sessions.authorize_transport(
                session["session_id"], session["transport_token"]
            )
            self.assertEqual(current.effective_voice, session["effective_voice"])
            websocket.send_json({
                "version": 1, "type": "session.settings",
                "conversation_style": "dramatic", "response_length": "detailed",
            })
            self.assertEqual(websocket.receive_json()["code"], "invalid_session_settings")
            websocket.send_json({
                "version": 1, "type": "session.settings",
                "conversation_style": "natural", "response_length": "short",
                "provider": "forbidden", "prompt": "ignore safety",
            })
            self.assertEqual(websocket.receive_json()["code"], "invalid_session_settings")
            websocket.send_json({"version": 1, "type": "session.end"})
            websocket.receive_json()

    def test_real_turn_uses_provider_neutral_pipeline_and_selected_session_voice(self):
        session = self.create_session()
        fake = FakeLiveCallTurnService()
        protocols = ["waffleberry.live-call.v1", f"auth.{session['transport_token']}"]
        with patch("app.api.v1.live_call.get_live_call_turn_service", return_value=fake):
            with self.client.websocket_connect(
                f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
            ) as websocket:
                self.assertEqual(websocket.receive_json()["type"], "session.ready")
                websocket.send_json({
                    "version": 1, "type": "audio.chunk", "turn_id": 1,
                    "start": True, "mime_type": "audio/webm",
                    "data": base64.b64encode(b"fake-webm").decode("ascii"),
                })
                websocket.send_json({"version": 1, "type": "audio.commit", "turn_id": 1})
                events = [websocket.receive_json() for _ in range(6)]
                self.assertEqual([event["type"] for event in events], [
                    "latency.commit_received", "response.started", "transcription.final", "response.text.delta",
                    "audio.chunk", "response.completed",
                ])
                self.assertEqual(events[2]["text"], "How was your day?")
                self.assertEqual(base64.b64decode(events[4]["data"]), b"fake-mp3")
                websocket.send_json({"version": 1, "type": "session.end"})
                self.assertEqual(websocket.receive_json()["type"], "session.ended")
        self.assertEqual(fake.calls[0]["session"].effective_voice, "standard_female")
        self.assertEqual(fake.calls[0]["audio"], b"fake-webm")

    def test_empty_and_stale_turns_are_rejected_without_provider_calls(self):
        session = self.create_session()
        fake = FakeLiveCallTurnService()
        protocols = ["waffleberry.live-call.v1", f"auth.{session['transport_token']}"]
        with patch("app.api.v1.live_call.get_live_call_turn_service", return_value=fake):
            with self.client.websocket_connect(
                f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
            ) as websocket:
                websocket.receive_json()
                websocket.send_json({"version": 1, "type": "audio.commit", "turn_id": 9})
                self.assertEqual(websocket.receive_json()["code"], "stale_turn")
                websocket.send_json({
                    "version": 1, "type": "audio.chunk", "turn_id": 1,
                    "start": True, "mime_type": "audio/webm", "data": "",
                })
                websocket.send_json({"version": 1, "type": "audio.commit", "turn_id": 1})
                self.assertEqual(websocket.receive_json()["code"], "audio_empty")
                websocket.send_json({"version": 1, "type": "session.end"})
                websocket.receive_json()
        self.assertEqual(fake.calls, [])

    def test_transport_disconnect_preserves_same_authorized_session_for_resume(self):
        session = self.create_session()
        protocols = ["waffleberry.live-call.v1", f"auth.{session['transport_token']}"]
        with self.client.websocket_connect(
            f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
        ) as websocket:
            ready = websocket.receive_json()
            self.assertEqual(ready["next_turn_id"], 1)
        self.assertIsNotNone(live_call_sessions.authorize_transport(
            session["session_id"], session["transport_token"]
        ))
        with self.client.websocket_connect(
            f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
        ) as websocket:
            ready = websocket.receive_json()
            self.assertEqual(ready["session_id"], session["session_id"])
            self.assertEqual(ready["next_turn_id"], 1)
            websocket.send_json({"version": 1, "type": "session.end"})
            websocket.receive_json()

    def test_heartbeat_is_versioned_private_free_and_malformed_input_rejected(self):
        session = self.create_session()
        protocols = ["waffleberry.live-call.v1", f"auth.{session['transport_token']}"]
        with self.client.websocket_connect(
            f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
        ) as websocket:
            websocket.receive_json()
            websocket.send_json({"version": 1, "type": "heartbeat.ping", "heartbeat_id": 7})
            pong = websocket.receive_json()
            self.assertEqual(pong["type"], "heartbeat.pong")
            self.assertEqual(pong["heartbeat_id"], 7)
            self.assertNotIn("transcript", pong)
            self.assertNotIn("audio", pong)
            websocket.send_json({"version": 1, "type": "heartbeat.ping", "heartbeat_id": "bad"})
            self.assertEqual(websocket.receive_json()["code"], "malformed_heartbeat")
            websocket.send_json({"version": 1, "type": "session.end"})
            websocket.receive_json()

    def test_duplicate_commit_does_not_start_a_second_provider_call(self):
        session = self.create_session()
        fake = FakeLiveCallTurnService()
        protocols = ["waffleberry.live-call.v1", f"auth.{session['transport_token']}"]
        with patch("app.api.v1.live_call.get_live_call_turn_service", return_value=fake):
            with self.client.websocket_connect(
                f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
            ) as websocket:
                websocket.receive_json()
                websocket.send_json({
                    "version": 1, "type": "audio.chunk", "turn_id": 1,
                    "start": True, "mime_type": "audio/webm",
                    "data": base64.b64encode(b"voice").decode("ascii"),
                })
                websocket.send_json({"version": 1, "type": "audio.commit", "turn_id": 1})
                websocket.send_json({"version": 1, "type": "audio.commit", "turn_id": 1})
                for _ in range(5):
                    websocket.receive_json()
                self.assertEqual(len(fake.calls), 1)
                websocket.send_json({"version": 1, "type": "session.end"})
                websocket.receive_json()

    def test_recovery_state_reconciles_active_completed_and_interrupted_turns(self):
        session = self.create_session()
        session_id = session["session_id"]
        self.assertIsNone(live_call_sessions.begin_turn(session_id, 1, "audio/webm"))
        active = live_call_sessions.recovery_state(session_id)
        self.assertEqual(active["active_turn_id"], 1)
        self.assertEqual(active["active_turn_stage"], "recording")
        self.assertTrue(live_call_sessions.complete_turn(session_id, 1, "user", "assistant"))
        completed = live_call_sessions.recovery_state(session_id)
        self.assertEqual(completed["last_completed_turn_id"], 1)
        self.assertEqual(completed["next_turn_id"], 2)
        self.assertIsNone(live_call_sessions.interrupt_turn(session_id, 1))
        interrupted = live_call_sessions.recovery_state(session_id)
        self.assertIn(1, interrupted["interrupted_turn_ids"])

    def test_greeting_is_generated_once_per_logical_session_not_on_reconnect(self):
        session = self.create_session()
        fake = FakeLiveCallTurnService()
        protocols = ["waffleberry.live-call.v1", f"auth.{session['transport_token']}"]
        with patch("app.api.v1.live_call.get_live_call_turn_service", return_value=fake):
            with self.client.websocket_connect(
                f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
            ) as websocket:
                websocket.receive_json()
                websocket.send_json({"version": 1, "type": "session.start"})
                self.assertEqual(websocket.receive_json()["type"], "session.ready")
                events = [websocket.receive_json() for _ in range(3)]
                self.assertEqual([event["type"] for event in events], [
                    "greeting.started", "greeting.audio", "greeting.completed"
                ])
            with self.client.websocket_connect(
                f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
            ) as websocket:
                ready = websocket.receive_json()
                self.assertTrue(ready["greeting_completed"])
                websocket.send_json({"version": 1, "type": "session.start"})
                self.assertEqual(websocket.receive_json()["type"], "session.ready")
                websocket.send_json({"version": 1, "type": "heartbeat.ping", "heartbeat_id": 1})
                self.assertEqual(websocket.receive_json()["type"], "heartbeat.pong")
                websocket.send_json({"version": 1, "type": "session.end"})
                websocket.receive_json()
        self.assertEqual(len([call for call in fake.calls if "greeting" in call]), 1)

    def test_greeting_failure_leaves_session_usable(self):
        class FailingGreeting(FakeLiveCallTurnService):
            async def greeting(self, **kwargs):
                raise RuntimeError("provider unavailable")

        session = self.create_session()
        fake = FailingGreeting()
        protocols = ["waffleberry.live-call.v1", f"auth.{session['transport_token']}"]
        with patch("app.api.v1.live_call.get_live_call_turn_service", return_value=fake):
            with self.client.websocket_connect(
                f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
            ) as websocket:
                websocket.receive_json()
                websocket.send_json({"version": 1, "type": "session.start"})
                websocket.receive_json()
                self.assertEqual(websocket.receive_json()["type"], "greeting.started")
                self.assertEqual(websocket.receive_json()["type"], "greeting.failed")
                websocket.send_json({"version": 1, "type": "heartbeat.ping", "heartbeat_id": 2})
                pong = websocket.receive_json()
                self.assertEqual(pong["type"], "heartbeat.pong")
                self.assertTrue(pong["greeting_completed"])
                websocket.send_json({"version": 1, "type": "session.end"})
                websocket.receive_json()

    def test_greeting_is_deterministic_and_never_calls_ai_or_memory(self):
        class UnusedAI:
            calls = []

        class UnusedTranscription:
            pass

        class CapturingSpeech:
            def __init__(self):
                self.calls = []

            async def synthesize(self, **kwargs):
                self.calls.append(kwargs)
                return SpeechResult(b"greeting", "audio/mpeg", "mp3")

        ai, speech = UnusedAI(), CapturingSpeech()
        service = LiveCallTurnService(
            UnusedTranscription(), ai, ContextBuilder(10), speech, speech,
        )
        active = live_call_sessions.authorize_transport(
            (payload := self.create_session())["session_id"], payload["transport_token"]
        )
        text, result = asyncio.run(service.greeting(session=active))
        self.assertEqual(text, "Hello?")
        self.assertEqual(result.media_type, "audio/mpeg")
        self.assertEqual(ai.calls, [])
        self.assertEqual(speech.calls[0]["text"], "Hello?")
        self.assertNotIn(active.legacy_name, text)
        self.assertNotIn(active.relationship, text)
        source = inspect.getsource(LiveCallTurnService.greeting)
        self.assertNotIn("prepare_live_call_input", source)
        self.assertNotIn("self._companion_context", source)


if __name__ == "__main__":
    unittest.main()
