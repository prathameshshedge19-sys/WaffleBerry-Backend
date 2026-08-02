"""Transactional orchestration from trusted text sources to Memory candidates."""

import logging
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from time import perf_counter
from typing import Any, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crud.memory import (
    LegacyCRUD,
    MemoryCRUD,
    MemoryPersistenceError,
    StorySessionCRUD,
)
from app.crud.user import MessageCRUD
from app.models.memory import (
    LegacyStatus,
    Memory,
    MemoryReviewStatus,
    StoryMessage,
    StorySession,
    StorySessionStatus,
)
from app.models.user import Conversation, Message
from app.services.memory.extractor import MemoryExtractionService
from app.services.memory.fingerprint import build_memory_fingerprint
from app.services.memory.provenance import (
    ProvenanceSourceRecord,
    RegisteredProvenanceVerifier,
)
from app.services.memory.storage_contracts import (
    MemoryPipelineErrorDetail,
    MemoryPipelineItem,
    MemoryPipelineSourceType,
    MemoryStorageReport,
)
from app.services.memory.storage_exceptions import (
    MemoryCrossLegacyError,
    MemoryOwnershipError,
    MemoryPipelineExtractionError,
    MemoryPipelineValidationError,
    MemorySourceError,
)
from app.services.memory.validation import MemoryValidationService
from app.services.memory.validation_contracts import MemoryValidationStatus


logger = logging.getLogger(__name__)

GUIDED_STORY_AUTO_APPROVAL_CONFIDENCE = Decimal("0.40")


