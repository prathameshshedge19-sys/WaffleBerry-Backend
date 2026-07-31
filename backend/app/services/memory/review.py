"""Owner-scoped human review workflow for persisted Memory candidates."""

from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.crud.memory import LegacyCRUD
from app.models.memory import (
    LegacyStatus,
    Memory,
    MemoryLink,
    MemoryParticipant,
    MemoryReviewStatus,
    MemoryRevision,
    MemoryTag,
)
from app.schemas.memory import (
    MemoryCandidateCreate,
    MemoryDetails,
    MemoryParticipantCreate,
    MemoryProvenanceCreate,
    MemoryReviewEditRequest,
    MemoryReviewParticipant,
    MemoryReviewProvenance,
    MemoryReviewResponse,
    RelatedMemoryReview,
)
from app.services.memory.fingerprint import build_memory_fingerprint


class MemoryReviewError(Exception):
    """Safe review-service boundary error."""


class MemoryReviewNotFoundError(MemoryReviewError):
    pass


class MemoryReviewConflictError(MemoryReviewError):
    pass


class MemoryReviewDuplicateError(MemoryReviewConflictError):
    pass


class MemoryReviewArchivedError(MemoryReviewConflictError):
    """Raised when archived Legacy memory content would be mutated."""


class MemoryReviewService:
    """Centralize conservative review transitions and editable projections."""

    def list_memories(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        review_status: MemoryReviewStatus = MemoryReviewStatus.CANDIDATE,
        category: str | None = None,
        memory_type=None,
        source_type: str | None = None,
        has_contradiction: bool | None = None,
        has_enrichment: bool | None = None,
        story_session_id: int | None = None,
        offset: int = 0,
        limit: int = 30,
    ) -> tuple[list[MemoryReviewResponse], int]:
        self._require_legacy(db, legacy_id, user_id)
        query = self._base_query(db, legacy_id).filter(
            Memory.review_status == review_status
        )
        if category:
            query = query.filter(Memory.category == category)
        if memory_type:
            query = query.filter(Memory.memory_type == memory_type)
        if source_type:
            query = query.filter(
                Memory.provenance.any(source_type=source_type)
            )
        if story_session_id:
            query = query.filter(
                Memory.provenance.any(
                    story_session_id=story_session_id
                )
            )
        if has_contradiction is not None:
            predicate = Memory.contradiction_group_id.is_not(None)
            query = query.filter(
                predicate if has_contradiction else ~predicate
            )
        if has_enrichment is not None:
            predicate = or_(
                Memory.outgoing_links.any(
                    MemoryLink.link_type == "possible_enrichment"
                ),
                Memory.incoming_links.any(
                    MemoryLink.link_type == "possible_enrichment"
                ),
            )
            query = query.filter(
                predicate if has_enrichment else ~predicate
            )
        total = query.count()
        memories = (
            query.order_by(
                Memory.importance.desc().nullslast(),
                Memory.created_at.desc(),
                Memory.memory_id.desc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            self._to_response(db, memory, legacy_id)
            for memory in memories
        ], total

    def get_memory(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        memory_id: int,
    ) -> MemoryReviewResponse:
        self._require_legacy(db, legacy_id, user_id)
        memory = self._get_memory(db, legacy_id, memory_id)
        if memory is None:
            raise MemoryReviewNotFoundError(
                "Memory was not found for this legacy."
            )
        return self._to_response(db, memory, legacy_id)

    def approve(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        memory_id: int,
        expected_updated_at: datetime,
    ) -> MemoryReviewResponse:
        return self._transition(
            db,
            user_id=user_id,
            legacy_id=legacy_id,
            memory_id=memory_id,
            expected_updated_at=expected_updated_at,
            target=MemoryReviewStatus.APPROVED,
        )

    def reject(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        memory_id: int,
        expected_updated_at: datetime,
    ) -> MemoryReviewResponse:
        return self._transition(
            db,
            user_id=user_id,
            legacy_id=legacy_id,
            memory_id=memory_id,
            expected_updated_at=expected_updated_at,
            target=MemoryReviewStatus.REJECTED,
        )

    def edit(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        memory_id: int,
        edit: MemoryReviewEditRequest,
    ) -> MemoryReviewResponse:
        self._require_legacy(db, legacy_id, user_id, require_active=True)
        memory = self._locked_candidate(db, legacy_id, memory_id)
        self._require_fresh(memory, edit.expected_updated_at)
        snapshot = self._editable_snapshot(memory)
        fields = edit.model_fields_set - {
            "expected_updated_at",
            "edit_reason",
        }
        scalar_fields = {
            "title",
            "summary",
            "category",
            "memory_type",
            "emotional_significance",
            "importance",
            "uncertainty_note",
        }
        for field in fields & scalar_fields:
            setattr(memory, field, getattr(edit, field))
        if "details" in fields:
            memory.details = (
                edit.details.model_dump(mode="json")
                if edit.details is not None
                else None
            )
        if "participants" in fields:
            memory.participants.clear()
            memory.participants.extend(
                MemoryParticipant(**participant.model_dump())
                for participant in (edit.participants or [])
            )
        if "tags" in fields:
            memory.tag_links.clear()
            self._attach_tags(db, memory, edit.tags or [])

        candidate = self._candidate_projection(memory)
        fingerprint = build_memory_fingerprint(legacy_id, candidate)
        other_memories = (
            self._base_query(db, legacy_id)
            .filter(Memory.memory_id != memory_id)
            .all()
        )
        duplicate = any(
            (
                other.normalized_fingerprint == fingerprint
                if other.normalized_fingerprint
                else build_memory_fingerprint(
                    legacy_id,
                    self._candidate_projection(other),
                ) == fingerprint
            )
            for other in other_memories
        )
        if duplicate:
            db.rollback()
            raise MemoryReviewDuplicateError(
                "An equivalent memory already exists for this legacy."
            )
        revision_number = (
            db.query(MemoryRevision.revision_number)
            .filter(MemoryRevision.memory_id == memory_id)
            .order_by(MemoryRevision.revision_number.desc())
            .limit(1)
            .scalar()
            or 0
        ) + 1
        db.add(
            MemoryRevision(
                memory_id=memory_id,
                revision_number=revision_number,
                edited_by_user_id=user_id,
                previous_content=snapshot,
                edit_reason=edit.edit_reason,
            )
        )
        memory.normalized_fingerprint = fingerprint
        memory.updated_at = datetime.now(timezone.utc)
        try:
            db.commit()
            db.refresh(memory)
        except IntegrityError as exc:
            db.rollback()
            raise MemoryReviewDuplicateError(
                "An equivalent memory already exists for this legacy."
            ) from exc
        return self._to_response(db, memory, legacy_id)

    def _transition(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        memory_id: int,
        expected_updated_at: datetime,
        target: MemoryReviewStatus,
    ) -> MemoryReviewResponse:
        self._require_legacy(db, legacy_id, user_id, require_active=True)
        memory = self._locked_candidate(db, legacy_id, memory_id)
        self._require_fresh(memory, expected_updated_at)
        memory.review_status = target
        memory.reviewed_at = datetime.now(timezone.utc)
        memory.reviewed_by_user_id = user_id
        memory.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(memory)
        return self._to_response(db, memory, legacy_id)

    @staticmethod
    def _require_legacy(
        db: Session,
        legacy_id: int,
        user_id: int,
        require_active: bool = False,
    ) -> None:
        legacy = LegacyCRUD.get_user_legacy(db, legacy_id, user_id)
        if legacy is None:
            raise MemoryReviewNotFoundError(
                "Legacy or memory was not found."
            )
        if require_active and legacy.status == LegacyStatus.ARCHIVED:
            raise MemoryReviewArchivedError(
                "Restore this Legacy before continuing."
            )

    def _locked_candidate(
        self, db: Session, legacy_id: int, memory_id: int
    ) -> Memory:
        memory = (
            self._base_query(db, legacy_id)
            .filter(Memory.memory_id == memory_id)
            .with_for_update()
            .first()
        )
        if memory is None:
            raise MemoryReviewNotFoundError(
                "Legacy or memory was not found."
            )
        if memory.review_status != MemoryReviewStatus.CANDIDATE:
            raise MemoryReviewConflictError(
                "This memory has already been reviewed."
            )
        return memory

    @staticmethod
    def _require_fresh(memory: Memory, expected: datetime) -> None:
        actual = memory.updated_at
        if actual is None:
            raise MemoryReviewConflictError(
                "This memory changed. Refresh and try again."
            )
        actual_value = actual.replace(tzinfo=None)
        expected_value = expected.replace(tzinfo=None)
        if abs((actual_value - expected_value).total_seconds()) > 0.001:
            raise MemoryReviewConflictError(
                "This memory changed. Refresh and try again."
            )

    @staticmethod
    def _base_query(db: Session, legacy_id: int):
        return (
            db.query(Memory)
            .filter(Memory.legacy_id == legacy_id)
            .options(
                joinedload(Memory.provenance),
                joinedload(Memory.participants),
                joinedload(Memory.tag_links).joinedload(MemoryTag.tag),
                joinedload(Memory.outgoing_links),
                joinedload(Memory.incoming_links),
            )
        )

    def _get_memory(
        self, db: Session, legacy_id: int, memory_id: int
    ) -> Memory | None:
        return (
            self._base_query(db, legacy_id)
            .filter(Memory.memory_id == memory_id)
            .first()
        )

    def _to_response(
        self, db: Session, memory: Memory, legacy_id: int
    ) -> MemoryReviewResponse:
        related: list[RelatedMemoryReview] = []
        seen: set[tuple[int, str]] = set()
        if memory.contradiction_group_id is not None:
            conflicts = (
                self._base_query(db, legacy_id)
                .filter(
                    Memory.contradiction_group_id
                    == memory.contradiction_group_id,
                    Memory.memory_id != memory.memory_id,
                )
                .all()
            )
            for item in conflicts:
                related.append(
                    self._related(item, "conflicting")
                )
                seen.add((item.memory_id, "conflicting"))
        link_ids = {
            link.target_memory_id for link in memory.outgoing_links
            if link.link_type == "possible_enrichment"
        } | {
            link.source_memory_id for link in memory.incoming_links
            if link.link_type == "possible_enrichment"
        }
        if link_ids:
            linked = (
                self._base_query(db, legacy_id)
                .filter(Memory.memory_id.in_(link_ids))
                .all()
            )
            for item in linked:
                key = (item.memory_id, "possible_enrichment")
                if key not in seen:
                    related.append(
                        self._related(item, "possible_enrichment")
                    )
        return MemoryReviewResponse(
            memory_id=memory.memory_id,
            memory_type=memory.memory_type,
            category=memory.category,
            title=memory.title,
            summary=memory.summary,
            details=(
                MemoryDetails.model_validate(memory.details)
                if memory.details is not None
                else None
            ),
            emotional_significance=memory.emotional_significance,
            importance=memory.importance,
            extraction_confidence=memory.extraction_confidence,
            uncertainty_note=memory.uncertainty_note,
            review_status=memory.review_status,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
            participants=[
                MemoryReviewParticipant(
                    name=item.name,
                    relationship=item.relationship,
                    role=item.role,
                )
                for item in memory.participants
            ],
            tags=[link.tag.name for link in memory.tag_links],
            provenance=[
                self._provenance(item) for item in memory.provenance
            ],
            related_memories=related,
            has_contradiction=memory.contradiction_group_id is not None,
            has_possible_enrichment=bool(link_ids),
        )

    def _related(self, memory: Memory, relationship: str):
        return RelatedMemoryReview(
            memory_id=memory.memory_id,
            title=memory.title,
            summary=memory.summary,
            review_status=memory.review_status,
            relationship=relationship,
            provenance=[
                self._provenance(item) for item in memory.provenance
            ],
        )

    @staticmethod
    def _provenance(item):
        return MemoryReviewProvenance(
            source_type=item.source_type,
            excerpt=item.excerpt,
            speaker=item.speaker,
            chapter=item.chapter,
            captured_at=item.extracted_at,
            conversation_id=item.conversation_id,
            story_session_id=item.story_session_id,
            story_session_title=(
                item.story_session.title
                if item.story_session is not None
                else None
            ),
        )

    @staticmethod
    def _editable_snapshot(memory: Memory) -> dict:
        return {
            "title": memory.title,
            "summary": memory.summary,
            "category": memory.category,
            "memory_type": (
                memory.memory_type.value
                if hasattr(memory.memory_type, "value")
                else memory.memory_type
            ),
            "details": memory.details,
            "emotional_significance": memory.emotional_significance,
            "importance": memory.importance,
            "uncertainty_note": memory.uncertainty_note,
            "participants": [
                {
                    "name": item.name,
                    "relationship": item.relationship,
                    "role": item.role,
                }
                for item in memory.participants
            ],
            "tags": [link.tag.name for link in memory.tag_links],
        }

    @staticmethod
    def _candidate_projection(memory: Memory) -> MemoryCandidateCreate:
        return MemoryCandidateCreate(
            memory_type=memory.memory_type,
            category=memory.category,
            title=memory.title,
            summary=memory.summary,
            details=(
                MemoryDetails.model_validate(memory.details)
                if memory.details is not None
                else None
            ),
            emotional_significance=memory.emotional_significance,
            importance=memory.importance,
            extraction_confidence=memory.extraction_confidence,
            uncertainty_note=memory.uncertainty_note,
            contradiction_group_id=memory.contradiction_group_id,
            superseded_by_memory_id=memory.superseded_by_memory_id,
            participants=[
                MemoryParticipantCreate(
                    name=item.name,
                    relationship=item.relationship,
                    role=item.role,
                )
                for item in memory.participants
            ],
            tags=[link.tag.name for link in memory.tag_links],
            provenance=[
                MemoryProvenanceCreate(
                    source_type=item.source_type,
                    conversation_id=item.conversation_id,
                    message_id=item.message_id,
                    story_session_id=item.story_session_id,
                    story_message_id=item.story_message_id,
                    source_locator=item.source_locator,
                    excerpt=item.excerpt,
                    speaker=item.speaker,
                    chapter=item.chapter,
                    extracted_at=item.extracted_at,
                    extractor_version=item.extractor_version,
                )
                for item in memory.provenance
            ],
        )

    @staticmethod
    def _attach_tags(
        db: Session, memory: Memory, tag_names: list[str]
    ) -> None:
        from app.models.memory import Tag

        for name in tag_names:
            normalized = name.casefold()
            tag = (
                db.query(Tag)
                .filter(
                    Tag.legacy_id == memory.legacy_id,
                    Tag.normalized_name == normalized,
                )
                .first()
            )
            if tag is None:
                tag = Tag(
                    legacy_id=memory.legacy_id,
                    name=name,
                    normalized_name=normalized,
                )
                db.add(tag)
                db.flush()
            memory.tag_links.append(MemoryTag(tag=tag))
