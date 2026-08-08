"""Phase 6.8.4 complete, owner-scoped Legacy JSON export tests."""

import json
import re
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.v1.story_memory import export_legacy
from app.db import Base
from app.dependencies.auth import get_current_user
from app.models.memory import (
    CompanionMemoryProvenance,
    Legacy,
    Memory,
    MemoryContradictionGroup,
    MemoryExtractionRun,
    MemoryExtractionRunStatus,
    MemoryLink,
    MemoryParticipant,
    MemoryProvenance,
    MemoryReviewStatus,
    MemoryRevision,
    MemoryTag,
    MemoryType,
    StoryMessage,
    StoryMessageRole,
    StorySession,
    Tag,
)
from app.models.user import Conversation, Message, MessageRole, User
from app.services.legacy_export import (
    LegacyExportNotFoundError,
    LegacyExportService,
)
from app.services.legacy_lifecycle import LegacyLifecycleService


FIXED_NOW = datetime(2026, 7, 31, 18, 0, tzinfo=timezone.utc)


class LegacyExportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.owner = User(
            full_name="Owner",
            email="export-owner@example.test",
            password_hash="never-export-this",
        )
        self.other = User(
            full_name="Other",
            email="export-other@example.test",
            password_hash="other-secret",
        )
        self.db.add_all([self.owner, self.other])
        self.db.flush()
        self.legacy = Legacy(
            owner_user_id=self.owner.user_id,
            display_name="Mamá/../🌼",
            relationship="Mother",
            client_correlation_id="private-browser-id",
        )
        self.other_legacy = Legacy(
            owner_user_id=self.other.user_id,
            display_name="Other",
            relationship="Relative",
        )
        self.db.add_all([self.legacy, self.other_legacy])
        self.db.flush()
        self._populate_export_graph()
        self._populate_unrelated_data()
        self.db.commit()
        self.owner_id = self.owner.user_id
        self.other_id = self.other.user_id
        self.legacy_id = self.legacy.legacy_id
        self.service = LegacyExportService(clock=lambda: FIXED_NOW)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _populate_export_graph(self):
        self.story = StorySession(
            legacy_id=self.legacy.legacy_id,
            chapter_key="childhood",
            title="Childhood 🌟",
            created_by_user_id=self.owner.user_id,
        )
        self.conversation = Conversation(
            user_id=self.owner.user_id,
            legacy_id=self.legacy.legacy_id,
            title="Mamá chat",
        )
        self.general_conversation = Conversation(
            user_id=self.owner.user_id,
            legacy_id=None,
            title="General private chat",
        )
        self.db.add_all(
            [self.story, self.conversation, self.general_conversation]
        )
        self.db.flush()
        self.db.add_all(
            [
                StoryMessage(
                    story_session_id=self.story.story_session_id,
                    role=StoryMessageRole.ASSISTANT,
                    content="Second",
                    sequence=2,
                ),
                StoryMessage(
                    story_session_id=self.story.story_session_id,
                    role=StoryMessageRole.USER,
                    content="First 🌼",
                    sequence=1,
                ),
            ]
        )
        group = MemoryContradictionGroup(
            legacy_id=self.legacy.legacy_id,
            topic="Birthplace",
            resolution_status="unresolved",
        )
        self.db.add(group)
        self.db.flush()
        statuses = list(MemoryReviewStatus)
        self.memories = []
        for index, review_status in enumerate(statuses):
            initial_status = (
                MemoryReviewStatus.CANDIDATE
                if review_status == MemoryReviewStatus.SUPERSEDED
                else review_status
            )
            memory = Memory(
                legacy_id=self.legacy.legacy_id,
                memory_type=(MemoryType.NARRATIVE if index == 1 else MemoryType.ATOMIC),
                category="story",
                title=f"Memory {index}",
                summary=f"Summary {index}",
                details={"approximate_date": None, "emoji": "🌼"},
                importance=index + 1,
                extraction_confidence=Decimal("0.875"),
                review_status=initial_status,
                contradiction_group_id=(group.contradiction_group_id if index == 0 else None),
            )
            self.db.add(memory)
            self.memories.append(memory)
        self.db.flush()
        superseded = next(
            item
            for item, desired_status in zip(self.memories, statuses)
            if desired_status == MemoryReviewStatus.SUPERSEDED
        )
        superseded.review_status = MemoryReviewStatus.SUPERSEDED
        superseded.superseded_by_memory_id = self.memories[0].memory_id
        tag = Tag(
            legacy_id=self.legacy.legacy_id,
            name="Family",
            normalized_name="family",
        )
        self.db.add(tag)
        self.db.flush()
        first = self.memories[0]
        self.db.add_all(
            [
                MemoryParticipant(
                    memory_id=first.memory_id,
                    name="Anita",
                    relationship="Mother",
                    role="subject",
                ),
                MemoryTag(memory_id=first.memory_id, tag_id=tag.tag_id),
                MemoryRevision(
                    memory_id=first.memory_id,
                    revision_number=1,
                    previous_content={"summary": "Earlier"},
                    edit_reason="Clarified",
                ),
                MemoryProvenance(
                    memory_id=first.memory_id,
                    source_type="story_session",
                    story_session_id=self.story.story_session_id,
                    source_locator={
                        "message_boundary": 2,
                        "file_path": "C:/private/source.txt",
                        "provider_prompt": "hidden",
                    },
                    excerpt="First 🌼",
                ),
                MemoryLink(
                    legacy_id=self.legacy.legacy_id,
                    source_memory_id=first.memory_id,
                    target_memory_id=self.memories[1].memory_id,
                    link_type="supports",
                ),
                MemoryExtractionRun(
                    legacy_id=self.legacy.legacy_id,
                    story_session_id=self.story.story_session_id,
                    message_boundary=2,
                    trigger_type="completion",
                    status=MemoryExtractionRunStatus.COMPLETED,
                    attempt_count=7,
                    candidate_count=4,
                    memories_created=4,
                    last_error_code=None,
                ),
            ]
        )
        assistant = Message(
            conversation_id=self.conversation.conversation_id,
            role=MessageRole.ASSISTANT,
            content="Grounded answer",
        )
        user = Message(
            conversation_id=self.conversation.conversation_id,
            role=MessageRole.USER,
            content="Question",
        )
        unrelated = Message(
            conversation_id=self.general_conversation.conversation_id,
            role=MessageRole.USER,
            content="Never export this conversation",
        )
        self.db.add_all([assistant, user, unrelated])
        self.db.flush()
        self.db.add(
            CompanionMemoryProvenance(
                assistant_message_id=assistant.message_id,
                memory_id=first.memory_id,
                retrieval_order=0,
                retrieved_at=FIXED_NOW,
            )
        )

    def _populate_unrelated_data(self):
        self.db.add(
            Memory(
                legacy_id=self.other_legacy.legacy_id,
                memory_type=MemoryType.ATOMIC,
                category="story",
                title="Other owner's secret",
                summary="Never export this memory",
                review_status=MemoryReviewStatus.APPROVED,
            )
        )

    def _build(self):
        return self.service.build(
            self.db, user_id=self.owner_id, legacy_id=self.legacy_id
        )

    def test_owner_exports_versioned_unicode_active_legacy(self):
        snapshot = self._build()
        payload = json.loads(self.service.serialize(snapshot).decode("utf-8"))
        self.assertEqual(payload["export_format"], "waffleberry_legacy")
        self.assertEqual(payload["export_version"], 1)
        self.assertEqual(payload["legacy"]["display_name"], "Mamá/../🌼")
        self.assertEqual(payload["legacy"]["status"], "active")
        self.assertNotIn("owner_user_id", payload["legacy"])
        self.assertNotIn("client_correlation_id", payload["legacy"])

    def test_archived_export_is_read_only(self):
        LegacyLifecycleService().archive(
            self.db, user_id=self.owner_id, legacy_id=self.legacy_id
        )
        before = self.legacy.updated_at
        payload = self._build()
        self.db.refresh(self.legacy)
        self.assertEqual(payload.legacy.status, "archived")
        self.assertEqual(self.legacy.updated_at, before)

    def test_missing_cross_owner_and_unauthenticated_are_rejected(self):
        for user_id, legacy_id in (
            (self.other_id, self.legacy_id),
            (self.owner_id, 999999),
        ):
            with self.assertRaises(LegacyExportNotFoundError):
                self.service.build(self.db, user_id=user_id, legacy_id=legacy_id)
        with self.assertRaises(HTTPException) as context:
            export_legacy(
                self.legacy_id,
                current_user=self.other,
                db=self.db,
            )
        self.assertEqual(context.exception.status_code, 404)
        with self.assertRaises(HTTPException) as context:
            get_current_user(credentials=None, db=self.db)
        self.assertEqual(context.exception.status_code, 401)

    def test_download_response_has_safe_filename_and_utf8_content_type(self):
        response = export_legacy(
            self.legacy_id,
            current_user=self.owner,
            db=self.db,
        )
        self.assertTrue(response.media_type.startswith("application/json"))
        disposition = response.headers["content-disposition"]
        self.assertIsNotNone(
            re.fullmatch(
                r'attachment; filename="waffleberry-legacy-mama-\d{4}-\d{2}-\d{2}\.json"',
                disposition,
            )
        )
        self.assertNotIn("/", disposition)
        self.assertEqual(json.loads(response.body)["legacy"]["display_name"], "Mamá/../🌼")

    def test_stories_messages_and_conversations_are_scoped_and_ordered(self):
        payload = self._build().model_dump(mode="json")
        self.assertEqual(len(payload["stories"]), 1)
        self.assertEqual(
            [item["sequence"] for item in payload["stories"][0]["messages"]],
            [1, 2],
        )
        self.assertEqual(len(payload["conversations"]), 1)
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("Never export this conversation", rendered)
        self.assertNotIn("Other owner's secret", rendered)

    def test_complete_memory_contract_and_safe_provenance(self):
        payload = self._build().model_dump(mode="json")
        self.assertEqual(
            {item["review_status"] for item in payload["memories"]},
            {item.value for item in MemoryReviewStatus},
        )
        self.assertEqual(
            {item["memory_type"] for item in payload["memories"]},
            {"atomic", "narrative"},
        )
        first = payload["memories"][0]
        self.assertEqual(first["participants"][0]["name"], "Anita")
        self.assertEqual(first["tags"], ["Family"])
        self.assertEqual(first["revisions"][0]["revision_number"], 1)
        self.assertEqual(first["contradiction"]["topic"], "Birthplace")
        self.assertEqual(first["relationships"][0]["link_type"], "supports")
        locator = first["provenance"][0]["source_locator"]
        self.assertEqual(locator, {"message_boundary": 2})
        self.assertEqual(first["extraction_confidence"], "0.875")

    def test_safe_extraction_and_companion_provenance(self):
        payload = self._build().model_dump(mode="json")
        run = payload["extraction_history"][0]
        self.assertEqual(run["status"], "completed")
        self.assertNotIn("attempt_count", run)
        grounding = payload["conversations"][0]["companion_grounding"][0]
        self.assertEqual(grounding["grounded_memory_ids"], [self.memories[0].memory_id])
        self.assertNotIn("score", grounding)

    def test_export_is_deterministic_and_does_not_mutate_database(self):
        before = {
            "legacies": self.db.query(Legacy).count(),
            "memories": self.db.query(Memory).count(),
            "messages": self.db.query(Message).count(),
        }
        first = self.service.serialize(self._build())
        second = self.service.serialize(self._build())
        self.assertEqual(first, second)
        self.assertEqual(
            before,
            {
                "legacies": self.db.query(Legacy).count(),
                "memories": self.db.query(Memory).count(),
                "messages": self.db.query(Message).count(),
            },
        )
        rendered = first.decode("utf-8").casefold()
        for forbidden in (
            "password_hash",
            "never-export-this",
            "client_correlation_id",
            "normalized_fingerprint",
            "provider_prompt",
            "file_path",
            "attempt_count",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_empty_and_large_legacy_exports(self):
        empty = Legacy(
            owner_user_id=self.owner_id,
            display_name="Empty",
            relationship="Friend",
        )
        self.db.add(empty)
        self.db.flush()
        empty_export = self.service.build(
            self.db, user_id=self.owner_id, legacy_id=empty.legacy_id
        )
        self.assertEqual(empty_export.stories, [])
        self.assertEqual(empty_export.memories, [])
        self.assertEqual(empty_export.conversations, [])
        for index in range(100):
            self.db.add(
                Memory(
                    legacy_id=empty.legacy_id,
                    memory_type=MemoryType.ATOMIC,
                    category="story",
                    title=f"Large {index}",
                    summary=f"Summary {index}",
                    review_status=MemoryReviewStatus.CANDIDATE,
                )
            )
        self.db.flush()
        large = self.service.build(
            self.db, user_id=self.owner_id, legacy_id=empty.legacy_id
        )
        self.assertEqual(len(large.memories), 100)


if __name__ == "__main__":
    unittest.main()
