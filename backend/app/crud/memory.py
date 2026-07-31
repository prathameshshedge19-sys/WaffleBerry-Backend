"""Owner-scoped persistence operations for the Memory Engine foundation."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.memory import (
    CompanionMemoryProvenance,
    Legacy,
    LegacyStatus,
    Memory,
    MemoryContradictionGroup,
    MemoryExtractionRun,
    MemoryLink,
    MemoryParticipant,
    MemoryProvenance,
    MemoryReviewStatus,
    MemoryRevision,
    MemoryTag,
    StoryMessage,
    StorySession,
    StorySessionStatus,
    Tag,
)
from app.models.user import Conversation, Message, User
from app.schemas.memory import (
    LegacyCreate,
    MemoryCandidateCreate,
    MemoryProvenanceCreate,
    StoryMessageCreate,
    StorySessionCreate,
)
from app.services.memory.fingerprint import build_memory_fingerprint


class MemoryPersistenceError(ValueError):
    """Raised when ownership or provenance invariants are violated."""


class LegacyCRUD:
    """Persistence operations scoped to a legacy owner."""

    @staticmethod
    def create_legacy(
        db: Session,
        owner_user_id: int,
        legacy: LegacyCreate,
    ) -> Legacy:
        """Create a legacy for an existing owner."""
        if (
            db.query(User.user_id)
            .filter(User.user_id == owner_user_id)
            .first()
            is None
        ):
            raise MemoryPersistenceError("Legacy owner does not exist.")

        if legacy.client_correlation_id:
            existing = (
                db.query(Legacy)
                .filter(
                    Legacy.owner_user_id == owner_user_id,
                    Legacy.client_correlation_id
                    == legacy.client_correlation_id,
                )
                .first()
            )
            if existing is not None:
                return existing
        db_legacy = Legacy(
            owner_user_id=owner_user_id,
            display_name=legacy.display_name,
            relationship=legacy.relationship,
            client_correlation_id=legacy.client_correlation_id,
        )
        try:
            db.add(db_legacy)
            db.commit()
            db.refresh(db_legacy)
        except IntegrityError:
            db.rollback()
            if legacy.client_correlation_id:
                concurrent = (
                    db.query(Legacy)
                    .filter(
                        Legacy.owner_user_id == owner_user_id,
                        Legacy.client_correlation_id
                        == legacy.client_correlation_id,
                    )
                    .first()
                )
                if concurrent is not None:
                    return concurrent
            raise
        except Exception:
            db.rollback()
            raise
        return db_legacy

    @staticmethod
    def get_user_legacy(
        db: Session,
        legacy_id: int,
        user_id: int,
    ) -> Legacy | None:
        """Return a legacy only when owned by the specified user."""
        return (
            db.query(Legacy)
            .filter(
                Legacy.legacy_id == legacy_id,
                Legacy.owner_user_id == user_id,
            )
            .first()
        )

    @staticmethod
    def get_user_legacies(
        db: Session,
        user_id: int,
        status: LegacyStatus = LegacyStatus.ACTIVE,
    ) -> list[Legacy]:
        """List one user's Legacies in one explicit lifecycle state."""
        return (
            db.query(Legacy)
            .filter(
                Legacy.owner_user_id == user_id,
                Legacy.status == status,
            )
            .order_by(Legacy.created_at.asc(), Legacy.legacy_id.asc())
            .all()
        )

    @staticmethod
    def apply_status_transition(
        db: Session,
        legacy: Legacy,
        *,
        target: LegacyStatus,
        updated_at: datetime,
    ) -> Legacy:
        """Stage one focused lifecycle change without committing it."""
        legacy.status = target
        legacy.updated_at = updated_at
        db.flush()
        return legacy

    @staticmethod
    def get_user_legacy_for_update(
        db: Session,
        legacy_id: int,
        user_id: int,
    ) -> Legacy | None:
        """Lock and return one owner-scoped Legacy where supported."""
        return (
            db.query(Legacy)
            .filter(
                Legacy.legacy_id == legacy_id,
                Legacy.owner_user_id == user_id,
            )
            .with_for_update()
            .first()
        )

    @staticmethod
    def delete_legacy_graph(db: Session, legacy: Legacy) -> None:
        """Stage deletion of one complete Legacy graph without committing."""
        legacy_id = legacy.legacy_id
        memory_ids = db.query(Memory.memory_id).filter(
            Memory.legacy_id == legacy_id
        )
        story_session_ids = db.query(StorySession.story_session_id).filter(
            StorySession.legacy_id == legacy_id
        )
        conversation_ids = db.query(Conversation.conversation_id).filter(
            Conversation.legacy_id == legacy_id
        )
        message_ids = db.query(Message.message_id).filter(
            Message.conversation_id.in_(conversation_ids)
        )

        db.query(CompanionMemoryProvenance).filter(
            or_(
                CompanionMemoryProvenance.memory_id.in_(memory_ids),
                CompanionMemoryProvenance.assistant_message_id.in_(message_ids),
            )
        ).delete(synchronize_session=False)
        db.query(MemoryLink).filter(
            or_(
                MemoryLink.legacy_id == legacy_id,
                MemoryLink.source_memory_id.in_(memory_ids),
                MemoryLink.target_memory_id.in_(memory_ids),
            )
        ).delete(synchronize_session=False)
        for model in (
            MemoryTag,
            MemoryParticipant,
            MemoryRevision,
            MemoryProvenance,
        ):
            db.query(model).filter(
                model.memory_id.in_(memory_ids)
            ).delete(synchronize_session=False)
        db.query(Memory).filter(Memory.legacy_id == legacy_id).delete(
            synchronize_session=False
        )
        db.query(Tag).filter(Tag.legacy_id == legacy_id).delete(
            synchronize_session=False
        )
        db.query(MemoryContradictionGroup).filter(
            MemoryContradictionGroup.legacy_id == legacy_id
        ).delete(synchronize_session=False)
        db.query(MemoryExtractionRun).filter(
            MemoryExtractionRun.legacy_id == legacy_id
        ).delete(synchronize_session=False)
        db.query(StoryMessage).filter(
            StoryMessage.story_session_id.in_(story_session_ids)
        ).delete(synchronize_session=False)
        db.query(StorySession).filter(
            StorySession.legacy_id == legacy_id
        ).delete(synchronize_session=False)
        db.query(Message).filter(
            Message.conversation_id.in_(conversation_ids)
        ).delete(synchronize_session=False)
        db.query(Conversation).filter(
            Conversation.legacy_id == legacy_id
        ).delete(synchronize_session=False)
        db.query(Legacy).filter(
            Legacy.legacy_id == legacy_id,
            Legacy.owner_user_id == legacy.owner_user_id,
        ).delete(synchronize_session=False)
        db.flush()

    @staticmethod
    def apply_identity_changes_if_current(
        db: Session,
        *,
        legacy_id: int,
        user_id: int,
        expected_updated_at: datetime,
        updated_at: datetime,
        changes: dict[str, str],
    ) -> bool:
        """Atomically update identity only if its timestamp is current."""
        values: dict = {**changes, "updated_at": updated_at}
        # SQLite may store a server-default whole-second value without a
        # fractional suffix, then bind the same datetime with microseconds.
        # A one-microsecond window preserves compare-and-swap semantics while
        # accommodating that representation difference.
        timestamp_floor = expected_updated_at - timedelta(microseconds=1)
        timestamp_ceiling = expected_updated_at + timedelta(microseconds=1)
        changed = (
            db.query(Legacy)
            .filter(
                Legacy.legacy_id == legacy_id,
                Legacy.owner_user_id == user_id,
                Legacy.updated_at >= timestamp_floor,
                Legacy.updated_at <= timestamp_ceiling,
            )
            .update(values, synchronize_session=False)
        )
        return changed == 1


