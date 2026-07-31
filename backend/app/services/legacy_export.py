"""Build a deterministic, privacy-conscious JSON Legacy snapshot."""

import json
import math
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.crud.legacy_export import LegacyExportCRUD
from app.schemas.legacy_export import (
    CompanionGroundingExport,
    ConversationExport,
    ConversationMessageExport,
    ExtractionRunExport,
    LegacyExport,
    LegacyProfileExport,
    MemoryContradictionExport,
    MemoryExport,
    MemoryParticipantExport,
    MemoryProvenanceExport,
    MemoryRelationshipExport,
    MemoryRevisionExport,
    StoryMessageExport,
    StorySessionExport,
)


class LegacyExportNotFoundError(Exception):
    """Raised for missing and foreign-owned Legacies alike."""


class LegacyExportService:
    """Assemble and serialize one read-only owner export."""

    _UNSAFE_KEYS = frozenset(
        {
            "path",
            "file_path",
            "filename",
            "file_name",
            "token",
            "secret",
            "password",
            "credential",
            "credentials",
            "api_key",
            "raw_response",
            "provider_response",
        }
    )
    _UNSAFE_KEY_MARKERS = (
        "prompt",
    )

    def __init__(self, clock: Callable[[], datetime] | None = None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def build(self, db: Session, *, user_id: int, legacy_id: int) -> LegacyExport:
        rows = LegacyExportCRUD.load(
            db,
            legacy_id=legacy_id,
            user_id=user_id,
        )
        if rows is None:
            raise LegacyExportNotFoundError("Legacy was not found.")

        links_by_memory = defaultdict(list)
        for link in rows.memory_links:
            links_by_memory[link.source_memory_id].append(
                MemoryRelationshipExport(
                    target_memory_id=link.target_memory_id,
                    link_type=link.link_type,
                )
            )
        grounding_by_message = defaultdict(list)
        for record in rows.companion_provenance:
            grounding_by_message[record.assistant_message_id].append(record.memory_id)
        story_ids = {item.story_session_id for item in rows.stories}
        story_message_ids = {
            message.story_message_id
            for story in rows.stories
            for message in story.messages
        }
        conversation_ids = {item.conversation_id for item in rows.conversations}
        conversation_message_ids = {
            message.message_id
            for conversation in rows.conversations
            for message in conversation.messages
        }
        allowed_sources = (
            story_ids,
            story_message_ids,
            conversation_ids,
            conversation_message_ids,
        )

        return LegacyExport(
            exported_at=self._utc(self._clock()),
            legacy=LegacyProfileExport(
                legacy_id=rows.legacy.legacy_id,
                display_name=rows.legacy.display_name,
                relationship=rows.legacy.relationship,
                status=self._enum(rows.legacy.status),
                created_at=self._utc(rows.legacy.created_at),
                updated_at=self._utc(rows.legacy.updated_at),
            ),
            stories=[self._story(item) for item in rows.stories],
            memories=[
                self._memory(
                    item,
                    links_by_memory[item.memory_id],
                    allowed_sources,
                )
                for item in rows.memories
            ],
            extraction_history=[
                ExtractionRunExport(
                    extraction_run_id=item.extraction_run_id,
                    story_session_id=item.story_session_id,
                    message_boundary=item.message_boundary,
                    trigger_type=item.trigger_type,
                    status=self._enum(item.status),
                    candidate_count=item.candidate_count,
                    memories_created=item.memories_created,
                    failure_category=item.last_error_code,
                    created_at=self._utc(item.created_at),
                    started_at=self._optional_utc(item.started_at),
                    completed_at=self._optional_utc(item.completed_at),
                )
                for item in rows.extraction_runs
            ],
            conversations=[
                self._conversation(item, grounding_by_message)
                for item in rows.conversations
            ],
        )

    def serialize(self, export: LegacyExport) -> bytes:
        """Return strict, readable UTF-8 JSON without non-standard numbers."""
        payload = self._safe_json(export.model_dump(mode="json"))
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=False,
        ).encode("utf-8")

    def filename(self, export: LegacyExport) -> str:
        normalized = unicodedata.normalize("NFKD", export.legacy.display_name)
        ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
        if not slug:
            slug = f"legacy-{export.legacy.legacy_id}"
        slug = slug[:80].rstrip("-")
        return f"waffleberry-legacy-{slug}-{export.exported_at:%Y-%m-%d}.json"

    def _story(self, story) -> StorySessionExport:
        messages = sorted(
            story.messages,
            key=lambda item: (item.sequence, item.story_message_id),
        )
        return StorySessionExport(
            story_session_id=story.story_session_id,
            chapter_key=story.chapter_key,
            title=story.title,
            status=self._enum(story.status),
            created_at=self._utc(story.created_at),
            updated_at=self._utc(story.updated_at),
            completed_at=self._optional_utc(story.completed_at),
            messages=[
                StoryMessageExport(
                    story_message_id=item.story_message_id,
                    role=self._enum(item.role),
                    content=item.content,
                    sequence=item.sequence,
                    created_at=self._utc(item.created_at),
                )
                for item in messages
            ],
        )

    def _memory(self, memory, relationships, allowed_sources) -> MemoryExport:
        (
            story_ids,
            story_message_ids,
            conversation_ids,
            conversation_message_ids,
        ) = allowed_sources
        contradiction = None
        if memory.contradiction_group is not None:
            group = memory.contradiction_group
            contradiction = MemoryContradictionExport(
                contradiction_group_id=group.contradiction_group_id,
                topic=group.topic,
                resolution_status=group.resolution_status,
                resolution_note=group.resolution_note,
                resolved_at=self._optional_utc(group.resolved_at),
            )
        return MemoryExport(
            memory_id=memory.memory_id,
            memory_type=self._enum(memory.memory_type),
            category=memory.category,
            title=memory.title,
            summary=memory.summary,
            details=self._safe_json(memory.details),
            emotional_significance=memory.emotional_significance,
            importance=memory.importance,
            extraction_confidence=self._finite_decimal(memory.extraction_confidence),
            review_status=self._enum(memory.review_status),
            uncertainty_note=memory.uncertainty_note,
            superseded_by_memory_id=memory.superseded_by_memory_id,
            created_at=self._utc(memory.created_at),
            updated_at=self._utc(memory.updated_at),
            reviewed_at=self._optional_utc(memory.reviewed_at),
            participants=[
                MemoryParticipantExport(
                    name=item.name,
                    relationship=item.relationship,
                    role=item.role,
                )
                for item in sorted(
                    memory.participants,
                    key=lambda item: (item.name.casefold(), item.memory_participant_id),
                )
            ],
            tags=sorted(
                (item.tag.name for item in memory.tag_links),
                key=lambda value: (value.casefold(), value),
            ),
            revisions=[
                MemoryRevisionExport(
                    revision_number=item.revision_number,
                    previous_content=self._safe_json(item.previous_content),
                    edit_reason=item.edit_reason,
                    created_at=self._utc(item.created_at),
                )
                for item in sorted(
                    memory.revisions,
                    key=lambda item: (item.revision_number, item.memory_revision_id),
                )
            ],
            provenance=[
                MemoryProvenanceExport(
                    provenance_id=item.provenance_id,
                    source_type=item.source_type,
                    conversation_id=(
                        item.conversation_id
                        if item.conversation_id in conversation_ids
                        else None
                    ),
                    message_id=(
                        item.message_id
                        if item.message_id in conversation_message_ids
                        else None
                    ),
                    story_session_id=(
                        item.story_session_id
                        if item.story_session_id in story_ids
                        else None
                    ),
                    story_message_id=(
                        item.story_message_id
                        if item.story_message_id in story_message_ids
                        else None
                    ),
                    source_locator=self._safe_locator(item.source_locator),
                    excerpt=item.excerpt,
                    speaker=item.speaker,
                    chapter=item.chapter,
                    extracted_at=self._utc(item.extracted_at),
                )
                for item in sorted(memory.provenance, key=lambda item: item.provenance_id)
            ],
            contradiction=contradiction,
            relationships=relationships,
        )

    def _conversation(self, conversation, grounding_by_message) -> ConversationExport:
        messages = sorted(
            conversation.messages,
            key=lambda item: (item.created_at, item.message_id),
        )
        grounding = [
            CompanionGroundingExport(
                assistant_message_id=item.message_id,
                grounded_memory_ids=grounding_by_message[item.message_id],
            )
            for item in messages
            if grounding_by_message[item.message_id]
        ]
        return ConversationExport(
            conversation_id=conversation.conversation_id,
            title=conversation.title,
            created_at=self._utc(conversation.created_at),
            updated_at=self._utc(conversation.updated_at),
            messages=[
                ConversationMessageExport(
                    message_id=item.message_id,
                    role=self._enum(item.role),
                    content=item.content,
                    created_at=self._utc(item.created_at),
                )
                for item in messages
            ],
            companion_grounding=grounding,
        )

    @classmethod
    def _safe_locator(cls, value: Any) -> Any:
        return cls._safe_json(value)

    @classmethod
    def _safe_json(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, Decimal):
            return str(value) if value.is_finite() else None
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return cls._utc(value).isoformat().replace("+00:00", "Z")
        if isinstance(value, dict):
            return {
                str(key): cls._safe_json(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                if not cls._is_unsafe_key(key)
            }
        if isinstance(value, (list, tuple)):
            return [cls._safe_json(item) for item in value]
        return str(value)

    @classmethod
    def _is_unsafe_key(cls, key: Any) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
        return (
            normalized in cls._UNSAFE_KEYS
            or normalized.endswith("_path")
            or normalized.startswith("provider_")
            or any(marker in normalized for marker in cls._UNSAFE_KEY_MARKERS)
        )

    @staticmethod
    def _enum(value: Any) -> str:
        return value.value if isinstance(value, Enum) else str(value)

    @staticmethod
    def _finite_decimal(value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(value)
        except Exception:
            return None
        return result if result.is_finite() else None

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _optional_utc(cls, value: Any) -> datetime | None:
        return cls._utc(value) if isinstance(value, datetime) else None