class MemoryStoragePipeline:
    """Extract, validate, and atomically persist each eligible candidate."""

    def __init__(
        self,
        extraction_service: MemoryExtractionService,
        validation_service: MemoryValidationService | None = None,
    ) -> None:
        self._extraction = extraction_service
        self._validation = validation_service or MemoryValidationService()

    async def process_story_session(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        story_session_id: int,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryStorageReport:
        """Process one owner-scoped, persisted Story Session."""
        started = perf_counter()
        legacy = self._require_legacy(db, legacy_id, user_id)
        story_session = StorySessionCRUD.get_legacy_story_session(
            db, story_session_id, legacy_id
        )
        if story_session is None:
            raise MemorySourceError(
                "Story Session was not found for the requested legacy."
            )
        messages = StorySessionCRUD.get_story_messages(
            db, story_session_id, legacy_id
        )
        message_boundary = (
            metadata.get("message_boundary")
            if metadata is not None
            else None
        )
        if message_boundary is not None:
            if not isinstance(message_boundary, int) or message_boundary < 1:
                raise MemorySourceError(
                    "Story extraction message boundary is invalid."
                )
            messages = [
                message
                for message in messages
                if message.sequence <= message_boundary
            ]
        self._verify_story_messages(story_session, messages)
        try:
            candidates = await self._extraction.extract_story_session(
                legacy, story_session, messages
            )
        except Exception as exc:
            raise MemoryPipelineExtractionError(
                "Memory extraction failed for the Story Session."
            ) from exc
        return self._process_candidates(
            db=db,
            user_id=user_id,
            legacy_id=legacy_id,
            legacy_status=legacy.status,
            source_type=MemoryPipelineSourceType.STORY_SESSION,
            source_id=story_session_id,
            story_session=story_session,
            candidates=candidates,
            source_records=self._story_source_records(
                legacy_id, story_session_id, messages
            ),
            started=started,
        )

    async def process_conversation(
        self,
        db: Session,
        *,
        user_id: int,
        legacy_id: int,
        conversation_id: int,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryStorageReport:
        """Process one conversation owned by the user and linked to the legacy."""
        del metadata
        started = perf_counter()
        legacy = self._require_legacy(db, legacy_id, user_id)
        conversation = (
            db.query(Conversation)
            .filter(
                Conversation.conversation_id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.legacy_id == legacy_id,
            )
            .first()
        )
        if conversation is None:
            raise MemorySourceError(
                "Conversation was not found for the requested user legacy."
            )
        messages = MessageCRUD.get_conversation_messages(db, conversation_id)
        self._verify_conversation_messages(conversation, messages)
        try:
            candidates = await self._extraction.extract_conversation(
                legacy, conversation, messages
            )
        except Exception as exc:
            raise MemoryPipelineExtractionError(
                "Memory extraction failed for the conversation."
            ) from exc
        return self._process_candidates(
            db=db,
            user_id=user_id,
            legacy_id=legacy_id,
            legacy_status=legacy.status,
            source_type=MemoryPipelineSourceType.CONVERSATION,
            source_id=conversation_id,
            story_session=None,
            candidates=candidates,
            source_records=self._conversation_source_records(
                legacy_id, conversation_id, messages
            ),
            started=started,
        )

    def _process_candidates(
        self,
        *,
        db: Session,
        user_id: int,
        legacy_id: int,
        legacy_status: LegacyStatus,
        source_type: MemoryPipelineSourceType,
        source_id: int,
        story_session: StorySession | None,
        candidates: Sequence,
        source_records: list[ProvenanceSourceRecord],
        started: float,
    ) -> MemoryStorageReport:
        report = MemoryStorageReport(
            legacy_id=legacy_id,
            source_type=source_type,
            source_id=source_id,
            candidates_extracted=len(candidates),
        )
        verifier = RegisteredProvenanceVerifier(source_records)
        existing = MemoryCRUD.list_legacy_memories(
            db, legacy_id, user_id
        )
        status_counts: Counter[str] = Counter()

        for index, candidate in enumerate(candidates):
            try:
                result = self._validation.validate_candidate(
                    candidate,
                    legacy_id=legacy_id,
                    existing_memories=existing,
                    provenance_verifier=verifier,
                )
            except Exception as exc:
                raise MemoryPipelineValidationError(
                    "Memory candidate validation could not complete."
                ) from exc

            status = result.status
            status_counts[status.value] += 1
            item = MemoryPipelineItem(
                candidate_index=index,
                validation_status=status,
                recommended_action=result.recommended_action,
                related_memory_ids=result.related_memory_ids,
                explanation=result.explanation,
                extraction_confidence=(
                    result.normalized_candidate.extraction_confidence
                    if result.normalized_candidate is not None
                    else None
                ),
                validation_confidence=result.validation_confidence,
            )

            if status == MemoryValidationStatus.DUPLICATE:
                report.duplicates_skipped += 1
            elif status == MemoryValidationStatus.POSSIBLE_DUPLICATE:
                report.possible_duplicates_skipped += 1
            elif status == MemoryValidationStatus.INVALID:
                report.invalid_candidates_skipped += 1
            elif status == MemoryValidationStatus.INSUFFICIENT_INFORMATION:
                report.insufficient_candidates_skipped += 1
            elif status in {
                MemoryValidationStatus.ACCEPTED,
                MemoryValidationStatus.POSSIBLE_ENRICHMENT,
                MemoryValidationStatus.CONTRADICTION,
            }:
                report.candidates_accepted_for_persistence += 1
                self._persist_result(
                    db=db,
                    user_id=user_id,
                    legacy_id=legacy_id,
                    legacy_status=legacy_status,
                    source_type=source_type,
                    story_session=story_session,
                    result=result,
                    item=item,
                    report=report,
                    existing=existing,
                )
            report.items.append(item)

        report.validation_status_counts = dict(status_counts)
        report.duration_ms = max(
            0, int((perf_counter() - started) * 1000)
        )
        logger.info(
            "memory_storage_pipeline_complete",
            extra={
                "legacy_id": legacy_id,
                "source_type": source_type.value,
                "source_id": source_id,
                "candidates_extracted": report.candidates_extracted,
                "validation_status_counts": report.validation_status_counts,
                "memories_created": report.memories_created,
                "candidates_skipped": (
                    report.candidates_extracted - report.memories_created
                ),
                "pipeline_duration_ms": report.duration_ms,
                "error_count": len(report.errors),
            },
        )
        return report

    def _persist_result(
        self,
        *,
        db: Session,
        user_id: int,
        legacy_id: int,
        legacy_status: LegacyStatus,
        source_type: MemoryPipelineSourceType,
        story_session: StorySession | None,
        result,
        item: MemoryPipelineItem,
        report: MemoryStorageReport,
        existing: list[Memory],
    ) -> None:
        candidate = result.normalized_candidate
        if candidate is None:
            item.error_code = "missing_normalized_candidate"
            report.errors.append(
                MemoryPipelineErrorDetail(
                    code=item.error_code,
                    candidate_index=item.candidate_index,
                    message="Eligible validation result had no candidate.",
                )
            )
            return
        fingerprint = build_memory_fingerprint(legacy_id, candidate)
        duplicate = MemoryCRUD.get_memory_by_fingerprint(
            db, legacy_id, fingerprint
        )
        if duplicate is not None:
            item.validation_status = MemoryValidationStatus.DUPLICATE
            item.related_memory_ids = [duplicate.memory_id]
            item.persisted = False
            report.duplicates_skipped += 1
            report.candidates_accepted_for_persistence -= 1
            return

        try:
            with db.begin_nested():
                self._require_related_memories(
                    db, legacy_id, result.related_memory_ids
                )
                group = None
                if result.status == MemoryValidationStatus.CONTRADICTION:
                    group = (
                        MemoryCRUD
                        .get_or_create_contradiction_group_for_memories(
                            db,
                            legacy_id,
                            result.related_memory_ids,
                            candidate.title,
                        )
                    )
                    candidate = candidate.model_copy(
                        update={
                            "contradiction_group_id":
                                group.contradiction_group_id
                        }
                    )
                memory = MemoryCRUD.add_memory_candidate(
                    db,
                    legacy_id,
                    candidate,
                    normalized_fingerprint=fingerprint,
                )
                if result.status == MemoryValidationStatus.POSSIBLE_ENRICHMENT:
                    for related_id in result.related_memory_ids:
                        MemoryCRUD.add_memory_link(
                            db,
                            legacy_id,
                            memory.memory_id,
                            related_id,
                            "possible_enrichment",
                        )
                if self._should_auto_approve(
                    memory=memory,
                    user_id=user_id,
                    legacy_status=legacy_status,
                    source_type=source_type,
                    story_session=story_session,
                    validation_status=result.status,
                ):
                    memory.review_status = MemoryReviewStatus.APPROVED
                    memory.reviewed_at = datetime.now(timezone.utc)
                    memory.reviewed_by_user_id = user_id
            db.commit()
            db.refresh(memory)
        except IntegrityError:
            db.rollback()
            duplicate = MemoryCRUD.get_memory_by_fingerprint(
                db, legacy_id, fingerprint
            )
            if duplicate is not None:
                item.validation_status = MemoryValidationStatus.DUPLICATE
                item.related_memory_ids = [duplicate.memory_id]
                report.duplicates_skipped += 1
                report.candidates_accepted_for_persistence -= 1
                return
            self._record_persistence_error(
                item, report, "persistence_integrity_error"
            )
            return
        except MemoryPersistenceError as exc:
            db.rollback()
            code = (
                "cross_legacy_relationship"
                if "same legacy" in str(exc).casefold()
                or "belong to" in str(exc).casefold()
                else "persistence_invariant_error"
            )
            self._record_persistence_error(item, report, code)
            return
        except Exception:
            db.rollback()
            self._record_persistence_error(
                item, report, "candidate_persistence_error"
            )
            return

        item.persisted = True
        item.memory_id = memory.memory_id
        item.contradiction_group_id = memory.contradiction_group_id
        report.memories_created += 1
        report.created_memory_ids.append(memory.memory_id)
        existing.append(memory)
        if result.status == MemoryValidationStatus.POSSIBLE_ENRICHMENT:
            report.possible_enrichments_persisted += 1
        elif result.status == MemoryValidationStatus.CONTRADICTION:
            report.contradictions_persisted += 1

    @staticmethod
    def _should_auto_approve(
        *,
        memory: Memory,
        user_id: int,
        legacy_status: LegacyStatus,
        source_type: MemoryPipelineSourceType,
        story_session: StorySession | None,
        validation_status: MemoryValidationStatus,
    ) -> bool:
        return (
            source_type == MemoryPipelineSourceType.STORY_SESSION
            and story_session is not None
            and story_session.status == StorySessionStatus.COMPLETED
            and story_session.created_by_user_id == user_id
            and legacy_status == LegacyStatus.ACTIVE
            and validation_status in {
                MemoryValidationStatus.ACCEPTED,
                MemoryValidationStatus.POSSIBLE_ENRICHMENT,
                MemoryValidationStatus.CONTRADICTION,
            }
            and memory.review_status == MemoryReviewStatus.CANDIDATE
            and memory.extraction_confidence is not None
            and memory.extraction_confidence
            >= GUIDED_STORY_AUTO_APPROVAL_CONFIDENCE
            and memory.superseded_by_memory_id is None
        )

    @staticmethod
    def _record_persistence_error(
        item: MemoryPipelineItem,
        report: MemoryStorageReport,
        code: str,
    ) -> None:
        item.error_code = code
        report.errors.append(
            MemoryPipelineErrorDetail(
                code=code,
                candidate_index=item.candidate_index,
                message="The candidate could not be persisted safely.",
            )
        )

    @staticmethod
    def _require_related_memories(
        db: Session,
        legacy_id: int,
        related_memory_ids: Sequence[int],
    ) -> None:
        unique_ids = set(related_memory_ids)
        if not unique_ids:
            return
        matching = (
            db.query(Memory.memory_id)
            .filter(
                Memory.legacy_id == legacy_id,
                Memory.memory_id.in_(unique_ids),
            )
            .count()
        )
        if matching != len(unique_ids):
            raise MemoryPersistenceError(
                "Related memories must belong to the same legacy."
            )

    @staticmethod
    def _require_legacy(db: Session, legacy_id: int, user_id: int):
        legacy = LegacyCRUD.get_user_legacy(db, legacy_id, user_id)
        if legacy is None:
            raise MemoryOwnershipError(
                "The requested legacy is not accessible to this user."
            )
        return legacy

    @staticmethod
    def _verify_story_messages(
        story_session: StorySession,
        messages: Sequence[StoryMessage],
    ) -> None:
        if any(
            message.story_session_id != story_session.story_session_id
            for message in messages
        ):
            raise MemoryCrossLegacyError(
                "A Story Message does not belong to its source session."
            )

    @staticmethod
    def _verify_conversation_messages(
        conversation: Conversation,
        messages: Sequence[Message],
    ) -> None:
        if any(
            message.conversation_id != conversation.conversation_id
            for message in messages
        ):
            raise MemoryCrossLegacyError(
                "A Message does not belong to its source conversation."
            )

    @staticmethod
    def _story_source_records(
        legacy_id: int,
        story_session_id: int,
        messages: Sequence[StoryMessage],
    ) -> list[ProvenanceSourceRecord]:
        return [
            ProvenanceSourceRecord(
                source_type="story_session",
                legacy_id=legacy_id,
                story_session_id=story_session_id,
                story_message_id=message.story_message_id,
                speaker=(
                    message.role.value
                    if hasattr(message.role, "value")
                    else str(message.role)
                ),
                content=message.content,
            )
            for message in messages
        ]

    @staticmethod
    def _conversation_source_records(
        legacy_id: int,
        conversation_id: int,
        messages: Sequence[Message],
    ) -> list[ProvenanceSourceRecord]:
        return [
            ProvenanceSourceRecord(
                source_type="conversation",
                legacy_id=legacy_id,
                conversation_id=conversation_id,
                message_id=message.message_id,
                speaker=(
                    message.role.value
                    if hasattr(message.role, "value")
                    else str(message.role)
                ),
                content=message.content,
            )
            for message in messages
        ]