class StorySessionCRUD:
    """Owner- and legacy-scoped Guided Story persistence."""

    @staticmethod
    def create_story_session(
        db: Session,
        legacy_id: int,
        user_id: int,
        story_session: StorySessionCreate,
    ) -> StorySession:
        """Create a session only under a legacy owned by the user."""
        legacy = LegacyCRUD.get_user_legacy(db, legacy_id, user_id)
        if legacy is None:
            raise MemoryPersistenceError("Legacy not found for user.")

        db_session = StorySession(
            legacy_id=legacy.legacy_id,
            chapter_key=story_session.chapter_key,
            title=story_session.title,
            created_by_user_id=user_id,
        )
        try:
            db.add(db_session)
            db.commit()
            db.refresh(db_session)
        except Exception:
            db.rollback()
            raise
        return db_session

    @staticmethod
    def get_or_create_active_story_session(
        db: Session,
        legacy_id: int,
        user_id: int,
        story_session: StorySessionCreate,
    ) -> StorySession:
        """Resume the newest unfinished chapter session or create one."""
        if LegacyCRUD.get_user_legacy(db, legacy_id, user_id) is None:
            raise MemoryPersistenceError("Legacy not found for user.")
        existing = (
            db.query(StorySession)
            .filter(
                StorySession.legacy_id == legacy_id,
                StorySession.chapter_key == story_session.chapter_key,
                StorySession.status.in_(
                    [
                        StorySessionStatus.IN_PROGRESS,
                        StorySessionStatus.PAUSED,
                    ]
                ),
            )
            .order_by(
                StorySession.updated_at.desc(),
                StorySession.story_session_id.desc(),
            )
            .first()
        )
        if existing is not None:
            if existing.status == StorySessionStatus.PAUSED:
                existing.status = StorySessionStatus.IN_PROGRESS
                db.commit()
                db.refresh(existing)
            return existing
        return StorySessionCRUD.create_story_session(
            db, legacy_id, user_id, story_session
        )

    @staticmethod
    def get_legacy_story_session(
        db: Session,
        story_session_id: int,
        legacy_id: int,
    ) -> StorySession | None:
        """Return a Story Session only inside the supplied legacy."""
        return (
            db.query(StorySession)
            .filter(
                StorySession.story_session_id == story_session_id,
                StorySession.legacy_id == legacy_id,
            )
            .first()
        )

    @staticmethod
    def append_story_message(
        db: Session,
        story_session_id: int,
        legacy_id: int,
        message: StoryMessageCreate,
        client_message_id: str | None = None,
        _attempt: int = 0,
    ) -> StoryMessage:
        """Append one message with a deterministic per-session sequence."""
        story_session = (
            db.query(StorySession)
            .filter(
                StorySession.story_session_id == story_session_id,
                StorySession.legacy_id == legacy_id,
            )
            .with_for_update()
            .first()
        )
        if story_session is None:
            raise MemoryPersistenceError(
                "Story Session not found for legacy."
            )

        if client_message_id:
            existing = (
                db.query(StoryMessage)
                .filter(
                    StoryMessage.story_session_id == story_session_id,
                    StoryMessage.client_message_id == client_message_id,
                )
                .first()
            )
            if existing is not None:
                return existing
        last_sequence = (
            db.query(func.max(StoryMessage.sequence))
            .filter(
                StoryMessage.story_session_id
                == story_session.story_session_id
            )
            .scalar()
            or 0
        )
        db_message = StoryMessage(
            story_session_id=story_session.story_session_id,
            role=message.role,
            content=message.content,
            sequence=last_sequence + 1,
            client_message_id=client_message_id,
        )
        try:
            db.add(db_message)
            db.commit()
            db.refresh(db_message)
        except IntegrityError:
            db.rollback()
            if client_message_id:
                concurrent = (
                    db.query(StoryMessage)
                    .filter(
                        StoryMessage.story_session_id == story_session_id,
                        StoryMessage.client_message_id == client_message_id,
                    )
                    .first()
                )
                if concurrent is not None:
                    return concurrent
            if _attempt < 2:
                return StorySessionCRUD.append_story_message(
                    db,
                    story_session_id,
                    legacy_id,
                    message,
                    client_message_id,
                    _attempt + 1,
                )
            raise
        except Exception:
            db.rollback()
            raise
        return db_message

    @staticmethod
    def get_story_messages(
        db: Session,
        story_session_id: int,
        legacy_id: int,
    ) -> list[StoryMessage]:
        """List Story Session messages in deterministic order."""
        if (
            StorySessionCRUD.get_legacy_story_session(
                db,
                story_session_id,
                legacy_id,
            )
            is None
        ):
            raise MemoryPersistenceError(
                "Story Session not found for legacy."
            )
        return (
            db.query(StoryMessage)
            .filter(StoryMessage.story_session_id == story_session_id)
            .order_by(
                StoryMessage.sequence.asc(),
                StoryMessage.story_message_id.asc(),
            )
            .all()
        )


