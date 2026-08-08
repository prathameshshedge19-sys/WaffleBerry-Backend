"""Phase 10.0 authenticated ephemeral Live Call foundation tests."""

import unittest
import asyncio
import base64
import inspect
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
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
from app.dependencies.ai import get_realtime_bootstrap_provider, get_realtime_tool_service
from app.main import app
from app.models.user import User
from app.schemas.memory import LegacyCreate
from app.services.live_call import LiveCallSessionStore, LiveCallTurnService, live_call_sessions
from app.services.ai.provider import SpeechResult
from app.services.ai.context_builder import ContextBuilder
from app.services.chat_service import ChatService
from app.services.persona_profile import PersonaProfile
from app.services.realtime_live_call import (
    OpenAIRealtimeBootstrapProvider,
    REALTIME_TOOLS,
    RealtimeBootstrapError,
    RealtimeMemoryState,
    RealtimeToolService,
    build_realtime_session_payload,
    choose_live_call_engine,
    choose_live_call_delivery,
    relationship_personality_prior,
    session_instructions,
)


class FakeRealtimeBootstrapProvider:
    async def create(self, session):
        return {"client_secret": "ephemeral-test-secret", "expires_at": 123456}


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
        discarded = []
        app.dependency_overrides[get_realtime_tool_service] = lambda: SimpleNamespace(
            discard_session=lambda session_id: discarded.append(session_id)
        )
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
        self.assertEqual(discarded, [session["session_id"], session["session_id"]])

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

    def test_superseded_and_expired_sessions_release_ephemeral_runtime(self):
        store = LiveCallSessionStore()
        first = store.create(
            user_id=1, legacy_id=1, legacy_name="Aaji", relationship="grandmother",
            effective_voice="marin",
        )
        self.assertIsNone(store.begin_turn(first.session_id, 1, "audio/webm"))
        self.assertIsNone(store.append_audio(first.session_id, 1, b"private audio"))
        self.assertTrue(store.complete_turn(
            first.session_id, 1, "private transcript", "private response",
        ))
        store.create(
            user_id=1, legacy_id=1, legacy_name="Aaji", relationship="grandmother",
            effective_voice="marin",
        )
        self.assertEqual(store.history(first.session_id), ())
        self.assertNotIn(first.session_id, store._runtime)

        expiring = store.create(
            user_id=2, legacy_id=2, legacy_name="Dad", relationship="father",
            effective_voice="cedar",
        )
        self.assertIsNone(store.begin_turn(expiring.session_id, 1, "audio/webm"))
        self.assertIsNone(store.append_audio(expiring.session_id, 1, b"private audio"))
        store._sessions[expiring.session_id] = replace(
            expiring, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        self.assertIsNone(store.authorize_user(expiring.session_id, 2))
        self.assertNotIn(expiring.session_id, store._runtime)

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

    def test_call_preferences_are_snapshotted_and_websocket_cannot_mutate_them(self):
        UserCRUD.set_conversation_preferences(
            self.db, self.owner.user_id, voice="simran",
            conversation_style="gentle", response_length="short",
        )
        session = self.create_session()
        self.assertEqual(session["effective_voice"], "simran")
        self.assertEqual(session["conversation_style"], "gentle")
        self.assertEqual(session["response_length"], "short")
        UserCRUD.set_conversation_preferences(
            self.db, self.owner.user_id, voice="cedar",
            conversation_style="expressive", response_length="detailed",
        )
        current = live_call_sessions.authorize_transport(
            session["session_id"], session["transport_token"]
        )
        self.assertEqual((current.effective_voice, current.conversation_style, current.response_length),
                         ("simran", "gentle", "short"))
        protocols = ["waffleberry.live-call.v1", f"auth.{session['transport_token']}"]
        with self.client.websocket_connect(
            f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
        ) as websocket:
            websocket.receive_json()
            websocket.send_json({
                "version": 1, "type": "session.settings",
                "conversation_style": "expressive", "response_length": "detailed",
            })
            self.assertEqual(websocket.receive_json()["code"], "unsupported_event_type")
            websocket.send_json({"version": 1, "type": "session.end"})
            websocket.receive_json()
        next_session = self.create_session()
        self.assertEqual((next_session["effective_voice"], next_session["conversation_style"],
                          next_session["response_length"]), ("cedar", "expressive", "detailed"))

    def test_realtime_engine_is_flagged_voice_capable_and_uses_ephemeral_bootstrap(self):
        UserCRUD.set_conversation_preferences(
            self.db, self.owner.user_id, voice="cedar",
            conversation_style="gentle", response_length="short",
        )
        settings = SimpleNamespace(
            live_call_realtime_enabled=True,
            live_call_realtime_strict=True,
            default_standard_voice_profile="standard_female",
            openai_realtime_model="gpt-realtime-test",
            openai_realtime_vad_threshold=0.60,
        )
        app.dependency_overrides[get_realtime_bootstrap_provider] = FakeRealtimeBootstrapProvider
        with patch("app.api.v1.live_call.get_settings", return_value=settings):
            response = self.client.post(
                "/api/v1/live-call/session",
                json={"legacy_id": self.legacy.legacy_id, "engine": "realtime"},
            )
            self.assertEqual(response.status_code, 201)
            session = response.json()
            self.assertEqual((session["engine"], session["transport"]), ("realtime", "webrtc"))
            self.assertEqual(session["engine_reason"], "none")
            self.assertTrue(session["realtime_strict"])
            self.assertTrue(session["realtime_capable"])
            self.assertNotIn("api_key", response.text.lower())
            bootstrap = self.client.post(
                f"/api/v1/live-call/realtime/{session['session_id']}/bootstrap"
            )
        self.assertEqual(bootstrap.status_code, 200)
        self.assertEqual(bootstrap.json(), {
            "client_secret": "ephemeral-test-secret", "expires_at": 123456,
            "model": "gpt-realtime-test", "voice": "cedar",
        })

    def test_custom_voice_stays_cascade_even_when_realtime_is_requested(self):
        settings = SimpleNamespace(live_call_realtime_enabled=True)
        self.assertEqual(choose_live_call_engine(settings, "simran", "realtime"),
                         ("cascade", False, "external_realtime_disabled"))
        self.assertEqual(choose_live_call_engine(settings, "cedar", "cascade"),
                         ("cascade", True, "explicit_cascade_selection"))

    def test_realtime_engine_selection_reasons_are_authoritative(self):
        disabled = SimpleNamespace(live_call_realtime_enabled=False)
        enabled = SimpleNamespace(live_call_realtime_enabled=True)
        self.assertEqual(choose_live_call_engine(disabled, "marin", "auto"),
                         ("cascade", True, "feature_flag_disabled"))
        self.assertEqual(choose_live_call_engine(enabled, "marin", "auto"),
                         ("realtime", True, "none"))
        self.assertEqual(choose_live_call_engine(enabled, "cedar", "auto"),
                         ("realtime", True, "none"))
        self.assertEqual(choose_live_call_engine(enabled, "custom", "auto"),
                         ("cascade", False, "voice_not_realtime_capable"))

    def test_external_voice_realtime_separates_engine_from_renderer(self):
        enabled = SimpleNamespace(
            live_call_realtime_enabled=True,
            live_call_external_voice_realtime_enabled=True,
        )
        for voice in ("simran", "shubh"):
            plan = choose_live_call_delivery(enabled, voice, "auto")
            self.assertEqual(
                (plan.conversation_engine, plan.speech_renderer, plan.reason),
                ("realtime", "external_nonstreaming_tts", "none"),
            )
        session = LiveCallSessionStore().create(
            user_id=1, legacy_id=2, legacy_name="Aaji", relationship="grandmother",
            effective_voice="simran", engine="realtime", realtime_capable=True,
            speech_renderer="external_nonstreaming_tts",
        )
        payload = build_realtime_session_payload(enabled, session)["session"]
        self.assertEqual(payload["output_modalities"], ["text"])
        self.assertNotIn("output", payload["audio"])
        self.assertEqual(session.effective_voice, "simran")

    def test_realtime_bootstrap_classifies_provider_failures_and_returns_only_ephemeral_secret(self):
        active = live_call_sessions.authorize_transport(
            (created := self.create_session())["session_id"], created["transport_token"]
        )

        class Response:
            def __init__(self, status_code, body):
                self.status_code, self._body = status_code, body
            def json(self):
                return self._body

        class Client:
            response = None
            request = None
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): pass
            async def post(self, url, **kwargs):
                Client.request = (url, kwargs)
                return Client.response

        settings = SimpleNamespace(
            openai_api_key="permanent-server-key", jwt_secret_key="jwt-secret",
            openai_realtime_session_url="https://api.openai.com/v1/realtime/client_secrets",
            openai_realtime_model="gpt-realtime-2.1",
            openai_realtime_vad_threshold=0.60,
        )
        provider = OpenAIRealtimeBootstrapProvider(settings)
        cases = [
            (401, "bootstrap_auth_failed"),
            (400, "bootstrap_provider_rejected"),
            (500, "bootstrap_request_failed"),
        ]
        with patch("app.services.realtime_live_call.httpx.AsyncClient", Client):
            for status_code, category in cases:
                Client.response = Response(status_code, {})
                with self.assertRaises(RealtimeBootstrapError) as caught:
                    asyncio.run(provider.create(active))
                self.assertEqual((caught.exception.category, caught.exception.status_code),
                                 (category, status_code))
            Client.response = Response(200, {"value": "ephemeral-browser-key", "expires_at": 9})
            credential = asyncio.run(provider.create(active))

        self.assertEqual(credential, {
            "client_secret": "ephemeral-browser-key", "expires_at": 9,
        })
        _, request = Client.request
        self.assertEqual(request["json"]["session"]["model"], "gpt-realtime-2.1")
        self.assertEqual(request["headers"]["Authorization"], "Bearer permanent-server-key")
        self.assertNotIn("permanent-server-key", str(credential))

    def test_realtime_bootstrap_distinguishes_rate_limit_from_quota_without_retrying(self):
        active = live_call_sessions.authorize_transport(
            (created := self.create_session())["session_id"], created["transport_token"]
        )

        class Response:
            status_code = 429
            headers = {"Retry-After": "30"}
            def __init__(self, code): self.code = code
            def json(self): return {"error": {"code": self.code}}

        class Client:
            response = None
            calls = 0
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): pass
            async def post(self, *_args, **_kwargs):
                Client.calls += 1
                return Client.response

        settings = SimpleNamespace(
            openai_api_key="server-key", jwt_secret_key="jwt-secret",
            openai_realtime_session_url="https://api.openai.com/v1/realtime/client_secrets",
            openai_realtime_model="gpt-realtime-test", openai_realtime_vad_threshold=0.60,
        )
        provider = OpenAIRealtimeBootstrapProvider(settings)
        with patch("app.services.realtime_live_call.httpx.AsyncClient", Client):
            Client.response = Response("rate_limit_exceeded")
            with self.assertRaises(RealtimeBootstrapError) as rate:
                asyncio.run(provider.create(active))
            Client.response = Response("insufficient_quota")
            with self.assertRaises(RealtimeBootstrapError) as quota:
                asyncio.run(provider.create(active))
        self.assertEqual((rate.exception.category, rate.exception.retry_after),
                         ("provider_rate_limited", 30))
        self.assertEqual((quota.exception.category, quota.exception.retry_after),
                         ("provider_quota_exhausted", 30))
        self.assertEqual(Client.calls, 2)

    def test_realtime_session_uses_patient_server_vad_and_frozen_voice(self):
        aaji = LegacyCRUD.create_legacy(
            self.db,
            self.owner.user_id,
            LegacyCreate(display_name="Aaji", relationship="grandmother"),
        )
        UserCRUD.set_conversation_preferences(
            self.db, self.owner.user_id, voice="marin",
            conversation_style="natural", response_length="balanced",
        )
        settings = SimpleNamespace(
            live_call_realtime_enabled=True,
            default_standard_voice_profile="standard_female",
            openai_realtime_model="gpt-realtime-test",
            openai_realtime_vad_threshold=0.60,
        )
        with patch("app.api.v1.live_call.get_settings", return_value=settings):
            created = self.client.post(
                "/api/v1/live-call/session",
                json={"legacy_id": aaji.legacy_id, "engine": "realtime"},
            ).json()
        session = live_call_sessions.authorize_user(created["session_id"], self.owner.user_id)
        self.assertEqual(created["legacy_name"], "Aaji")
        self.assertEqual(created["relationship"], "grandmother")
        self.assertEqual(session.legacy_id, aaji.legacy_id)
        self.assertEqual(session.legacy_name, "Aaji")
        self.assertEqual(session.relationship, "grandmother")
        self.assertEqual(session.engine, "realtime")
        self.assertEqual(session.effective_voice, "marin")
        payload = build_realtime_session_payload(settings, session)["session"]
        self.assertEqual(payload["model"], "gpt-realtime-test")
        self.assertIsInstance(payload["instructions"], str)
        self.assertIn("Aaji", payload["instructions"])
        self.assertIn("speak from Aaji's first-person perspective", payload["instructions"])
        self.assertIn("you are not literally Aaji", payload["instructions"])
        self.assertIn("Keep other people distinct", payload["instructions"])
        self.assertIn("Persona affects grammatical perspective only", payload["instructions"])
        self.assertNotIn("Companion for Aaji", payload["instructions"])
        self.assertEqual(payload["output_modalities"], ["audio"])
        self.assertEqual(set(payload["audio"]["input"]), {"turn_detection", "transcription"})
        self.assertEqual(payload["audio"]["input"]["transcription"]["model"], "gpt-live-transcribe")
        vad = payload["audio"]["input"]["turn_detection"]
        self.assertEqual(vad, {
            "type": "server_vad", "threshold": 0.60, "prefix_padding_ms": 400,
            "silence_duration_ms": 1400, "create_response": True,
            "interrupt_response": False,
        })
        self.assertEqual(payload["audio"]["output"]["voice"], "marin")
        self.assertEqual(payload["tool_choice"], "required")

        captured = {}
        class CapturingChatService:
            def prepare_live_call_input(self, _db, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    messages=(), memory_ids=(), identity_direct=True,
                    conflict_count=0, identity_count=1, has_uncertainty=False,
                    resolved_entities=(), memory_evidence=(),
                )
            def retrieve_live_call_identity(self, _db, **kwargs):
                captured.update(kwargs)
                return (
                    SimpleNamespace(records=({"fact_type": "full_name", "value": "Aaji",
                        "relationship": None, "conflicting": False,
                        "uncertainty_note": None},), candidate_count=1,
                        conflict_present=False),
                    SimpleNamespace(canonical_value="Aaji"),
                )

        result = RealtimeToolService(CapturingChatService()).execute(
            self.db, session, "retrieve_legacy_memory_context", {"query": "Goa"}
        )
        self.assertEqual(captured["legacy_id"], aaji.legacy_id)
        self.assertEqual(captured["legacy_name"], "Aaji")
        self.assertEqual(captured["relationship"], "grandmother")
        self.assertEqual(result["status"], "supported")
        identity = RealtimeToolService(CapturingChatService()).execute(
            self.db, session, "get_legacy_identity_context", {"query": "Who are you?"}
        )
        self.assertEqual(identity["identity_count"], 1)
        self.assertEqual(identity["identity"][0]["value"], "Aaji")

        with patch("app.api.v1.live_call.get_settings", return_value=settings):
            granny_created = self.client.post(
                "/api/v1/live-call/session",
                json={"legacy_id": self.legacy.legacy_id, "engine": "realtime"},
            ).json()
        granny_session = live_call_sessions.authorize_user(
            granny_created["session_id"], self.owner.user_id
        )
        granny_instructions = build_realtime_session_payload(settings, granny_session)["session"]["instructions"]
        self.assertIn("speak from Granny's first-person perspective", granny_instructions)
        self.assertNotIn("Aaji", granny_instructions)

    def test_realtime_tool_timeout_returns_bounded_failure_result(self):
        UserCRUD.set_conversation_preferences(
            self.db, self.owner.user_id, voice="cedar",
            conversation_style="natural", response_length="balanced",
        )
        settings = SimpleNamespace(
            live_call_realtime_enabled=True,
            default_standard_voice_profile="standard_female",
            live_call_realtime_tool_timeout_seconds=0.01,
        )
        slow_tools = SimpleNamespace(
            execute=lambda *args: (time.sleep(0.05) or {"status": "grounded"})
        )
        app.dependency_overrides[get_realtime_tool_service] = lambda: slow_tools
        with patch("app.api.v1.live_call.get_settings", return_value=settings):
            session = self.client.post(
                "/api/v1/live-call/session",
                json={"legacy_id": self.legacy.legacy_id, "engine": "realtime"},
            ).json()
            response = self.client.post(
                f"/api/v1/live-call/realtime/{session['session_id']}/tool",
                json={"call_id": "slow-call", "name": "get_legacy_identity_context", "arguments": {}},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], {"status": "error", "uncertain": True})

    def test_realtime_identity_tool_is_session_authorized_and_scoped(self):
        UserCRUD.set_conversation_preferences(
            self.db, self.owner.user_id, voice="cedar",
            conversation_style="natural", response_length="balanced",
        )
        settings = SimpleNamespace(
            live_call_realtime_enabled=True,
            default_standard_voice_profile="standard_female",
        )
        fake_tools = SimpleNamespace(execute=lambda db, session, name, arguments, call_id=None: {
            "status": "grounded",
            "legacy": {"name": session.legacy_name, "relationship": session.relationship},
        })
        app.dependency_overrides[get_realtime_tool_service] = lambda: fake_tools
        with patch("app.api.v1.live_call.get_settings", return_value=settings):
            session = self.client.post(
                "/api/v1/live-call/session",
                json={"legacy_id": self.legacy.legacy_id, "engine": "realtime"},
            ).json()
        response = self.client.post(
            f"/api/v1/live-call/realtime/{session['session_id']}/tool",
            json={"call_id": "call-1", "name": "get_legacy_identity_context", "arguments": {}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["legacy"]["name"], "Granny")
        app.dependency_overrides[get_current_user] = lambda: self.other
        denied = self.client.post(
            f"/api/v1/live-call/realtime/{session['session_id']}/tool",
            json={"call_id": "call-2", "name": "get_legacy_identity_context", "arguments": {}},
        )
        self.assertEqual(denied.status_code, 404)

    def test_realtime_session_operations_are_owned_ended_and_argument_scoped(self):
        UserCRUD.set_conversation_preferences(
            self.db, self.owner.user_id, voice="marin",
            conversation_style="natural", response_length="balanced",
        )
        settings = SimpleNamespace(
            live_call_realtime_enabled=True, live_call_realtime_strict=True,
            default_standard_voice_profile="standard_female",
            openai_realtime_model="gpt-realtime-test",
            openai_realtime_vad_threshold=0.60,
            live_call_realtime_tool_timeout_seconds=1.0,
        )
        app.dependency_overrides[get_realtime_bootstrap_provider] = FakeRealtimeBootstrapProvider
        with patch("app.api.v1.live_call.get_settings", return_value=settings):
            session = self.client.post(
                "/api/v1/live-call/session",
                json={"legacy_id": self.legacy.legacy_id, "engine": "realtime"},
            ).json()
            app.dependency_overrides[get_current_user] = lambda: self.other
            self.assertEqual(self.client.post(
                f"/api/v1/live-call/realtime/{session['session_id']}/bootstrap"
            ).status_code, 404)
            self.assertEqual(self.client.post(
                f"/api/v1/live-call/realtime/{session['session_id']}/tool",
                json={"call_id": "other", "name": "retrieve_legacy_memory_context",
                      "arguments": {"query": "family"}},
            ).status_code, 404)

            app.dependency_overrides[get_current_user] = lambda: self.owner
            override = self.client.post(
                f"/api/v1/live-call/realtime/{session['session_id']}/tool",
                json={"call_id": "override", "name": "retrieve_legacy_memory_context",
                      "arguments": {"query": "family", "legacy_id": self.other_legacy.legacy_id}},
            )
            self.assertEqual(override.status_code, 200)
            self.assertEqual(override.json()["result"]["status"], "error")
            self.assertEqual(self.client.delete(
                f"/api/v1/live-call/session/{session['session_id']}"
            ).status_code, 200)
            for endpoint in ("bootstrap", "tool"):
                response = self.client.post(
                    f"/api/v1/live-call/realtime/{session['session_id']}/{endpoint}",
                    json=({"call_id": "ended", "name": "retrieve_legacy_memory_context",
                           "arguments": {"query": "family"}} if endpoint == "tool" else None),
                )
                self.assertEqual(response.status_code, 404)

    def test_live_call_source_contract_does_not_log_or_persist_private_payloads(self):
        root = Path(__file__).resolve().parents[1]
        sources = "\n".join((root / relative).read_text(encoding="utf-8") for relative in (
            "app/api/v1/live_call.py", "app/services/live_call.py",
            "app/services/realtime_live_call.py",
        ))
        for forbidden in (
            "logger.info(transcript", "logger.debug(transcript", "logger.info(request.text",
            "logger.debug(request.text", "logger.info(result)", "logger.debug(result)",
            "db.add(runtime", "db.add(audio", "localStorage", "sessionStorage",
        ):
            self.assertNotIn(forbidden, sources)
        self.assertIn('session_id=uuid4().hex', sources)
        self.assertIn('transport_token=secrets.token_urlsafe(32)', sources)

    def test_realtime_memory_adapter_reuses_followup_state_and_deduplicates(self):
        calls = []

        class MemoryChat:
            def prepare_live_call_input(self, _db, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    messages=(SimpleNamespace(role="system", content="compact grounded evidence"),),
                    memory_ids=(11,), identity_direct=False, identity_count=0,
                    conflict_count=0, has_uncertainty=True,
                    resolved_entities=("Meenakshi",),
                    memory_evidence=({"memory_id": 11, "title": "Goa", "summary": "Trip",
                        "uncertainty": "possibly 1986", "conflict": False},),
                )

        session = SimpleNamespace(
            session_id="memory-session", user_id=7, legacy_id=9,
            legacy_name="Aaji", relationship="grandmother",
        )
        service = RealtimeToolService(MemoryChat())
        first = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {"query": "Tell me about Goa"},
        )
        duplicate = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {"query": "Tell me about Goa"},
        )
        followup = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {"query": "What happened after that?"},
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(first, duplicate)
        self.assertEqual(first["status"], "supported")
        self.assertTrue(first["uncertain"])
        self.assertNotIn("memory_id", first["memories"][0])
        self.assertEqual(followup["followup_context"], "active")
        self.assertEqual(calls[1]["history"][0].content, "Tell me about Goa")
        self.assertEqual(calls[1]["user_id"], 7)
        self.assertEqual(calls[1]["legacy_id"], 9)

    def test_realtime_memory_adapter_preserves_chat_family_and_trip_evidence(self):
        calls = []

        class ParityChat:
            def prepare_live_call_input(self, _db, **kwargs):
                calls.append(kwargs)
                query = kwargs["user_message"]
                family = "family" in query.casefold() or "who else" in query.casefold()
                if family:
                    memories = (
                        {"memory_id": 1, "title": "My husband", "summary": "My husband is Rohan Deshmukh.",
                         "uncertainty": None, "conflict": False, "subject": "self", "subjects": ["self"]},
                        {"memory_id": 2, "title": "My brother", "summary": "My younger brother is Aditya Deshmukh; we grew up in Pune.",
                         "uncertainty": None, "conflict": False, "subject": "self", "subjects": ["self"]},
                    )
                    identities = (
                        {"fact_type": "spouse_name", "value": "Rohan Deshmukh", "relationship": "husband",
                         "conflicting": False, "uncertainty_note": None},
                        {"fact_type": "sibling_name", "value": "Aditya Deshmukh", "relationship": "younger brother",
                         "conflicting": False, "uncertainty_note": None},
                    )
                    ids = (1, 2)
                else:
                    memories = ({
                        "memory_id": 3, "title": "First winter trip to Kashmir",
                        "summary": "At 24 I went with Rohan, had a snow fight near the hotel, then he made hot tea and we watched snowfall by the window; quiet family time mattered most.",
                        "uncertainty": None, "conflict": False, "subject": "self", "subjects": ["self"],
                    },)
                    identities, ids = (), (3,)
                return SimpleNamespace(
                    messages=(), memory_ids=ids, identity_direct=bool(identities),
                    identity_count=len(identities), identity_evidence=identities,
                    conflict_count=0, has_uncertainty=False, resolved_entities=(),
                    memory_evidence=memories, query_intent="family" if family else "trip",
                    matched_candidate_count=len(memories), grounding_chars=500,
                    identity_context_chars=100 if identities else 0,
                )

        session = SimpleNamespace(
            session_id="parity-session", user_id=7, legacy_id=9,
            legacy_name="Aaji", relationship="grandmother",
        )
        service = RealtimeToolService(ParityChat())
        family = service.execute(
            self.db, session, "retrieve_legacy_memory_context",
            {"query": "Tell me about your family"},
        )
        self.assertEqual([item["value"] for item in family["identity"]],
                         ["Rohan Deshmukh", "Aditya Deshmukh"])
        self.assertEqual([item["title"] for item in family["memories"]],
                         ["My husband", "My brother"])
        self.assertTrue(all("memory_id" not in item for item in family["memories"]))

        followup = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {"query": "Who else?"},
        )
        self.assertEqual(followup["followup_context"], "active")
        self.assertIn("Tell me about your family", calls[-1]["history"][0].content)
        self.assertIn("Aditya Deshmukh", str(followup))

        trips = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {"query": "Tell me about trips"},
        )
        self.assertIn("Kashmir", str(trips))
        self.assertIn("snow fight", str(trips))
        self.assertIn("hot tea", str(trips))
        self.assertIn("quiet family time", str(trips))

        precise = service.execute(
            self.db, session, "retrieve_legacy_memory_context",
            {"query": "What about the Kashmir trip"},
        )
        self.assertEqual(precise["memories"], trips["memories"])

    def test_realtime_tool_contract_routes_broad_biography_through_shared_chat_memory(self):
        tool_descriptions = {tool["name"]: tool["description"] for tool in REALTIME_TOOLS}
        self.assertIn("direct single identity fact", tool_descriptions["get_legacy_identity_context"])
        memory_description = tool_descriptions["retrieve_legacy_memory_context"]
        for topic in ("family", "life", "childhood", "trip"):
            self.assertIn(topic, memory_description)
        instructions = session_instructions(SimpleNamespace(
            legacy_name="Aaji", relationship="grandmother",
            conversation_style="natural", response_length="balanced",
        ))
        self.assertIn("Use the identity tool only for a direct single identity fact", instructions)
        self.assertIn("especially broad family, life, childhood, or trip", instructions)

    def test_realtime_memory_routing_is_deterministic_and_overrides_model_choice(self):
        service = RealtimeToolService(SimpleNamespace())
        empty = RealtimeMemoryState()
        family_routes = [service._route("Tell me about your family", empty) for _ in range(20)]
        trip_routes = [service._route("Tell me about your trips", empty) for _ in range(20)]
        self.assertEqual(family_routes, ["broad_memory"] * 20)
        self.assertEqual(trip_routes, ["broad_memory"] * 20)
        self.assertEqual(service._route("Tell me about the Kashmir trip", empty), "episode")
        self.assertEqual(service._route("What happened in Goa?", empty), "episode")
        self.assertEqual(service._route("Who is your husband?", empty), "identity")
        self.assertEqual(service._route("Who is Meenakshi?", empty), "identity")
        self.assertEqual(service._route("How are you?", empty), "social")
        self.assertEqual(service._route("I passed my exam!", empty), "social")
        family_state = RealtimeMemoryState(last_query="Tell me about your family")
        trip_state = RealtimeMemoryState(last_query="Tell me about your trips")
        self.assertEqual(service._route("Who else?", family_state), "followup")
        self.assertEqual(service._route("Who went with you?", trip_state), "followup")

        calls = []
        class RoutedChat:
            def retrieve_live_call_identity(self, _db, **kwargs):
                calls.append(("identity", kwargs["query"]))
                return (
                    SimpleNamespace(records=({"fact_type": "spouse_name", "value": "Rohan",
                        "relationship": "husband", "conflicting": False,
                        "uncertainty_note": None},), candidate_count=1,
                        conflict_present=False),
                    SimpleNamespace(canonical_value="Rohan"),
                )
            def prepare_live_call_input(self, _db, **kwargs):
                calls.append(("memory", kwargs["user_message"]))
                supported = "unknown" not in kwargs["user_message"].casefold()
                return SimpleNamespace(
                    memory_ids=(1,) if supported else (), identity_direct=False,
                    identity_evidence=(), identity_count=0, conflict_count=0,
                    has_uncertainty=False, resolved_entities=(),
                    memory_evidence=({"memory_id": 1, "summary": "Kashmir"},) if supported else (),
                    query_intent="family", matched_candidate_count=int(supported),
                    grounding_chars=20 if supported else 0, identity_context_chars=0,
                )

        routed = RealtimeToolService(RoutedChat())
        session = SimpleNamespace(session_id="routing-session", user_id=1, legacy_id=2,
                                  legacy_name="Aaji", relationship="grandmother")
        family = routed.execute(self.db, session, "get_legacy_identity_context",
                                {"query": "Tell me about your family"}, "family-call")
        self.assertEqual(calls[-1][0], "memory")
        self.assertEqual(family["status"], "supported")
        husband = routed.execute(self.db, session, "retrieve_legacy_memory_context",
                                 {"query": "Who is your husband?"}, "husband-call")
        self.assertEqual(calls[-1][0], "identity")
        self.assertEqual(husband["identity"][0]["value"], "Rohan")
        before_social = len(calls)
        social = routed.execute(self.db, session, "retrieve_legacy_memory_context",
                                {"query": "How are you?"}, "social-call")
        self.assertEqual((social["status"], len(calls)), ("not_required", before_social))
        unsupported = routed.execute(self.db, session, "get_legacy_identity_context",
                                     {"query": "Tell me about unknown trips"}, "unknown-call")
        self.assertEqual(unsupported["status"], "unsupported")
        call_count = len(calls)
        duplicate = routed.execute(self.db, session, "get_legacy_identity_context",
                                   {"query": "Tell me about unknown trips"}, "unknown-call")
        self.assertEqual(duplicate, unsupported)
        self.assertEqual(len(calls), call_count)

    def test_realtime_identity_adapter_preserves_conflict_and_unsupported_status(self):
        class IdentityChat:
            def __init__(self): self.calls = 0
            def retrieve_live_call_identity(self, _db, **kwargs):
                self.calls += 1
                records = () if "Tokyo" in kwargs["query"] else (
                    {"fact_type": "spouse_name", "value": "Meenakshi",
                     "relationship": "wife", "conflicting": True,
                     "uncertainty_note": "two approved versions"},
                )
                return (
                    SimpleNamespace(records=records, candidate_count=len(records),
                                    conflict_present=bool(records)),
                    SimpleNamespace(canonical_value="Meenakshi" if records else None),
                )

        session = SimpleNamespace(
            session_id="identity-session", user_id=3, legacy_id=4,
            legacy_name="Aaji", relationship="grandmother",
        )
        service = RealtimeToolService(IdentityChat())
        conflict = service.execute(
            self.db, session, "get_legacy_identity_context", {"query": "Who is Meenakshi?"},
        )
        unsupported = service.execute(
            self.db, session, "get_legacy_identity_context",
            {"query": "Who was the Tokyo restaurant owner?"},
        )
        relationship = service.execute(
            self.db, session, "get_legacy_identity_context", {"query": "Who am I to you?"},
        )
        self.assertEqual(conflict["status"], "conflicted")
        self.assertEqual(conflict["conflict_count"], 1)
        self.assertEqual(unsupported["status"], "unsupported")
        self.assertEqual(unsupported["identity"], [])
        self.assertEqual(relationship["status"], "supported")
        self.assertEqual(relationship["selected_legacy"], {
            "name": "Aaji", "relationship_to_user": "grandmother", "role": "self",
        })
        self.assertEqual(conflict["identity"][0]["perspective_owner"], "self")

    def test_realtime_memory_perspective_uses_structured_subject_roles_without_rewriting(self):
        def memory(names, roles):
            return SimpleNamespace(participant_names=names, participant_roles=roles)

        own = memory(["Aaji"], ["subject"])
        other = memory(["Anjali"], ["subject"])
        mixed = memory(["Aaji", "Meenakshi"], ["subject", "subject"])
        mentioned = memory(["Aaji", "Anjali"], ["subject", "mentioned_person"])
        missing = memory(["Aaji"], [None])
        canonical_names = tuple(mixed.participant_names)

        self.assertEqual(ChatService._memory_perspective(own, "Aaji"), {
            "subject": "self", "subjects": ["self"],
        })
        self.assertEqual(ChatService._memory_perspective(other, "Aaji"), {
            "subject": "Anjali", "subjects": ["Anjali"],
        })
        self.assertEqual(ChatService._memory_perspective(mixed, "Aaji"), {
            "subject": "multiple", "subjects": ["self", "Meenakshi"],
        })
        self.assertEqual(ChatService._memory_perspective(mentioned, "Aaji")["subject"], "self")
        self.assertEqual(ChatService._memory_perspective(missing, "Aaji")["subject"], "uncertain")
        self.assertEqual(tuple(mixed.participant_names), canonical_names)

    def test_realtime_response_policy_makes_grounding_invisible_when_answer_is_supported(self):
        session = SimpleNamespace(
            legacy_name="Aaji", relationship="grandmother",
            conversation_style="gentle", response_length="short",
        )
        instructions = session_instructions(session)

        self.assertIn("Supported facts: answer directly with no source or completeness disclaimer", instructions)
        self.assertIn("Partial information: say only the known part and stop", instructions)
        self.assertIn("Preserve names and uncertainty exactly", instructions)
        self.assertIn("I remember it in two different ways", instructions)
        self.assertIn("briefly say 'I don't remember that' and stop", instructions)
        self.assertIn("INTERNAL ONLY", instructions)
        self.assertIn("speak from Aaji's first-person perspective", instructions)

    def test_realtime_tool_first_policy_prohibits_meta_memory_but_allows_human_recall(self):
        session = SimpleNamespace(
            legacy_name="Aaji", relationship="grandmother",
            conversation_style="gentle", response_length="short",
        )
        instructions = session_instructions(session).casefold()

        self.assertIn("call it before producing any spoken content", instructions)
        self.assertIn("brief silence is better than narrating processing", instructions)
        self.assertIn("internal only", instructions)
        for prohibited in (
            "let me check my memories",
            "according to my memories",
            "the memory says",
            "i found a memory",
        ):
            if prohibited == "let me check my memories":
                self.assertIn("let me check", instructions)
            else:
                self.assertIn(prohibited, instructions)
        self.assertIn("speak from aaji's first-person perspective", instructions)
        self.assertIn("apply all factual constraints silently", instructions)

    def test_realtime_invisible_grounding_firewall_is_direct_human_and_concise(self):
        instructions = session_instructions(SimpleNamespace(
            legacy_name="Aaji", relationship="grandmother",
            conversation_style="natural", response_length="balanced",
        ))
        lowered = instructions.casefold()

        for internal_term in (
            "preserved", "stored", "recorded", "available", "provided information",
            "evidence", "grounding", "verification", "context", "records", "memory data",
            "retrieval", "tool calls", "database",
        ):
            self.assertIn(internal_term, lowered)
        for prohibited_narration in (
            "let me check", "stay within", "according to the information i have",
            "according to my memories", "that's all i know", "avoiding guessing",
        ):
            self.assertIn(prohibited_narration, lowered)
        self.assertIn("never mention or paraphrase those concepts", lowered)
        self.assertIn("my husband is madhav, and anjali is my daughter", lowered)
        self.assertIn("broad family, childhood, life, or trip question", lowered)
        self.assertIn("no source or completeness disclaimer", lowered)
        self.assertIn("partial information: say only the known part and stop", lowered)
        self.assertIn("i don't remember that", lowered)
        self.assertIn("i'm not completely sure. i remember it in two different ways", lowered)
        self.assertIn("natural human recall", lowered)
        self.assertIn("i remember goa", lowered)
        self.assertIn("call a required tool before producing any spoken content", lowered)
        self.assertIn("speak from aaji's first-person perspective", lowered)
        self.assertIn("creative present-moment warmth", lowered)
        self.assertIn("balanced speech: usually 2-4 sentences", lowered)

    def test_realtime_natural_conversation_contract_respects_style_length_and_turn_economy(self):
        def instructions(style="natural", length="balanced"):
            return session_instructions(SimpleNamespace(
                legacy_name="Aaji", relationship="grandmother",
                conversation_style=style, response_length=length,
            ))

        balanced = instructions()
        self.assertIn("BALANCED speech: usually 2-4 sentences", balanced)
        self.assertIn("Ordinary turns should be about 1-3 spoken sentences", balanced)
        self.assertIn("simple facts often one sentence", balanced)
        self.assertIn("answer the immediate question plus at most one useful detail", balanced.casefold())
        self.assertIn("Expand naturally only for an explicit story", balanced)
        self.assertIn("Occasional brief acknowledgements", balanced)
        self.assertIn("neither is mandatory", balanced)
        self.assertIn("React naturally to statements", balanced)
        self.assertIn("If interrupted, answer the new user turn", balanced)
        self.assertIn("Follow abrupt topic changes", balanced)
        self.assertIn("Interpret short turns", balanced)
        self.assertIn("no Markdown, headings, numbered framing, bullet-list speech", balanced)
        self.assertIn("Follow the language and code-switching style", balanced)
        self.assertIn("then stop", balanced)
        self.assertIn("Do not end each answer with a question or invitation", balanced)
        self.assertIn("a genuine follow-up question are allowed", balanced)
        for service_phrase in (
            "How can I help?", "Feel free to ask", "Would you like me to elaborate?",
            "Certainly!", "Is there anything else?",
        ):
            self.assertIn(service_phrase, balanced)

        self.assertIn("SHORT speech: usually 1-2 sentences", instructions(length="short"))
        self.assertIn("DETAILED speech: give richer answers", instructions(length="detailed"))
        self.assertIn("GENTLE: use slightly softer wording", instructions(style="gentle"))
        self.assertIn("EXPRESSIVE: react with somewhat more animation", instructions(style="expressive"))
        self.assertLess(len(balanced), 5000)

    def test_realtime_personality_is_snapshotted_from_approved_style_evidence(self):
        profile = PersonaProfile(
            greetings=("Arre wah",), nicknames=("Beta",),
            recurring_expressions=("Eat something first",),
            tone_markers=("playful humour", "warm"),
        )
        session = SimpleNamespace(
            legacy_name="Aaji", relationship="grandmother",
            conversation_style="gentle", response_length="short",
            persona_profile=profile,
        )
        instructions = session_instructions(session)

        self.assertIn('"nicknames":["Beta"]', instructions)
        self.assertIn('"tone_markers":["playful humour","warm"]', instructions)
        self.assertIn("prefer them and use exact nicknames", instructions)
        self.assertIn("With no clues, use only the relationship prior", instructions)
        self.assertIn("Keep the character consistent during the call", instructions)
        self.assertIn("Creative present-moment warmth, affection, concern, gentle humor", instructions)
        self.assertIn("allowed but optional", instructions)
        self.assertIn("never store them or turn them into facts", instructions)
        self.assertIn("Never invent concrete biography", instructions)
        self.assertIn("Give the factual answer before any optional personality touch", instructions)
        self.assertIn("relationship describes who this Companion is to the user; never reverse it", instructions)
        self.assertIn("GENTLE:", instructions)
        self.assertIn("SHORT speech:", instructions)
        for forbidden_roleplay in ("As Aaji", "as your AI Companion", "in character"):
            self.assertIn(forbidden_roleplay, instructions)

    def test_realtime_relationship_priors_are_soft_distinct_and_nonbiographical(self):
        grandmother = relationship_personality_prior("grandmother")
        friend = relationship_personality_prior("friend")
        sibling = relationship_personality_prior("sister")
        partner = relationship_personality_prior("husband")

        self.assertIn("grandparent warmth", grandmother)
        self.assertIn("friend-like warmth", friend)
        self.assertIn("sibling warmth", sibling)
        self.assertIn("partner-like warmth", partner)
        self.assertEqual(len({grandmother, friend, sibling, partner}), 4)
        for prior in (grandmother, friend, sibling, partner):
            self.assertNotIn("used to", prior.casefold())
            self.assertNotIn("always", prior.casefold())

    def test_live_call_session_keeps_one_frozen_personality_snapshot(self):
        profile = PersonaProfile(tone_markers=("warm",))
        session = LiveCallSessionStore().create(
            user_id=1, legacy_id=2, legacy_name="Aaji", relationship="grandmother",
            effective_voice="marin", persona_profile=profile,
        )
        self.assertIs(session.persona_profile, profile)
        self.assertEqual(session.persona_profile.tone_markers, ("warm",))
        self.assertFalse(hasattr(session.persona_profile, "save"))

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

    def test_turn_failure_is_stage_classified_and_session_remains_usable(self):
        class FailingTurn(FakeLiveCallTurnService):
            async def process(self, **kwargs):
                raise RuntimeError("provider detail must remain private")

        session = self.create_session()
        protocols = ["waffleberry.live-call.v1", f"auth.{session['transport_token']}"]
        with patch("app.api.v1.live_call.get_live_call_turn_service", return_value=FailingTurn()):
            with self.client.websocket_connect(
                f"/api/v1/live-call/ws/{session['session_id']}", subprotocols=protocols
            ) as websocket:
                websocket.receive_json()
                websocket.send_json({
                    "version": 1, "type": "audio.chunk", "turn_id": 1,
                    "start": True, "mime_type": "audio/webm",
                    "data": base64.b64encode(b"fake-webm").decode("ascii"),
                })
                websocket.send_json({"version": 1, "type": "audio.commit", "turn_id": 1})
                self.assertEqual(websocket.receive_json()["type"], "latency.commit_received")
                self.assertEqual(websocket.receive_json()["type"], "response.started")
                failure = websocket.receive_json()
                self.assertEqual(failure["code"], "turn_failed")
                self.assertEqual(failure["failure_stage"], "turn_processing")
                self.assertNotIn("provider", str(failure))
                websocket.send_json({"version": 1, "type": "heartbeat.ping", "heartbeat_id": 3})
                pong = websocket.receive_json()
                self.assertIsNone(pong["active_turn_id"])
                self.assertEqual(pong["next_turn_id"], 2)
                websocket.send_json({"version": 1, "type": "session.end"})
                websocket.receive_json()

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

    def test_greeting_retry_preserves_the_snapshotted_voice_route(self):
        class UnusedAI:
            pass

        class UnusedTranscription:
            pass

        class RetrySpeech:
            def __init__(self):
                self.calls = []

            async def synthesize(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise RuntimeError("optional delivery control failed")
                return SpeechResult(b"greeting", "audio/mpeg", "mp3")

        speech = RetrySpeech()
        service = LiveCallTurnService(
            UnusedTranscription(), UnusedAI(), ContextBuilder(10), speech, speech,
        )
        active = live_call_sessions.authorize_transport(
            (payload := self.create_session())["session_id"], payload["transport_token"]
        )
        asyncio.run(service.greeting(session=active))
        self.assertEqual(len(speech.calls), 2)
        self.assertEqual(
            speech.calls[0]["standard_voice_profile"],
            speech.calls[1]["standard_voice_profile"],
        )


if __name__ == "__main__":
    unittest.main()
