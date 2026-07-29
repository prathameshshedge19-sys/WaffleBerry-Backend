"""Tests for streaming message persistence boundaries."""

import os
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret")

from app.crud.user import MessageCRUD
from app.db import Base
from app.models.user import Conversation, Message, MessageRole, User


class StreamingPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session = sessionmaker(bind=self.engine)()

        user = User(
            full_name="Test User",
            email="stream@example.com",
            password_hash="not-used",
        )
        self.session.add(user)
        self.session.commit()
        self.session.refresh(user)

        self.conversation = Conversation(
            user_id=user.user_id,
            title="New Chat",
        )
        self.session.add(self.conversation)
        self.session.commit()
        self.session.refresh(self.conversation)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_user_and_completed_assistant_are_each_stored_once(self):
        MessageCRUD.create_user_message(
            self.session,
            self.conversation,
            "Remember our picnic",
        )

        self.assertEqual(
            self.session.query(Message)
            .filter(Message.role == MessageRole.USER)
            .count(),
            1,
        )
        self.assertNotEqual(self.conversation.title, "New Chat")

        MessageCRUD.create_assistant_message(
            self.session,
            self.conversation,
            "I can help you reflect on that picnic.",
        )

        self.assertEqual(
            self.session.query(Message)
            .filter(Message.role == MessageRole.ASSISTANT)
            .count(),
            1,
        )

    def test_partial_failure_boundary_keeps_user_without_assistant(self):
        MessageCRUD.create_user_message(
            self.session,
            self.conversation,
            "A message whose response is interrupted",
        )

        self.assertEqual(
            self.session.query(Message).count(),
            1,
        )
        self.assertEqual(
            self.session.query(Message).one().role,
            MessageRole.USER,
        )


if __name__ == "__main__":
    unittest.main()