class MemoryCRUD:
    """Legacy-scoped memory candidate and review persistence."""

    @staticmethod
    def get_legacy_memory(
        db: Session,
        memory_id: int,
        legacy_id: int,
    ) -> Memory | None:
        """Return a memory only inside the supplied legacy."""
        return (
            db.query(Memory)
            .filter(
                Memory.memory_id == memory_id,
                Memory.legacy_id == legacy_id,
            )
            .first()
        )

    @staticmethod
    def list_approved_for_retrieval(
        db: Session,
        legacy_id: int,
    ) -> list[Memory]:
        """Return approved memories in stable retrieval order."""
        return (
            db.query(Memory)
            .filter(
                Memory.legacy_id == legacy_id,
                Memory.review_status == MemoryReviewStatus.APPROVED,
            )
            .order_by(
                Memory.importance.desc().nullslast(),
                Memory.updated_at.desc(),
                Memory.memory_id.asc(),
            )
            .all()
        )

    @staticmethod
    def create_contradiction_group(
        db: Session,
        legacy_id: int,
        user_id: int,
        topic: str,
    ) -> MemoryContradictionGroup:
        """Create a legacy-scoped container for conflicting accounts."""
        if LegacyCRUD.get_user_legacy(db, legacy_id, user_id) is None:
            raise MemoryPersistenceError("Legacy not found for user.")
        topic = topic.strip()
        if not topic:
            raise MemoryPersistenceError(
                "Contradiction topic must not be blank."
            )
        group = MemoryContradictionGroup(
            legacy_id=legacy_id,
            topic=topic,
        )
        try:
            db.add(group)
            db.commit()
            db.refresh(group)
        except Exception:
            db.rollback()
            raise
        return group

    @classmethod
    def create_memory_candidate(
        cls,
        db: Session,
        legacy_id: int,
        user_id: int,
        candidate: MemoryCandidateCreate,
    ) -> Memory:
        """Persist one candidate and all provenance in one transaction."""
        if LegacyCRUD.get_user_legacy(db, legacy_id, user_id) is None:
            raise MemoryPersistenceError("Legacy not found for user.")

        try:
            memory = cls.add_memory_candidate(db, legacy_id, candidate)
            db.commit()
            db.refresh(memory)
        except Exception:
            db.rollback()
            raise
        return memory

    @classmethod
    def add_memory_candidate(
        cls,
        db: Session,
        legacy_id: int,
        candidate: MemoryCandidateCreate,
        normalized_fingerprint: str | None = None,
    ) -> Memory:
        """Flush one complete candidate without committing the transaction."""
        cls._validate_candidate_links(db, legacy_id, candidate)
        memory = Memory(
            legacy_id=legacy_id,
            memory_type=candidate.memory_type,
            category=candidate.category,
            title=candidate.title,
            summary=candidate.summary,
            normalized_fingerprint=(
                normalized_fingerprint
                or build_memory_fingerprint(legacy_id, candidate)
            ),
            details=(
                candidate.details.model_dump(mode="json")
                if candidate.details is not None
                else None
            ),
            emotional_significance=candidate.emotional_significance,
            importance=candidate.importance,
            extraction_confidence=candidate.extraction_confidence,
            review_status=MemoryReviewStatus.CANDIDATE,
            uncertainty_note=candidate.uncertainty_note,
            contradiction_group_id=candidate.contradiction_group_id,
            superseded_by_memory_id=candidate.superseded_by_memory_id,
        )
        memory.participants = [
            MemoryParticipant(**participant.model_dump())
            for participant in candidate.participants
        ]
        memory.provenance = [
            cls._build_provenance(source)
            for source in candidate.provenance
        ]

        db.add(memory)
        db.flush()
        cls._attach_tags(db, memory, candidate.tags)
        db.flush()
        if not memory.provenance:
            raise MemoryPersistenceError(
                "A persisted memory requires verified provenance."
            )
        return memory

    @staticmethod
    def get_memory_by_fingerprint(
        db: Session,
        legacy_id: int,
        normalized_fingerprint: str,
    ) -> Memory | None:
        return (
            db.query(Memory)
            .filter(
                Memory.legacy_id == legacy_id,
                Memory.normalized_fingerprint == normalized_fingerprint,
            )
            .first()
        )

    @classmethod
    def add_memory_link(
        cls,
        db: Session,
        legacy_id: int,
        source_memory_id: int,
        target_memory_id: int,
        link_type: str,
    ) -> MemoryLink:
        """Flush a same-legacy review relationship without committing."""
        source = cls.get_legacy_memory(db, source_memory_id, legacy_id)
        target = cls.get_legacy_memory(db, target_memory_id, legacy_id)
        if source is None or target is None:
            raise MemoryPersistenceError(
                "Related memories must belong to the same legacy."
            )
        existing = (
            db.query(MemoryLink)
            .filter(
                MemoryLink.source_memory_id == source_memory_id,
                MemoryLink.target_memory_id == target_memory_id,
                MemoryLink.link_type == link_type,
            )
            .first()
        )
        if existing is not None:
            return existing
        link = MemoryLink(
            legacy_id=legacy_id,
            source_memory_id=source_memory_id,
            target_memory_id=target_memory_id,
            link_type=link_type,
        )
        db.add(link)
        db.flush()
        return link

    @classmethod
    def get_or_create_contradiction_group_for_memories(
        cls,
        db: Session,
        legacy_id: int,
        memory_ids: list[int],
        topic: str,
    ) -> MemoryContradictionGroup:
        """Reuse one related group or flush a new same-legacy group."""
        unique_ids = sorted(set(memory_ids))
        memories = (
            db.query(Memory)
            .filter(
                Memory.legacy_id == legacy_id,
                Memory.memory_id.in_(unique_ids),
            )
            .with_for_update()
            .all()
        )
        if len(memories) != len(unique_ids):
            raise MemoryPersistenceError(
                "Contradiction memories must belong to the same legacy."
            )
        group_ids = {
            memory.contradiction_group_id
            for memory in memories
            if memory.contradiction_group_id is not None
        }
        if len(group_ids) > 1:
            raise MemoryPersistenceError(
                "Conflicting contradiction groups require human review."
            )
        if group_ids:
            group = (
                db.query(MemoryContradictionGroup)
                .filter(
                    MemoryContradictionGroup.legacy_id == legacy_id,
                    MemoryContradictionGroup.contradiction_group_id
                    == next(iter(group_ids)),
                )
                .one()
            )
        else:
            group = MemoryContradictionGroup(
                legacy_id=legacy_id,
                topic=topic.strip() or "Conflicting accounts",
            )
            db.add(group)
            db.flush()
        for memory in memories:
            memory.contradiction_group_id = group.contradiction_group_id
        db.flush()
        return group

    @staticmethod
    def list_legacy_memories(
        db: Session,
        legacy_id: int,
        user_id: int,
        review_status: MemoryReviewStatus | None = None,
    ) -> list[Memory]:
        """List memories for exactly one legacy."""
        if LegacyCRUD.get_user_legacy(db, legacy_id, user_id) is None:
            raise MemoryPersistenceError("Legacy not found for user.")
        query = db.query(Memory).filter(Memory.legacy_id == legacy_id)
        if review_status is not None:
            query = query.filter(Memory.review_status == review_status)
        return query.order_by(
            Memory.created_at.desc(),
            Memory.memory_id.desc(),
        ).all()

    @classmethod
    def update_review_status(
        cls,
        db: Session,
        memory_id: int,
        legacy_id: int,
        user_id: int,
        review_status: MemoryReviewStatus,
    ) -> Memory:
        """Apply an explicit review status to an owner-scoped memory."""
        legacy = LegacyCRUD.get_user_legacy(db, legacy_id, user_id)
        memory = cls.get_legacy_memory(db, memory_id, legacy_id)
        if legacy is None or memory is None:
            raise MemoryPersistenceError("Memory not found for user legacy.")
        if review_status == MemoryReviewStatus.SUPERSEDED:
            if memory.superseded_by_memory_id is None:
                raise MemoryPersistenceError(
                    "A superseded memory requires a replacement."
                )

        memory.review_status = review_status
        memory.reviewed_at = datetime.now(timezone.utc)
        memory.reviewed_by_user_id = user_id
        try:
            db.commit()
            db.refresh(memory)
        except Exception:
            db.rollback()
            raise
        return memory

    @classmethod
    def supersede_memory(
        cls,
        db: Session,
        memory_id: int,
        replacement_memory_id: int,
        legacy_id: int,
        user_id: int,
    ) -> Memory:
        """Explicitly supersede a memory with another in the same legacy."""
        if LegacyCRUD.get_user_legacy(db, legacy_id, user_id) is None:
            raise MemoryPersistenceError("Legacy not found for user.")
        memory = cls.get_legacy_memory(db, memory_id, legacy_id)
        replacement = cls.get_legacy_memory(
            db,
            replacement_memory_id,
            legacy_id,
        )
        if memory is None or replacement is None:
            raise MemoryPersistenceError(
                "Both memories must belong to the same legacy."
            )
        if memory.memory_id == replacement.memory_id:
            raise MemoryPersistenceError(
                "A memory cannot supersede itself."
            )

        memory.superseded_by_memory_id = replacement.memory_id
        memory.review_status = MemoryReviewStatus.SUPERSEDED
        memory.reviewed_at = datetime.now(timezone.utc)
        memory.reviewed_by_user_id = user_id
        try:
            db.commit()
            db.refresh(memory)
        except Exception:
            db.rollback()
            raise
        return memory

    @classmethod
    def add_revision(
        cls,
        db: Session,
        memory_id: int,
        legacy_id: int,
        user_id: int,
        previous_content: dict,
        edit_reason: str | None = None,
    ) -> MemoryRevision:
        """Append an immutable pre-edit snapshot to a memory."""
        if LegacyCRUD.get_user_legacy(db, legacy_id, user_id) is None:
            raise MemoryPersistenceError("Legacy not found for user.")
        memory = cls.get_legacy_memory(db, memory_id, legacy_id)
        if memory is None:
            raise MemoryPersistenceError("Memory not found for legacy.")

        last_revision = (
            db.query(func.max(MemoryRevision.revision_number))
            .filter(MemoryRevision.memory_id == memory.memory_id)
            .scalar()
            or 0
        )
        revision = MemoryRevision(
            memory_id=memory.memory_id,
            revision_number=last_revision + 1,
            edited_by_user_id=user_id,
            previous_content=previous_content,
            edit_reason=edit_reason,
        )
        try:
            db.add(revision)
            db.commit()
            db.refresh(revision)
        except Exception:
            db.rollback()
            raise
        return revision

    @classmethod
    def attach_provenance(
        cls,
        db: Session,
        memory_id: int,
        legacy_id: int,
        user_id: int,
        source: MemoryProvenanceCreate,
    ) -> MemoryProvenance:
        """Attach a validated same-legacy source to an existing memory."""
        if LegacyCRUD.get_user_legacy(db, legacy_id, user_id) is None:
            raise MemoryPersistenceError("Legacy not found for user.")
        memory = cls.get_legacy_memory(db, memory_id, legacy_id)
        if memory is None:
            raise MemoryPersistenceError("Memory not found for legacy.")
        cls._validate_provenance(db, legacy_id, source)
        provenance = cls._build_provenance(source)
        provenance.memory_id = memory.memory_id
        try:
            db.add(provenance)
            db.commit()
            db.refresh(provenance)
        except Exception:
            db.rollback()
            raise
        return provenance

    @classmethod
    def _validate_candidate_links(
        cls,
        db: Session,
        legacy_id: int,
        candidate: MemoryCandidateCreate,
    ) -> None:
        """Validate all candidate relations before any persistence."""
        if candidate.contradiction_group_id is not None:
            group = (
                db.query(MemoryContradictionGroup)
                .filter(
                    MemoryContradictionGroup.contradiction_group_id
                    == candidate.contradiction_group_id,
                    MemoryContradictionGroup.legacy_id == legacy_id,
                )
                .first()
            )
            if group is None:
                raise MemoryPersistenceError(
                    "Contradiction group does not belong to legacy."
                )
        if candidate.superseded_by_memory_id is not None:
            replacement = cls.get_legacy_memory(
                db,
                candidate.superseded_by_memory_id,
                legacy_id,
            )
            if replacement is None:
                raise MemoryPersistenceError(
                    "Replacement memory does not belong to legacy."
                )
        for source in candidate.provenance:
            cls._validate_provenance(db, legacy_id, source)

    @staticmethod
    def _validate_provenance(
        db: Session,
        legacy_id: int,
        source: MemoryProvenanceCreate,
    ) -> None:
        """Reject references outside the target legacy or source container."""
        if source.source_type == "conversation":
            conversation = (
                db.query(Conversation)
                .filter(
                    Conversation.conversation_id == source.conversation_id,
                    Conversation.legacy_id == legacy_id,
                )
                .first()
            )
            message = (
                db.query(Message)
                .filter(
                    Message.message_id == source.message_id,
                    Message.conversation_id == source.conversation_id,
                )
                .first()
            )
            if conversation is None or message is None:
                raise MemoryPersistenceError(
                    "Conversation provenance must belong to the legacy."
                )
            message_role = (
                message.role.value
                if hasattr(message.role, "value")
                else str(message.role)
            )
            if (
                message_role != "user"
                or source.speaker != "user"
                or not source.excerpt
                or source.excerpt not in message.content
            ):
                raise MemoryPersistenceError(
                    "Conversation provenance must be verified user evidence."
                )
        elif source.source_type == "story_session":
            story_session = (
                db.query(StorySession)
                .filter(
                    StorySession.story_session_id
                    == source.story_session_id,
                    StorySession.legacy_id == legacy_id,
                )
                .first()
            )
            story_message = (
                db.query(StoryMessage)
                .filter(
                    StoryMessage.story_message_id
                    == source.story_message_id,
                    StoryMessage.story_session_id
                    == source.story_session_id,
                )
                .first()
            )
            if story_session is None or story_message is None:
                raise MemoryPersistenceError(
                    "Story provenance must belong to the legacy."
                )
            story_role = (
                story_message.role.value
                if hasattr(story_message.role, "value")
                else str(story_message.role)
            )
            if (
                story_role != "user"
                or source.speaker != "user"
                or not source.excerpt
                or source.excerpt not in story_message.content
            ):
                raise MemoryPersistenceError(
                    "Story provenance must be verified user evidence."
                )

    @staticmethod
    def _build_provenance(
        source: MemoryProvenanceCreate,
    ) -> MemoryProvenance:
        """Convert a validated application contract to an ORM entity."""
        values = source.model_dump(exclude_none=True, mode="python")
        if source.extracted_at is None:
            values.pop("extracted_at", None)
        return MemoryProvenance(**values)

    @staticmethod
    def _attach_tags(
        db: Session,
        memory: Memory,
        tag_names: list[str],
    ) -> None:
        """Attach normalized tags scoped to the candidate's legacy."""
        for name in tag_names:
            normalized_name = name.casefold()
            tag = (
                db.query(Tag)
                .filter(
                    Tag.legacy_id == memory.legacy_id,
                    Tag.normalized_name == normalized_name,
                )
                .first()
            )
            if tag is None:
                tag = Tag(
                    legacy_id=memory.legacy_id,
                    name=name,
                    normalized_name=normalized_name,
                )
                db.add(tag)
                db.flush()
            memory.tag_links.append(MemoryTag(tag=tag))
