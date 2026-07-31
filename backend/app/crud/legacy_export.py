"""Focused, read-only queries for one owner-scoped Legacy export."""

from dataclasses import dataclass

from sqlalchemy.orm import Session, selectinload

from app.models.memory import (
    CompanionMemoryProvenance,
    Legacy,
    Memory,
    MemoryExtractionRun,
    MemoryLink,
    MemoryTag,
    StorySession,
)
from app.models.user import Conversation, Message


@dataclass(frozen=True)
class LegacyExportRows:
    legacy: Legacy
    stories: list[StorySession]
    memories: list[Memory]
    extraction_runs: list[MemoryExtractionRun]
    conversations: list[Conversation]
    memory_links: list[MemoryLink]
    companion_provenance: list[CompanionMemoryProvenance]


class LegacyExportCRUD:
    """Load only data belonging to one Legacy in deterministic order."""

    @staticmethod
    def load(
        db: Session,
        *,
        legacy_id: int,
        user_id: int,
    ) -> LegacyExportRows | None:
        legacy = (
            db.query(Legacy)
            .filter(
                Legacy.legacy_id == legacy_id,
                Legacy.owner_user_id == user_id,
            )
            .first()
        )
        if legacy is None:
            return None

        stories = (
            db.query(StorySession)
            .options(selectinload(StorySession.messages))
            .filter(StorySession.legacy_id == legacy_id)
            .order_by(
                StorySession.created_at.asc(),
                StorySession.story_session_id.asc(),
            )
            .all()
        )
        memories = (
            db.query(Memory)
            .options(
                selectinload(Memory.provenance),
                selectinload(Memory.participants),
                selectinload(Memory.revisions),
                selectinload(Memory.tag_links).selectinload(MemoryTag.tag),
                selectinload(Memory.contradiction_group),
            )
            .filter(Memory.legacy_id == legacy_id)
            .order_by(Memory.created_at.asc(), Memory.memory_id.asc())
            .all()
        )
        extraction_runs = (
            db.query(MemoryExtractionRun)
            .filter(MemoryExtractionRun.legacy_id == legacy_id)
            .order_by(
                MemoryExtractionRun.created_at.asc(),
                MemoryExtractionRun.extraction_run_id.asc(),
            )
            .all()
        )
        conversations = (
            db.query(Conversation)
            .options(selectinload(Conversation.messages))
            .filter(
                Conversation.legacy_id == legacy_id,
                Conversation.user_id == user_id,
            )
            .order_by(
                Conversation.created_at.asc(),
                Conversation.conversation_id.asc(),
            )
            .all()
        )
        memory_links = (
            db.query(MemoryLink)
            .filter(MemoryLink.legacy_id == legacy_id)
            .order_by(
                MemoryLink.source_memory_id.asc(),
                MemoryLink.target_memory_id.asc(),
                MemoryLink.link_type.asc(),
                MemoryLink.memory_link_id.asc(),
            )
            .all()
        )
        companion_provenance = (
            db.query(CompanionMemoryProvenance)
            .join(
                Message,
                Message.message_id
                == CompanionMemoryProvenance.assistant_message_id,
            )
            .join(
                Conversation,
                Conversation.conversation_id == Message.conversation_id,
            )
            .join(Memory, Memory.memory_id == CompanionMemoryProvenance.memory_id)
            .filter(
                Conversation.legacy_id == legacy_id,
                Conversation.user_id == user_id,
                Memory.legacy_id == legacy_id,
            )
            .order_by(
                CompanionMemoryProvenance.assistant_message_id.asc(),
                CompanionMemoryProvenance.retrieval_order.asc(),
                CompanionMemoryProvenance.memory_id.asc(),
            )
            .all()
        )
        return LegacyExportRows(
            legacy=legacy,
            stories=stories,
            memories=memories,
            extraction_runs=extraction_runs,
            conversations=conversations,
            memory_links=memory_links,
            companion_provenance=companion_provenance,
        )
