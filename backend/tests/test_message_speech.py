"""Focused tests for read-only speech from persisted assistant messages."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.user import get_message_speech_service_for_request
from app.db import Base, get_db
from app.dependencies.auth import get_current_user
from app.main import app
from app.models.memory import Legacy
from app.models.user import Conversation, Message, MessageRole, User
from app.services.ai.exceptions import (
    AIProviderError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
)
from app.services.ai.provider import SPEECH_MEDIA_TYPES, SpeechResult
from app.services.message_speech_service import (
    MessageSpeechError,
    MessageSpeechService,
)
from app.services.voice_profile_resolver import (
    StandardVoiceProfile,
    StandardVoiceResolver,
)


class FakeSpeechService:
    def __init__(self):
        self.calls = []
        self.error = None

    async def synthesize(
        self,
        *,
        text,
        voice=None,
        standard_voice_profile=None,
        response_format=None,
        preserve_text=False,
    ):
        self.calls.append(
            {
                "text": text,
                "voice": voice,
                "standard_voice_profile": standard_voice_profile,
                "response_format": response_format,
                "preserve_text": preserve_text,
            }
        )
        if self.error is not None:
            raise self.error
        resolved_format = response_format or "mp3"
        return SpeechResult(
            content=b"persisted assistant speech",
            media_type=SPEECH_MEDIA_TYPES[resolved_format],
            file_extension=resolved_format,
        )


class MessageSpeechTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session = sessionmaker(bind=self.engine)()

        self.owner = User(
            full_name="Owner",
            email="owner-speech@example.com",
            password_hash="unused",
        )
        self.other_user = User(
            full_name="Other",
            email="other-speech@example.com",
            password_hash="unused",
        )
        self.session.add_all([self.owner, self.other_user])
        self.session.commit()
        self.session.refresh(self.owner)
        self.session.refresh(self.other_user)

        self.legacy = Legacy(
            owner_user_id=self.owner.user_id,
            display_name="Mother Legacy",
            relationship="Mother",
        )
        self.other_legacy = Legacy(
            owner_user_id=self.owner.user_id,
            display_name="Father Legacy",
            relationship="Father",
        )
        self.foreign_legacy = Legacy(
            owner_user_id=self.other_user.user_id,
            display_name="Private Legacy",
            relationship="Father",
        )
        self.session.add_all(
            [self.legacy, self.other_legacy, self.foreign_legacy]
        )
        self.session.commit()

        self.conversation = Conversation(
            user_id=self.owner.user_id,
            title="Original title",
            legacy_id=self.legacy.legacy_id,
        )
        self.other_conversation = Conversation(
            user_id=self.owner.user_id,
            title="Other conversation",
            legacy_id=self.other_legacy.legacy_id,
        )
        self.foreign_conversation = Conversation(
            user_id=self.other_user.user_id,
            title="Private conversation",
            legacy_id=self.foreign_legacy.legacy_id,
        )
        self.session.add_all(
            [self.conversation, self.other_conversation, self.foreign_conversation]
        )
        self.session.commit()
        for conversation in (
            self.conversation,
            self.other_conversation,
            self.foreign_conversation,
        ):
            self.session.refresh(conversation)

        self.user_message = self.add_message(
            self.conversation,
            MessageRole.USER,
            "Hello Berry",
        )
        self.assistant_message = self.add_message(
            self.conversation,
            MessageRole.ASSISTANT,
            "Hello. I remember that day.",
        )
        self.other_assistant = self.add_message(
            self.other_conversation,
            MessageRole.ASSISTANT,
            "A different conversation response.",
        )
        self.foreign_assistant = self.add_message(
            self.foreign_conversation,
            MessageRole.ASSISTANT,
            "Private response.",
        )

        self.speech = FakeSpeechService()
        self.service = MessageSpeechService(
            self.speech,
            StandardVoiceResolver("standard_female"),
            max_text_characters=4096,
        )
        app.dependency_overrides[get_db] = lambda: self.session
        app.dependency_overrides[get_current_user] = lambda: self.owner
        app.dependency_overrides[
            get_message_speech_service_for_request
        ] = lambda: self.service
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.session.close()
        self.engine.dispose()

    def add_message(self, conversation, role, content):
        message = Message(
            conversation_id=conversation.conversation_id,
            role=role,
            content=content,
        )
        self.session.add(message)
        self.session.commit()
        self.session.refresh(message)
        return message

    def endpoint(self, conversation=None, message=None):
        conversation = conversation or self.conversation
        message = message or self.assistant_message
        return (
            f"/api/v1/conversations/{conversation.conversation_id}"
            f"/messages/{message.message_id}/speech"
        )


class MessageSpeechAuthorizationTests(MessageSpeechTestCase):
    def test_authentication_is_required(self):
        app.dependency_overrides.pop(get_current_user)
        response = self.client.post(self.endpoint(), json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.speech.calls, [])

    def test_owner_can_synthesize_assistant_message(self):
        response = self.client.post(self.endpoint(), json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.speech.calls[0]["text"],
            self.assistant_message.content,
        )

    def test_other_user_cannot_discover_conversation_or_message(self):
        app.dependency_overrides[get_current_user] = lambda: self.other_user
        response = self.client.post(self.endpoint(), json={})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "message_not_found")
        self.assertEqual(self.speech.calls, [])

    def test_foreign_and_missing_resources_are_concealed(self):
        cases = (
            self.endpoint(self.conversation, self.other_assistant),
            (
                f"/api/v1/conversations/{self.conversation.conversation_id}"
                "/messages/999999/speech"
            ),
            (
                f"/api/v1/conversations/999999/messages/"
                f"{self.assistant_message.message_id}/speech"
            ),
        )
        for endpoint in cases:
            with self.subTest(endpoint=endpoint):
                response = self.client.post(endpoint, json={})
                self.assertEqual(response.status_code, 404)
                self.assertEqual(
                    response.json()["detail"]["code"],
                    "message_not_found",
                )
        self.assertEqual(self.speech.calls, [])


class MessageSpeechEligibilityTests(MessageSpeechTestCase):
    def test_user_and_system_messages_are_rejected(self):
        system_message = self.add_message(
            self.conversation,
            MessageRole.SYSTEM,
            "System context",
        )
        for message in (self.user_message, system_message):
            with self.subTest(role=message.role):
                response = self.client.post(
                    self.endpoint(message=message),
                    json={},
                )
                self.assertEqual(response.status_code, 409)
                self.assertEqual(
                    response.json()["detail"]["code"],
                    "assistant_message_required",
                )
        self.assertEqual(self.speech.calls, [])

    def test_stored_text_is_forwarded_exactly(self):
        values = (
            "  Berry keeps surrounding spaces.  ",
            "नमस्ते — मला तो दिवस आठवतो!",
            "Hello\nनमस्कार\nHow are you?",
            "Unicode: café, 東京, 🌸; punctuation?!",
        )
        for value in values:
            message = self.add_message(
                self.conversation,
                MessageRole.ASSISTANT,
                value,
            )
            with self.subTest(value=value):
                response = self.client.post(
                    self.endpoint(message=message),
                    json={},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.speech.calls[-1]["text"], value)
                self.assertTrue(self.speech.calls[-1]["preserve_text"])

    def test_blank_and_whitespace_stored_content_are_rejected(self):
        fake_db = SimpleNamespace(rollback=lambda: None)
        for content in ("", " \n\t "):
            with self.subTest(content=content):
                with patch(
                    "app.services.message_speech_service."
                    "ConversationCRUD.get_user_conversation",
                    return_value=SimpleNamespace(conversation_id=1),
                ), patch(
                    "app.services.message_speech_service."
                    "MessageCRUD.get_conversation_message",
                    return_value=SimpleNamespace(
                        role=MessageRole.ASSISTANT,
                        content=content,
                    ),
                ):
                    with self.assertRaises(MessageSpeechError) as raised:
                        self.run_async(
                            self.service.synthesize_assistant_message(
                                db=fake_db,
                                current_user=self.owner,
                                conversation_id=1,
                                message_id=1,
                                response_format=None,
                            )
                        )
                    self.assertEqual(raised.exception.code, "speech_text_invalid")
        self.assertEqual(self.speech.calls, [])

    def test_oversized_stored_content_is_rejected_without_truncation(self):
        service = MessageSpeechService(
            self.speech,
            StandardVoiceResolver("standard_female"),
            max_text_characters=5,
        )
        oversized = self.add_message(
            self.conversation,
            MessageRole.ASSISTANT,
            "123456",
        )
        app.dependency_overrides[
            get_message_speech_service_for_request
        ] = lambda: service
        response = self.client.post(
            self.endpoint(message=oversized),
            json={},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "speech_text_too_long",
        )
        self.assertEqual(self.speech.calls, [])

    @staticmethod
    def run_async(awaitable):
        import asyncio

        return asyncio.run(awaitable)


class MessageSpeechOptionsAndResponseTests(MessageSpeechTestCase):
    def test_format_is_forwarded_and_client_voice_and_text_are_rejected(self):
        response = self.client.post(
            self.endpoint(),
            json={"response_format": "WAV"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.speech.calls[0]["response_format"], "wav")

        for payload in (
            {"voice": None},
            {"voice": "nova"},
            {"response_format": "ogg"},
            {"text": "replacement text"},
        ):
            with self.subTest(payload=payload):
                response = self.client.post(self.endpoint(), json=payload)
                self.assertEqual(response.status_code, 422)

    def test_conversation_legacy_selects_the_internal_voice_profile(self):
        response = self.client.post(self.endpoint(), json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.speech.calls[-1]["standard_voice_profile"],
            StandardVoiceProfile.FEMALE,
        )

        response = self.client.post(
            self.endpoint(self.other_conversation, self.other_assistant),
            json={},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.speech.calls[-1]["standard_voice_profile"],
            StandardVoiceProfile.MALE,
        )

    def test_supported_historical_relationships_and_fallback_are_resolved(self):
        cases = (
            ("Father", StandardVoiceProfile.MALE),
            ("Brother", StandardVoiceProfile.MALE),
            ("Grandfather", StandardVoiceProfile.MALE),
            ("Mother", StandardVoiceProfile.FEMALE),
            ("Sister", StandardVoiceProfile.FEMALE),
            ("Grandmother", StandardVoiceProfile.FEMALE),
            ("Partner", StandardVoiceProfile.FEMALE),
            ("Unknown relationship", StandardVoiceProfile.FEMALE),
        )
        for relationship, expected in cases:
            with self.subTest(relationship=relationship):
                self.legacy.relationship = relationship
                self.session.commit()
                response = self.client.post(self.endpoint(), json={})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    self.speech.calls[-1]["standard_voice_profile"],
                    expected,
                )

    def test_every_format_returns_safe_raw_binary(self):
        for response_format, media_type in SPEECH_MEDIA_TYPES.items():
            with self.subTest(response_format=response_format):
                response = self.client.post(
                    self.endpoint(),
                    json={"response_format": response_format},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"persisted assistant speech")
                self.assertEqual(response.headers["content-type"], media_type)
                self.assertEqual(
                    response.headers["content-disposition"],
                    f'inline; filename="berry-response.{response_format}"',
                )
                self.assertNotIn("base64", response.text.lower())


class MessageSpeechFailureIsolationTests(MessageSpeechTestCase):
    def test_provider_failures_are_safe_and_do_not_change_chat_data(self):
        failures = (
            (AITimeoutError("provider secret"), 504, "speech_timeout"),
            (AIRateLimitError("provider secret"), 429, "speech_rate_limited"),
            (
                AIProviderUnavailableError("provider secret"),
                503,
                "speech_provider_unavailable",
            ),
            (AIProviderError("provider secret"), 502, "speech_generation_failed"),
        )
        original_title = self.conversation.title
        original_updated_at = self.conversation.updated_at
        original_messages = {
            item.message_id: (item.role, item.content)
            for item in self.session.query(Message).all()
        }

        for error, expected_status, expected_code in failures:
            self.speech.error = error
            with self.subTest(error=type(error).__name__):
                response = self.client.post(self.endpoint(), json={})
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(
                    response.json()["detail"]["code"],
                    expected_code,
                )
                self.assertNotIn("provider secret", response.text)

                conversation = self.session.get(
                    Conversation,
                    self.conversation.conversation_id,
                )
                messages = {
                    item.message_id: (item.role, item.content)
                    for item in self.session.query(Message).all()
                }
                self.assertEqual(conversation.title, original_title)
                self.assertEqual(conversation.updated_at, original_updated_at)
                self.assertEqual(messages, original_messages)

    def test_implementation_contains_no_write_or_companion_generation_path(self):
        with open(
            "app/services/message_speech_service.py",
            encoding="utf-8",
        ) as source_file:
            source = source_file.read()
        for forbidden in (
            "db.add",
            "db.commit",
            "db.delete",
            "create_message",
            "get_chat_service",
            "generate_response",
            "audio_path",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
