"""Phase 10.0 authenticated ephemeral Live Call foundation tests."""

import unittest
import asyncio
import base64
import inspect
import json
import time
from decimal import Decimal
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
from app.crud.user import ConversationCRUD, MessageCRUD, UserCRUD
from app.db import Base, get_db
from app.dependencies.auth import get_current_user
from app.dependencies.ai import (
    get_ai_service, get_grounded_answer_service, get_realtime_bootstrap_provider,
    get_realtime_tool_service,
)
from app.main import app
from app.models.user import Message, MessageRole, User
from app.models.memory import Memory, MemoryProvenance, MemoryReviewStatus, MemoryType
from app.schemas.memory import LegacyCreate
from app.services.live_call import LiveCallSessionStore, LiveCallTurnService, live_call_sessions
from app.services.ai.provider import SpeechResult
from app.services.ai.exceptions import AIProviderError
from app.services.ai.context_builder import ContextBuilder
from app.services.chat_service import ChatService
from app.services.grounded_answer import GroundedAnswerService
from app.services.memory.identity_facts import IdentityFactProjectionService
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

    def create_session(self, engine=None):
        payload = {"legacy_id": self.legacy.legacy_id}
        if engine is not None:
            payload["engine"] = engine
        response = self.client.post(
            "/api/v1/live-call/session",
            json=payload,
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
        self.assertGreater(first["conversation_id"], 0)

    def test_live_call_attaches_only_to_owned_matching_legacy_conversation(self):
        matching = ConversationCRUD.create_conversation(
            self.db, self.owner.user_id, legacy_id=self.legacy.legacy_id,
        )
        MessageCRUD.create_message_pair(
            self.db, matching, "Tell me about your sibling.",
            "My sibling is the supported person.",
        )
        attached = self.client.post(
            "/api/v1/live-call/session",
            json={"legacy_id": self.legacy.legacy_id,
                  "conversation_id": matching.conversation_id},
        )
        self.assertEqual(attached.status_code, 201)
        self.assertEqual(attached.json()["conversation_id"], matching.conversation_id)
        stored_session = live_call_sessions.authorize_user(
            attached.json()["session_id"], self.owner.user_id,
        )
        self.assertIn("Tell me about your sibling", stored_session.conversation_context)
        self.assertIn("My sibling is the supported person", stored_session.conversation_context)

        wrong_legacy = ConversationCRUD.create_conversation(
            self.db, self.owner.user_id, legacy_id=self.other_legacy.legacy_id,
        )
        self.assertEqual(self.client.post(
            "/api/v1/live-call/session",
            json={"legacy_id": self.legacy.legacy_id,
                  "conversation_id": wrong_legacy.conversation_id},
        ).status_code, 409)
        wrong_user = ConversationCRUD.create_conversation(
            self.db, self.other.user_id, legacy_id=self.legacy.legacy_id,
        )
        self.assertEqual(self.client.post(
            "/api/v1/live-call/session",
            json={"legacy_id": self.legacy.legacy_id,
                  "conversation_id": wrong_user.conversation_id},
        ).status_code, 404)

    def test_live_call_message_source_keys_are_durable_and_idempotent(self):
        conversation = ConversationCRUD.create_conversation(
            self.db, self.owner.user_id, legacy_id=self.legacy.legacy_id,
        )
        first, created = MessageCRUD.create_idempotent_source_message(
            self.db, conversation, role=MessageRole.USER, content="Same words",
            source_session_id="00000000-0000-0000-0000-000000000001",
            source_event_id="turn:1",
        )
        duplicate, duplicate_created = MessageCRUD.create_idempotent_source_message(
            self.db, conversation, role=MessageRole.USER, content="Same words",
            source_session_id="00000000-0000-0000-0000-000000000001",
            source_event_id="turn:1",
        )
        second, second_created = MessageCRUD.create_idempotent_source_message(
            self.db, conversation, role=MessageRole.USER, content="Same words",
            source_session_id="00000000-0000-0000-0000-000000000001",
            source_event_id="turn:2",
        )
        other_session, other_created = MessageCRUD.create_idempotent_source_message(
            self.db, conversation, role=MessageRole.USER, content="Same words",
            source_session_id="00000000-0000-0000-0000-000000000002",
            source_event_id="turn:1",
        )
        chat = Message(conversation_id=conversation.conversation_id,
                       role=MessageRole.USER, content="Same words")
        self.db.add(chat)
        self.db.commit()
        self.assertEqual(first.message_id, duplicate.message_id)
        self.assertEqual((created, duplicate_created, second_created, other_created),
                         (True, False, True, True))
        self.assertEqual(len({first.message_id, second.message_id,
                              other_session.message_id, chat.message_id}), 4)

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
        session = self.create_session(engine="cascade")
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
            openai_realtime_model="gpt-realtime-test",
            openai_realtime_vad_threshold=0.60,
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
        self.assertIn("authoritative active Legacy", payload["instructions"])
        self.assertIn("Never identify as ChatGPT", payload["instructions"])
        self.assertIn("Keep other people distinct", payload["instructions"])
        self.assertIn("Persona affects grammatical perspective only", payload["instructions"])
        self.assertNotIn("Companion for Aaji", payload["instructions"])
        self.assertEqual(payload["output_modalities"], ["audio"])
        self.assertEqual(set(payload["audio"]["input"]), {"turn_detection", "transcription"})
        self.assertEqual(payload["audio"]["input"]["transcription"]["model"], "gpt-live-transcribe")
        vad = payload["audio"]["input"]["turn_detection"]
        self.assertEqual(vad, {
            "type": "server_vad", "threshold": 0.60, "prefix_padding_ms": 400,
            "silence_duration_ms": 1400, "create_response": False,
            "interrupt_response": False,
        })
        self.assertEqual(payload["audio"]["output"]["voice"], "marin")
        self.assertEqual(payload["tool_choice"], "auto")
        for fragment in (
            "ordinary social conversation", "general-knowledge questions",
            "answer directly", "without a tool",
        ):
            self.assertIn(fragment, payload["instructions"])
        self.assertIn("answer directly without a tool", payload["instructions"])

        captured = {}
        class CapturingChatService:
            def prepare_conversation_live_call_input(self, _db, **kwargs):
                captured.update(kwargs)
                identity_query = "full name" in kwargs["user_message"].casefold()
                return SimpleNamespace(
                    messages=(), memory_ids=(() if identity_query else (1,)),
                    identity_direct=identity_query,
                    identity_evidence=(({
                        "fact_type": "full_name", "value": "Aaji", "relationship": None,
                        "conflicting": False, "uncertainty_note": None,
                    },) if identity_query else ()),
                    conflict_count=0, identity_count=int(identity_query),
                    has_uncertainty=False, resolved_entities=(),
                    memory_evidence=(() if identity_query else ({
                        "memory_id": 1, "summary": "A Goa trip",
                    },)),
                    query_intent=("identity" if identity_query else "trip"),
                    matched_candidate_count=1,
                    grounding_chars=20, identity_context_chars=0,
                )
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

        tools = RealtimeToolService(CapturingChatService())
        tools.route_turn(session, 1, "Tell me about your trip to Goa")
        result = tools.execute(
            self.db, session, "retrieve_legacy_memory_context", {}, turn_id=1,
        )
        self.assertEqual(captured["conversation"].legacy_id, aaji.legacy_id)
        self.assertEqual(captured["conversation"].user_id, self.owner.user_id)
        self.assertEqual(captured["user_message"], "Tell me about your trip to Goa")
        self.assertEqual(result["status"], "supported")
        tools.route_turn(session, 2, "What is your full name?")
        identity = tools.execute(
            self.db, session, "get_legacy_identity_context", {}, turn_id=2,
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
                json={"turn_id": 1, "call_id": "slow-call", "name": "get_legacy_identity_context", "arguments": {}},
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
        fake_tools = SimpleNamespace(
            route_turn=lambda session, turn_id, text: {
                "route": "identity", "tool_name": "get_legacy_identity_context",
            },
            execute=lambda db, session, name, arguments, call_id=None, turn_id=None: {
                "status": "grounded",
                "legacy": {"name": session.legacy_name, "relationship": session.relationship},
            },
            accepts_assistant_turn=lambda session, turn_id: turn_id == 1,
        )
        app.dependency_overrides[get_realtime_tool_service] = lambda: fake_tools
        with patch("app.api.v1.live_call.get_settings", return_value=settings):
            session = self.client.post(
                "/api/v1/live-call/session",
                json={"legacy_id": self.legacy.legacy_id, "engine": "realtime"},
            ).json()
        routed = self.client.post(
            f"/api/v1/live-call/realtime/{session['session_id']}/route",
            json={"turn_id": 1, "text": "Who was your husband?"},
        )
        self.assertEqual(routed.status_code, 200)
        self.assertEqual(routed.json(), {
            "route": "identity", "tool_name": "get_legacy_identity_context",
            "response_language": "english",
        })
        self.assertEqual(self.client.post(
            f"/api/v1/live-call/realtime/{session['session_id']}/route",
            json={"turn_id": 2, "text": "Who was your husband?", "legacy_id": 999},
        ).status_code, 422)
        response = self.client.post(
            f"/api/v1/live-call/realtime/{session['session_id']}/tool",
            json={"turn_id": 1, "call_id": "call-1", "name": "get_legacy_identity_context", "arguments": {}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["legacy"]["name"], "Granny")
        for _ in range(2):
            persisted = self.client.post(
                f"/api/v1/live-call/realtime/{session['session_id']}/assistant-turn",
                json={"turn_id": 1, "response_id": "visible-response-1",
                      "text": "My husband is the supported person."},
            )
            self.assertEqual(persisted.status_code, 202)
        messages = (
            self.db.query(Message)
            .filter(Message.conversation_id == session["conversation_id"])
            .order_by(Message.message_id)
            .all()
        )
        self.assertEqual([(item.role.value, item.content) for item in messages], [
            ("user", "Who was your husband?"),
            ("assistant", "My husband is the supported person."),
        ])
        app.dependency_overrides[get_current_user] = lambda: self.other
        self.assertEqual(self.client.post(
            f"/api/v1/live-call/realtime/{session['session_id']}/route",
            json={"turn_id": 2, "text": "Who was your husband?"},
        ).status_code, 404)
        denied = self.client.post(
            f"/api/v1/live-call/realtime/{session['session_id']}/tool",
            json={"turn_id": 1, "call_id": "call-2", "name": "get_legacy_identity_context", "arguments": {}},
        )
        self.assertEqual(denied.status_code, 404)

    def test_actual_http_family_and_self_identity_use_shared_profile_and_validated_renderer(self):
        self.legacy.display_name = "Anjali"
        for title, summary, claims in (
            ("Full name", "My full name is Anjali.", [{
                "fact_type": "full_name", "value": "Anjali", "confidence": 1,
            }]),
            ("Family", "My husband is Mohan and my younger brother is Aditya.", [{
                "fact_type": "spouse_name", "value": "Mohan",
                "relationship": "husband", "confidence": 1,
            }, {
                "fact_type": "sibling_name", "value": "Aditya",
                "relationship": "younger brother", "confidence": 1,
            }]),
        ):
            memory = Memory(
                legacy_id=self.legacy.legacy_id, memory_type=MemoryType.ATOMIC,
                category="identity", title=title, summary=summary,
                details={"identity_facts": claims},
                review_status=MemoryReviewStatus.APPROVED,
                extraction_confidence=Decimal("1"),
            )
            self.db.add(memory)
            self.db.flush()
            self.db.add(MemoryProvenance(
                memory_id=memory.memory_id, source_type="conversation",
                excerpt=summary, speaker="user",
            ))
            self.db.flush()
            IdentityFactProjectionService().project_memory(self.db, memory)
        self.db.commit()
        conversation = ConversationCRUD.create_conversation(
            self.db, self.owner.user_id, legacy_id=self.legacy.legacy_id,
        )
        stored_session = live_call_sessions.create(
            user_id=self.owner.user_id, legacy_id=self.legacy.legacy_id,
            legacy_name="Anjali", relationship=self.legacy.relationship,
            effective_voice="cedar", conversation_id=conversation.conversation_id,
            engine="realtime", speech_renderer="realtime_native",
            realtime_capable=True,
        )
        session = {"session_id": stored_session.session_id}

        class InvalidPersonaAI:
            async def generate_response(self, _messages, **_kwargs):
                return "I don't have a family in the human sense; I'm just an AI."

        chat = ChatService(SimpleNamespace(), ContextBuilder(12))
        tools = RealtimeToolService(chat)
        app.dependency_overrides[get_realtime_tool_service] = lambda: tools
        app.dependency_overrides[get_grounded_answer_service] = lambda: GroundedAnswerService(
            InvalidPersonaAI()
        )
        for turn_id, query, expected in (
            (1, "Who else is there in your family?", ("Mohan", "Aditya")),
            (2, "You are Anjali, right?", ("Anjali",)),
        ):
            routed = self.client.post(
                f"/api/v1/live-call/realtime/{session['session_id']}/route",
                json={"turn_id": turn_id, "text": query},
            )
            self.assertEqual(routed.status_code, 200)
            self.assertEqual(routed.json()["tool_name"], "retrieve_legacy_memory_context")
            grounded = self.client.post(
                f"/api/v1/live-call/realtime/{session['session_id']}/tool",
                json={"turn_id": turn_id, "call_id": f"call-{turn_id}",
                      "name": routed.json()["tool_name"], "arguments": {"query": query}},
            )
            self.assertEqual(grounded.status_code, 200)
            result = grounded.json()["result"]
            self.assertTrue(result["profile_engine_invoked"])
            self.assertGreater(result["profile_fact_count"], 0)
            self.assertEqual(result["retrieval_status"], "ok")
            self.assertEqual(result["answer_plan"]["status"], "supported")
            self.assertTrue(all(value in result["validated_text"] for value in expected))
            self.assertNotIn("just an AI", result["validated_text"])
            if turn_id == 1:
                family_text = result["validated_text"]
        late = self.client.post(
            f"/api/v1/live-call/realtime/{session['session_id']}/assistant-turn",
            json={"turn_id": 1, "response_id": "validated-call-1", "text": family_text,
                  "response_owner": "validated_personal", "playback_completed": True},
        )
        duplicate = self.client.post(
            f"/api/v1/live-call/realtime/{session['session_id']}/assistant-turn",
            json={"turn_id": 1, "response_id": "validated-call-1", "text": family_text,
                  "response_owner": "validated_personal", "playback_completed": True},
        )
        conflict = self.client.post(
            f"/api/v1/live-call/realtime/{session['session_id']}/assistant-turn",
            json={"turn_id": 1, "response_id": "native-conflict",
                  "text": "I don't have a family.",
                  "response_owner": "native_realtime", "playback_completed": True},
        )
        self.assertEqual((late.status_code, duplicate.status_code, conflict.status_code),
                         (202, 202, 202))
        self.assertEqual(late.json()["status"], "created")
        self.assertEqual(duplicate.json()["status"], "duplicate")
        self.assertEqual(conflict.json()["status"], "conflict")
        persisted = self.db.query(Message).filter(
            Message.conversation_id == conversation.conversation_id,
            Message.role == MessageRole.ASSISTANT,
        ).all()
        self.assertEqual([message.content for message in persisted], [family_text])

    def test_realtime_route_endpoint_separates_general_identity_and_family_turns(self):
        UserCRUD.set_conversation_preferences(
            self.db, self.owner.user_id, voice="cedar",
            conversation_style="natural", response_length="balanced",
        )
        settings = SimpleNamespace(
            live_call_realtime_enabled=True,
            default_standard_voice_profile="standard_female",
        )
        with patch("app.api.v1.live_call.get_settings", return_value=settings):
            session = self.client.post(
                "/api/v1/live-call/session",
                json={"legacy_id": self.legacy.legacy_id, "engine": "realtime"},
            ).json()
        endpoint = f"/api/v1/live-call/realtime/{session['session_id']}/route"
        cases = (
            (1, "What is the capital of Germany?", {"route": "direct", "tool_name": None,
                                                     "response_language": "english"}),
            (2, "Who was your husband?", {
                "route": "memory", "tool_name": "retrieve_legacy_memory_context",
                "response_language": "english",
            }),
            (3, "Tell me about your family", {
                "route": "memory", "tool_name": "retrieve_legacy_memory_context",
                "response_language": "english",
            }),
        )
        for turn_id, query, expected in cases:
            response = self.client.post(endpoint, json={"turn_id": turn_id, "text": query})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), expected)

    def test_normalization_provider_failure_never_turns_route_into_500(self):
        conversation = ConversationCRUD.create_conversation(
            self.db, self.owner.user_id, legacy_id=self.legacy.legacy_id,
        )
        session = live_call_sessions.create(
            user_id=self.owner.user_id, legacy_id=self.legacy.legacy_id,
            legacy_name="Granny", relationship=self.legacy.relationship,
            effective_voice="cedar", conversation_id=conversation.conversation_id,
            engine="realtime", speech_renderer="realtime_native", realtime_capable=True,
        )

        class FailingNormalizerAI:
            async def generate_response(self, _messages, **_options):
                raise AIProviderError("invalid_json_schema")

        tools = RealtimeToolService(ChatService(SimpleNamespace(), ContextBuilder(12)))
        app.dependency_overrides[get_realtime_tool_service] = lambda: tools
        app.dependency_overrides[get_ai_service] = FailingNormalizerAI
        response = self.client.post(
            f"/api/v1/live-call/realtime/{session.session_id}/route",
            json={"turn_id": 1, "text": "malasang tu kon ahe"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["response_language"], "english")

    def test_english_and_marathi_self_identity_have_identical_grounding_ownership(self):
        conversation = ConversationCRUD.create_conversation(
            self.db, self.owner.user_id, legacy_id=self.legacy.legacy_id,
        )
        session = live_call_sessions.create(
            user_id=self.owner.user_id, legacy_id=self.legacy.legacy_id,
            legacy_name="Anjali Deshmukh", relationship=self.legacy.relationship,
            effective_voice="cedar", conversation_id=conversation.conversation_id,
            engine="realtime", speech_renderer="realtime_native", realtime_capable=True,
        )

        class MarathiIdentityAI:
            async def generate_response(self, _messages, **_options):
                return json.dumps({
                    "detected_language": "marathi", "response_language": "marathi",
                    "normalized_english": "What is your name?", "code_switched": False,
                    "speech_act": "question", "substantive_intent": "personal_identity",
                    "translation_confidence": 0.99,
                })

        tools = RealtimeToolService(ChatService(SimpleNamespace(), ContextBuilder(12)))
        app.dependency_overrides[get_realtime_tool_service] = lambda: tools
        app.dependency_overrides[get_ai_service] = MarathiIdentityAI
        endpoint = f"/api/v1/live-call/realtime/{session.session_id}/route"
        english = self.client.post(endpoint, json={"turn_id": 1, "text": "What's your name?"})
        marathi = self.client.post(endpoint, json={"turn_id": 2, "text": "तुझं नाव काय आहे?"})
        self.assertEqual((english.status_code, marathi.status_code), (200, 200))
        self.assertEqual(
            {key: english.json()[key] for key in ("route", "tool_name")},
            {key: marathi.json()[key] for key in ("route", "tool_name")},
        )
        self.assertEqual(english.json()["response_language"], "english")
        self.assertEqual(marathi.json(), {
            "route": "memory", "tool_name": "retrieve_legacy_memory_context",
            "response_language": "marathi",
        })
        state = tools._state(session.session_id)
        self.assertEqual(state.turns[1].classification, "personal")
        self.assertEqual(state.turns[2].classification, "personal")
        self.assertEqual(state.turns[1].understanding.subject_type, "self")
        self.assertEqual(state.turns[2].understanding.subject_type, "self")

    def test_active_legacy_blocks_native_implementation_identity_persistence(self):
        conversation = ConversationCRUD.create_conversation(
            self.db, self.owner.user_id, legacy_id=self.legacy.legacy_id,
        )
        session = live_call_sessions.create(
            user_id=self.owner.user_id, legacy_id=self.legacy.legacy_id,
            legacy_name="Anjali Deshmukh", relationship=self.legacy.relationship,
            effective_voice="cedar", conversation_id=conversation.conversation_id,
            engine="realtime", speech_renderer="realtime_native", realtime_capable=True,
        )
        tools = RealtimeToolService(ChatService(SimpleNamespace(), ContextBuilder(12)))
        tools.route_turn(session, 1, "What is a mango?")
        self.assertEqual(tools.assistant_turn_decision(
            session, 1, "native-1", "You can call me ChatGPT. I'm an AI voice companion.",
            response_owner="native_realtime", playback_completed=True,
        ), "conflict")

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
                json={"turn_id": 1, "call_id": "other", "name": "retrieve_legacy_memory_context",
                      "arguments": {"query": "family"}},
            ).status_code, 404)

            app.dependency_overrides[get_current_user] = lambda: self.owner
            routed = self.client.post(
                f"/api/v1/live-call/realtime/{session['session_id']}/route",
                json={"turn_id": 1, "text": "Tell me about your family"},
            )
            self.assertEqual(routed.status_code, 200)
            override = self.client.post(
                f"/api/v1/live-call/realtime/{session['session_id']}/tool",
                json={"turn_id": 1, "call_id": "override", "name": "retrieve_legacy_memory_context",
                      "arguments": {"query": "family", "legacy_id": self.other_legacy.legacy_id}},
            )
            self.assertEqual(override.status_code, 200)
            self.assertIn(override.json()["result"]["status"], {"supported", "unsupported", "conflicted"})
            self.assertEqual(self.client.delete(
                f"/api/v1/live-call/session/{session['session_id']}"
            ).status_code, 200)
            for endpoint in ("bootstrap", "tool"):
                response = self.client.post(
                    f"/api/v1/live-call/realtime/{session['session_id']}/{endpoint}",
                    json=({"turn_id": 2, "call_id": "ended", "name": "retrieve_legacy_memory_context",
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
                    identity_evidence=(), query_intent="trip",
                    matched_candidate_count=1, grounding_chars=20,
                    identity_context_chars=0,
                )

        session = SimpleNamespace(
            session_id="memory-session", user_id=7, legacy_id=9,
            legacy_name="Aaji", relationship="grandmother",
        )
        service = RealtimeToolService(MemoryChat())
        service.route_turn(session, 1, "Tell me about your trip to Goa")
        first = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {},
            call_id="goa-call", turn_id=1,
        )
        duplicate = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {},
            call_id="goa-call", turn_id=1,
        )
        service.route_turn(session, 2, "What happened after that?")
        followup = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {},
            call_id="followup-call", turn_id=2,
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(duplicate, {"status": "cancelled", "uncertain": True})
        self.assertEqual(first["status"], "supported")
        self.assertTrue(first["uncertain"])
        self.assertEqual(first["memories"][0]["memory_id"], 11)
        self.assertEqual(first["selected_memory_ids"], [11])
        self.assertEqual(followup["followup_context"], "active")
        self.assertEqual(calls[1]["history"][0].content, "Tell me about your trip to Goa")
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
        self.assertEqual(family["selected_memory_ids"], [1, 2])
        self.assertEqual([item["memory_id"] for item in family["memories"]], [1, 2])

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

    def test_realtime_pet_turn_reuses_canonical_chat_retrieval_and_followup(self):
        calls = []

        class PetChat:
            def prepare_live_call_input(self, _db, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    messages=(), memory_ids=(71,), identity_direct=False,
                    identity_count=0, identity_evidence=(), conflict_count=0,
                    has_uncertainty=False, resolved_entities=("Bruno",),
                    memory_evidence=({
                        "memory_id": 71, "title": "Our Labrador Bruno",
                        "summary": "My dog's name was Bruno.",
                        "uncertainty": None, "conflict": False,
                        "subject": "self", "subjects": ["self"],
                    },),
                    query_intent="pets", matched_candidate_count=1,
                    grounding_chars=40, identity_context_chars=0,
                )

        session = SimpleNamespace(
            session_id="pet-parity", user_id=7, legacy_id=9,
            legacy_name="Aaji", relationship="grandmother",
        )
        service = RealtimeToolService(PetChat())
        self.assertEqual(service.route_turn(session, 1, "What was your dog's name?"), {
            "route": "memory", "tool_name": "retrieve_legacy_memory_context",
        })
        first = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {},
            call_id="pet-1", turn_id=1,
        )
        self.assertEqual(first["status"], "supported")
        self.assertEqual(first["memories"], [{
            "memory_id": 71, "title": "Our Labrador Bruno", "summary": "My dog's name was Bruno.",
            "uncertainty": None, "conflict": False,
            "subject": "self", "subjects": ["self"],
            "epistemic_status": "supported",
        }])
        self.assertNotIn("breed", str(first).casefold())

        self.assertEqual(
            service.route_turn(session, 2, "What else do you remember about him?"),
            {"route": "followup", "tool_name": "retrieve_legacy_memory_context"},
        )
        followup = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {},
            call_id="pet-2", turn_id=2,
        )
        self.assertEqual(followup["followup_context"], "active")
        self.assertEqual(calls[1]["history"][0].content, "What was your dog's name?")
        self.assertEqual(len(calls), 2)

    def test_realtime_sequential_subject_switch_replaces_anchor_and_preserves_attribute_followups(self):
        calls = []

        class SequentialChat:
            def prepare_live_call_input(self, _db, **kwargs):
                calls.append(kwargs)
                context = " ".join(
                    [item.content for item in kwargs["history"]]
                    + [kwargs["user_message"]]
                ).casefold()
                if "dog" in context:
                    ids, entities, summaries = (18, 22), ("Bruno", "Luffy"), (
                        "Bruno is a Labrador.", "Luffy is a Labrador.",
                    )
                elif "tv" in context:
                    ids, entities, summaries = (25, 27, 29), (), (
                        "We have an 85-inch TV at home.",) * 3
                else:
                    ids, entities, summaries = (15, 18), ("Mohan", "Bruno"), (
                        "My husband is Mohan.", "Bruno is a Labrador.",
                    )
                evidence = tuple({
                    "memory_id": memory_id, "summary": summary,
                    "uncertainty": None, "conflict": False,
                    "epistemic_status": "supported",
                } for memory_id, summary in zip(ids, summaries, strict=True))
                return SimpleNamespace(
                    memory_ids=ids, identity_direct=False, identity_evidence=(),
                    identity_count=0, conflict_count=0, has_uncertainty=False,
                    resolved_entities=entities, memory_evidence=evidence,
                    query_intent="family", matched_candidate_count=len(ids),
                    grounding_chars=40, identity_context_chars=0,
                    query_broad=True, fact_confidence="supported", coverage="partial",
                )

        session = SimpleNamespace(
            session_id="sequential-topics", user_id=1, legacy_id=2,
            legacy_name="Aaji", relationship="grandmother",
        )
        service = RealtimeToolService(SequentialChat())
        sequence = (
            ("Tell me about our family", (15, 18)),
            ("And what about the TV?", (25, 27, 29)),
            ("What size is it?", (25, 27, 29)),
            ("That's 85 inch, not 85 inches.", (25, 27, 29)),
            ("Did I ask you that dumbass?", (25, 27, 29)),
            ("I asked you about the TV.", (25, 27, 29)),
            ("And our dogs?", (18, 22)),
            ("Their names?", (18, 22)),
        )
        for turn_id, (query, expected_ids) in enumerate(sequence, 1):
            route = service.route_turn(session, turn_id, query)
            self.assertIn(route["route"], {"memory", "followup"})
            result = service.execute(
                self.db, session, route["tool_name"], {},
                call_id=f"sequence-{turn_id}", turn_id=turn_id,
            )
            self.assertEqual(tuple(result["selected_memory_ids"]), expected_ids)
            self.assertEqual(result["fact_confidence"], "supported")
            self.assertFalse(result["uncertain"])
        self.assertEqual(calls[2]["history"][0].content, "And what about the TV?")
        self.assertEqual(calls[3]["user_message"], "tv")
        self.assertEqual(calls[3]["history"][0].content, "And what about the TV?")
        self.assertEqual(calls[4]["user_message"], "tv")
        self.assertEqual(calls[4]["history"][0].content, "And what about the TV?")
        self.assertEqual(calls[5]["user_message"], "tv")
        self.assertEqual(calls[7]["history"][0].content, "And our dogs?")
        self.assertFalse(service.should_learn_user_turn(session, 4))
        self.assertFalse(service.should_learn_user_turn(session, 5))
        self.assertFalse(service.should_learn_user_turn(session, 6))

    def test_realtime_tool_contract_routes_broad_biography_through_shared_chat_memory(self):
        tool_descriptions = {tool["name"]: tool["description"] for tool in REALTIME_TOOLS}
        identity_description = tool_descriptions["get_legacy_identity_context"].casefold()
        self.assertIn("direct identity fact", identity_description)
        self.assertIn("broad family", identity_description)
        self.assertIn("retrieve_legacy_memory_context", identity_description)
        memory_description = tool_descriptions["retrieve_legacy_memory_context"]
        for topic in ("family", "life", "childhood", "trip"):
            self.assertIn(topic, memory_description)
        instructions = session_instructions(SimpleNamespace(
            legacy_name="Aaji", relationship="grandmother",
            conversation_style="natural", response_length="balanced",
        ))
        self.assertIn("For every personal turn", instructions)
        self.assertIn("call retrieve_legacy_memory_context", instructions)
        self.assertIn("identity facts, memories, or both", instructions)

    def test_realtime_memory_routing_is_deterministic_and_overrides_model_choice(self):
        service = RealtimeToolService(SimpleNamespace())
        empty = RealtimeMemoryState()
        family_routes = [service._route("Tell me about your family", empty) for _ in range(20)]
        trip_routes = [service._route("Tell me about your trips", empty) for _ in range(20)]
        self.assertEqual(family_routes, ["broad_memory"] * 20)
        self.assertEqual(trip_routes, ["broad_memory"] * 20)
        self.assertEqual(service._route("Tell me about the Kashmir trip", empty), "broad_memory")
        self.assertEqual(service._route("What happened in Goa?", empty), "general")
        self.assertEqual(service._route("Who is your husband?", empty), "broad_memory")
        self.assertEqual(service._route("Who is your brother?", empty), "broad_memory")
        self.assertEqual(service._route("How are you?", empty), "social")
        self.assertEqual(service._route("I passed my exam!", empty), "general")
        self.assertEqual(service._route("What is the capital of Germany?", empty), "general")
        for query in (
            "What is capitalism?", "Tell me about hospital care.",
            "What does capitulate mean?",
        ):
            self.assertEqual(service._route(query, empty), "general")
        self.assertEqual(service._route("Who was your pita?", empty), "broad_memory")
        for query in (
            "What was your dog's name?", "Tell me about your dog.",
            "What pets did you have?", "What was your cat's name?",
        ):
            self.assertEqual(service._route(query, empty), "broad_memory")
        self.assertEqual(service._route("What is a Labrador?", empty), "general")
        self.assertEqual(service._route("What food can dogs eat?", empty), "general")
        self.assertEqual(service._route("What was your brother's name?", empty), "broad_memory")
        self.assertEqual(service._route("Tumhare pati ka naam kya tha?", empty), "broad_memory")
        self.assertEqual(service._route("Tell me a story about your childhood", empty), "broad_memory")
        family_state = RealtimeMemoryState(last_query="Tell me about your family")
        trip_state = RealtimeMemoryState(last_query="Tell me about your trips")
        self.assertEqual(service._route("Who else?", family_state), "followup")
        self.assertEqual(service._route("Who went with you?", trip_state), "followup")
        for query in ("Our TV", "The TV", "What about the TV?", "And our car?", "The garden"):
            self.assertEqual(service._route(query, family_state), "broad_memory")
        for closing in ("Perfect, bye.", "Okay thanks.", "Good night."):
            self.assertEqual(service._route(closing, family_state), "social")
        for mixed in (
            "Thanks, tell me about our dogs.", "Okay, and the TV?",
            "Bye, what time was our flight?",
        ):
            self.assertIn(service._route(mixed, family_state), {"broad_memory", "followup"})

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
        self.assertEqual(calls[-1][0], "memory")
        self.assertEqual(husband["status"], "supported")
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

        routed.route_turn(session, 1, "Who is your husband?")
        authoritative = routed.execute(
            self.db, session, "retrieve_legacy_memory_context",
            {"query": "Describe a mango"}, "authoritative-call", turn_id=1,
        )
        self.assertEqual(calls[-1], ("memory", "Who is your husband?"))
        self.assertEqual(authoritative["status"], "supported")

    def test_realtime_route_turn_is_bounded_idempotent_and_followup_aware(self):
        service = RealtimeToolService(SimpleNamespace())
        session = SimpleNamespace(session_id="route-turn-session")
        self.assertEqual(service.route_turn(session, 1, "How are you?"), {
            "route": "direct", "tool_name": None,
        })
        self.assertEqual(service.route_turn(session, 1, "How are you?"), {
            "route": "direct", "tool_name": None,
        })
        with self.assertRaises(ValueError):
            service.route_turn(session, 1, "Describe a mango")
        identity = service.route_turn(session, 2, "Who was your husband?")
        self.assertEqual(identity, {
            "route": "memory", "tool_name": "retrieve_legacy_memory_context",
        })
        state = service._state(session.session_id)
        state.last_query = "Tell me about your family"
        followup = service.route_turn(session, 3, "Who else?")
        self.assertEqual(followup, {
            "route": "followup", "tool_name": "retrieve_legacy_memory_context",
        })

    def test_mixed_general_and_identity_confirmation_have_one_correct_owner(self):
        service = RealtimeToolService(SimpleNamespace())
        session = SimpleNamespace(session_id="mixed-intent")
        social = service.route_turn(session, 1, "Hello, how are you?")
        avocado = service.route_turn(
            session, 2, "What is an avocado? I've never eaten an avocado.",
        )
        family = service.route_turn(session, 3, "Who else is there in your family?")
        identity = service.route_turn(session, 4, "You are Anjali, right?")
        self.assertEqual(social, {"route": "direct", "tool_name": None})
        self.assertEqual(avocado, {"route": "direct", "tool_name": None})
        self.assertEqual(family["tool_name"], "retrieve_legacy_memory_context")
        self.assertEqual(identity["tool_name"], "retrieve_legacy_memory_context")

    def test_assistant_persistence_is_idempotent_and_late_events_are_noops(self):
        service = RealtimeToolService(SimpleNamespace())
        session = SimpleNamespace(session_id="assistant-owner")
        service.route_turn(session, 1, "Who is your brother?")
        service.register_validated_response(
            session, 1, "validated-call-1", answer_plan_status="supported",
            text="My brother is Aditya.",
        )
        self.assertEqual(service.assistant_turn_decision(
            session, 1, "validated-call-1", "My brother is Aditya.",
            response_owner="validated_personal", playback_completed=True,
        ), "create")
        self.assertEqual(service.assistant_turn_decision(
            session, 1, "validated-call-1", "My brother is Aditya.",
            response_owner="validated_personal", playback_completed=True,
        ), "duplicate")
        self.assertEqual(service.assistant_turn_decision(
            session, 1, "validated-call-1", "I have no brother.",
            response_owner="validated_personal", playback_completed=True,
        ), "conflict")
        service.route_turn(session, 2, "You are Anjali, right?")
        self.assertEqual(service.assistant_turn_decision(
            session, 1, "late-native", "Late response.",
        ), "conflict")
        self.assertEqual(service.assistant_turn_decision(
            session, 99, "unknown", "Unknown response.",
        ), "ignore")

    def test_every_personal_subject_routes_to_one_shared_grounding_tool(self):
        service = RealtimeToolService(SimpleNamespace())
        session = SimpleNamespace(session_id="personal-matrix")
        cases = (
            "Tell me about our family", "Who is your spouse?", "Your sibling?",
            "What about our pets?", "What about the television?", "Our vehicle?",
            "Tell me about the house", "What about our garden?", "Our trip?",
            "What are your preferences?", "What do you remember about school?",
            "What about our hometown?", "What about our bicycle?",
        )
        for turn_id, query in enumerate(cases, 1):
            routed = service.route_turn(session, turn_id, query)
            self.assertIn(routed["route"], {"memory", "followup"})
            self.assertEqual(routed["tool_name"], "retrieve_legacy_memory_context")
        general = RealtimeToolService(SimpleNamespace()).route_turn(
            SimpleNamespace(session_id="general-matrix"), 1, "How does an OLED TV work?",
        )
        self.assertEqual(general, {"route": "direct", "tool_name": None})

    def test_unknown_personal_subject_executes_grounding_once_before_unsupported(self):
        class GroundingChat:
            def __init__(self): self.calls = []
            def prepare_live_call_input(self, _db, **kwargs):
                self.calls.append(kwargs["user_message"])
                return SimpleNamespace(
                    grounded_turn=SimpleNamespace(
                        selected_memory_ids=(), selected_identity_fact_ids=(),
                        resolved_entities=(), topic_anchor="bicycle", conflict_count=0,
                        fact_confidence="unsupported", coverage="none", uncertain=False,
                        supported_relevant_evidence_count=0,
                    ),
                    identity_evidence=(), memory_evidence=(), memory_ids=(),
                    identity_direct=False, identity_count=0, conflict_count=0,
                    has_uncertainty=False, query_broad=True, query_intent=None,
                    grounding_chars=0, identity_context_chars=0,
                )

        chat = GroundingChat()
        service = RealtimeToolService(chat)
        session = SimpleNamespace(
            session_id="unknown-bicycle", user_id=1, legacy_id=2,
            legacy_name="Aaji", relationship="grandmother", conversation_id=None,
        )
        routed = service.route_turn(session, 1, "What about our bicycle?")
        result = service.execute(
            self.db, session, routed["tool_name"], {}, call_id="bicycle", turn_id=1,
        )
        self.assertEqual(chat.calls, ["What about our bicycle?"])
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["supported_relevant_evidence_count"], 0)

    def test_five_turn_social_personal_sequence_only_grounds_personal_turns(self):
        service = RealtimeToolService(SimpleNamespace())
        session = SimpleNamespace(session_id="five-turn-intent")
        sequence = (
            ("Hello, how are you?", False),
            ("I'm also doing good. Can you tell me about our family?", True),
            ("Can you tell me about our dogs?", True),
            ("Do you remember anything about a TV?", True),
            ("Perfect, bye.", False),
        )
        grounding_calls = 0
        for turn_id, (query, should_ground) in enumerate(sequence, 1):
            routed = service.route_turn(session, turn_id, query)
            self.assertEqual(routed["tool_name"] is not None, should_ground)
            if should_ground:
                grounding_calls += 1
                self.assertEqual(routed["tool_name"], "retrieve_legacy_memory_context")
        self.assertEqual(grounding_calls, 3)

    def test_realtime_identity_adapter_preserves_conflict_and_unsupported_status(self):
        class IdentityChat:
            def __init__(self): self.calls = 0
            def prepare_live_call_input(self, _db, **kwargs):
                self.calls += 1
                query = kwargs["user_message"]
                records = () if ("Tokyo" in query or "Who am I" in query) else ({
                    "fact_type": "spouse_name", "value": "Meenakshi",
                     "relationship": "wife", "conflicting": True,
                     "uncertainty_note": "two approved versions",
                     "identity_fact_id": 10,
                },)
                relationship = "Who am I" in query
                return SimpleNamespace(
                    grounded_turn=SimpleNamespace(
                        selected_memory_ids=(),
                        selected_identity_fact_ids=((10,) if records else ()),
                        resolved_entities=(("Meenakshi",) if records else ()),
                        topic_anchor=query, conflict_count=int(bool(records)),
                        fact_confidence=("conflicted" if records else "supported" if relationship else "unsupported"),
                        coverage="focused", uncertain=False,
                        supported_relevant_evidence_count=int(relationship),
                    ),
                    identity_evidence=records, memory_evidence=(), memory_ids=(),
                    identity_direct=relationship, identity_count=max(len(records), int(relationship)),
                    conflict_count=int(bool(records)), has_uncertainty=False,
                    resolved_entities=(("Meenakshi",) if records else ()),
                    query_broad=False, query_intent="family", matched_candidate_count=0,
                    grounding_chars=0, identity_context_chars=0,
                )

        session = SimpleNamespace(
            session_id="identity-session", user_id=3, legacy_id=4,
            legacy_name="Aaji", relationship="grandmother",
        )
        service = RealtimeToolService(IdentityChat())
        service.route_turn(session, 1, "Who was your wife?")
        conflict = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {}, turn_id=1,
        )
        service.route_turn(session, 2, "Who was your husband in Tokyo?")
        unsupported = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {}, turn_id=2,
        )
        service.route_turn(session, 3, "Who am I to you?")
        relationship = service.execute(
            self.db, session, "retrieve_legacy_memory_context", {}, turn_id=3,
        )
        self.assertEqual(conflict["status"], "conflicted")
        self.assertEqual(conflict["conflict_count"], 1)
        self.assertEqual(unsupported["status"], "unsupported")
        self.assertEqual(unsupported["identity"], [])
        self.assertEqual(relationship["status"], "supported")
        self.assertEqual(relationship["selected_legacy"], {
            "name": "Aaji", "relationship_to_user": "grandmother", "role": "self",
        })
        self.assertEqual(relationship["identity"], [])
        self.assertEqual(relationship["conflict_count"], 0)
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

        self.assertIn("Supported facts: speak as the represented person", instructions)
        self.assertIn("state the answer naturally in first person", instructions)
        self.assertIn("no source, confidence, certainty, recall, or completeness commentary", instructions)
        self.assertIn("My husband is [name]", instructions)
        self.assertIn("My brother is [name]", instructions)
        self.assertIn("I lived in [place]", instructions)
        self.assertIn("Do not preface a supported answer with 'I remember'", instructions)
        self.assertIn("Partial information: lead with any known fact", instructions)
        self.assertIn("scope uncertainty only to the specific missing detail", instructions)
        self.assertIn("Never claim to remember nothing", instructions)
        self.assertIn("Incomplete subject coverage is not uncertainty", instructions)
        self.assertIn("without volunteering unknown attributes", instructions)
        self.assertIn("if it changed, you can correct me", instructions)
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

        self.assertIn("call retrieve_legacy_memory_context before producing any spoken content", instructions)
        self.assertIn("brief silence is better than procedural speech", instructions)
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
        for commentary in ("source", "confidence", "certainty", "recall", "completeness"):
            self.assertIn(commentary, lowered)
        self.assertIn("no source, confidence, certainty, recall, or completeness commentary", lowered)
        self.assertIn("partial information: lead with any known fact about the subject", lowered)
        self.assertIn("scope uncertainty only to the specific missing detail", lowered)
        self.assertIn("never claim to remember nothing", lowered)
        self.assertIn("incomplete subject coverage is not uncertainty", lowered)
        self.assertIn("i'm not totally sure if that's still current", lowered)
        self.assertIn("if it changed, you can correct me", lowered)
        self.assertIn("supported fact", lowered)
        self.assertIn("person or subject", lowered)
        self.assertIn("i don't remember that", lowered)
        self.assertIn("i'm not completely sure. i remember it in two different ways", lowered)
        self.assertIn("natural human recall", lowered)
        self.assertIn("i remember goa", lowered)
        self.assertIn("call retrieve_legacy_memory_context before producing any spoken content", lowered)
        self.assertIn("general-knowledge questions, answer directly without a tool", lowered)
        self.assertIn("do not invent it", lowered)
        self.assertIn("never narrate tool use", lowered)
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
        self.assertLess(len(balanced), 7000)

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
