"""Transactional orchestration from trusted text sources to Memory candidates."""

import enum
import logging
import re
from contextlib import nullcontext
from collections import Counter
from datetime import datetime, timezone
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
    IdentityFactType,
    Legacy,
    LegacyIdentityFact,
    LegacyStatus,
    Memory,
    MemoryParticipant,
    MemoryRevision,
    MemoryReviewStatus,
    StoryMessage,
    StorySession,
    StorySessionStatus,
)
from app.models.user import Conversation, Message, User
from app.services.quota import QuotaService
from app.services.memory.extractor import MemoryExtractionService
from app.services.memory.canonical_perspective import (
    CanonicalMemoryPerspectiveService,
)
from app.services.memory.fingerprint import build_memory_fingerprint
from app.services.memory.identity_facts import (
    IdentityFactProjectionService,
    normalize_identity_value,
)
from app.services.memory.provenance import (
    ProvenanceSourceRecord,
    RegisteredProvenanceVerifier,
)
from app.services.memory.storage_contracts import (
    MemoryPipelineErrorDetail,
    MemoryPipelineItem,
    MemoryOperation,
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


class MemoryAuthorityClass(str, enum.Enum):
    """Authority boundary for automatic conversational memory writes."""

    PROTECTED_IDENTITY = "protected_identity"
    NORMAL_MEMORY = "normal_memory"

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
            extraction_run_id=(metadata or {}).get("extraction_run_id"),
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
        auto_learned = bool((metadata or {}).get("auto_learned"))
        eligible_candidates = (
            self._durable_candidates(candidates) if auto_learned else candidates
        )
        if auto_learned:
            logger.info(
                "MEMORY_LEARNING source=chat stage=extracted "
                "candidate_count=%d saved_count=0",
                len(candidates),
            )
            for candidate in candidates:
                if candidate in eligible_candidates:
                    continue
                reason = (
                    "low_confidence"
                    if float(candidate.extraction_confidence or 0) < 0.85
                    else "not_durable"
                )
                logger.info(
                    "MEMORY_LEARNING source=chat stage=discarded "
                    "candidate_count=1 saved_count=0 discard_reason=%s",
                    reason,
                )
        return self._process_candidates(
            db=db,
            user_id=user_id,
            legacy_id=legacy_id,
            legacy_status=legacy.status,
            source_type=MemoryPipelineSourceType.CONVERSATION,
            source_id=conversation_id,
            extraction_run_id=None,
            story_session=None,
            candidates=(
                self._mark_auto_learned(eligible_candidates)
                if auto_learned else eligible_candidates
            ),
            source_records=self._conversation_source_records(
                legacy_id, conversation_id, messages
            ),
            started=started,
            auto_approve=auto_learned,
        )

    async def process_live_call_turn(
        self, db: Session, *, user_id: int, legacy_id: int,
        session_safe_id: str, turn_id: int, user_text: str,
    ) -> MemoryStorageReport:
        """Process one final transcription outside the response/audio critical path."""
        started = perf_counter()
        legacy = self._require_legacy(db, legacy_id, user_id)
        candidates = await self._extraction.extract_live_call_turn(
            legacy, session_safe_id=session_safe_id, turn_id=turn_id, user_text=user_text,
        )
        locator = {"session_safe_id": session_safe_id, "turn_id": turn_id}
        return self._process_candidates(
            db=db, user_id=user_id, legacy_id=legacy_id, legacy_status=legacy.status,
            source_type=MemoryPipelineSourceType.LIVE_CALL, source_id=turn_id,
            extraction_run_id=None, story_session=None,
            candidates=self._mark_auto_learned(self._durable_candidates(candidates)),
            source_records=[ProvenanceSourceRecord(
                source_type="live_call", legacy_id=legacy_id, speaker="user",
                content=user_text, source_locator=locator,
            )], started=started, auto_approve=True,
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
        extraction_run_id: int | None,
        story_session: StorySession | None,
        candidates: Sequence,
        source_records: list[ProvenanceSourceRecord],
        started: float,
        auto_approve: bool = False,
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
        legacy = db.query(Legacy).filter(Legacy.legacy_id == legacy_id).one()
        identity_facts = db.query(LegacyIdentityFact).filter(
            LegacyIdentityFact.legacy_id == legacy_id
        ).all()
        perspective = CanonicalMemoryPerspectiveService()
        status_counts: Counter[str] = Counter()

        for index, candidate in enumerate(candidates):
            candidate = perspective.normalize(
                candidate,
                legacy=legacy,
                identity_facts=identity_facts,
                existing_memories=existing,
            )
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

            if (
                auto_approve
                and source_type in {
                    MemoryPipelineSourceType.CONVERSATION,
                    MemoryPipelineSourceType.LIVE_CALL,
                }
                and self._is_protected_chat_identity_mutation(
                    db, legacy_id, result.normalized_candidate
                )
            ):
                item.error_code = "protected_identity_mutation"
                report.items.append(item)
                logger.info(
                    "MEMORY_LEARNING source=chat stage=discarded "
                    "candidate_count=1 saved_count=0 "
                    "discard_reason=protected_identity_mutation"
                )
                continue

            if status == MemoryValidationStatus.DUPLICATE:
                report.duplicates_skipped += 1
            elif (
                status == MemoryValidationStatus.POSSIBLE_DUPLICATE
                and source_type != MemoryPipelineSourceType.STORY_SESSION
            ):
                report.possible_duplicates_skipped += 1
            elif status == MemoryValidationStatus.INVALID:
                report.invalid_candidates_skipped += 1
            elif status == MemoryValidationStatus.INSUFFICIENT_INFORMATION:
                report.insufficient_candidates_skipped += 1
            elif status in {
                MemoryValidationStatus.ACCEPTED,
                MemoryValidationStatus.POSSIBLE_ENRICHMENT,
                MemoryValidationStatus.POSSIBLE_DUPLICATE,
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
                    auto_approve=auto_approve,
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
                "extraction_run_id": extraction_run_id,
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
        auto_approve: bool = False,
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
        operation = self._classify_operation(
            db, legacy_id, result.status, candidate, result.related_memory_ids,
        )
        item.operation = operation
        if (
            auto_approve
            and source_type in {
                MemoryPipelineSourceType.CONVERSATION,
                MemoryPipelineSourceType.LIVE_CALL,
            }
            and result.status == MemoryValidationStatus.POSSIBLE_ENRICHMENT
            and self._merge_normal_memory_enrichment(
                db=db,
                user_id=user_id,
                legacy_id=legacy_id,
                candidate=candidate,
                operation=operation,
                related_memory_ids=result.related_memory_ids,
                item=item,
                report=report,
            )
        ):
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

        will_auto_approve = self._should_auto_approve_candidate(
            user_id=user_id,
            legacy_status=legacy_status,
            source_type=source_type,
            story_session=story_session,
            validation_status=result.status,
            auto_learned=auto_approve,
        )
        capacity_guard = nullcontext(None)
        if will_auto_approve:
            user = db.get(User, user_id)
            capacity_guard = QuotaService(db).memory_capacity_guard(
                user, legacy_id
            )
        try:
            with capacity_guard as capacity:
                if capacity is not None and not capacity.allowed:
                    item.error_code = "memory_quota_exceeded"
                    report.new_memory_skipped_due_to_quota = True
                    report.errors.append(MemoryPipelineErrorDetail(
                        code=item.error_code,
                        candidate_index=item.candidate_index,
                        message=(
                            "A new canonical memory was skipped because this "
                            "Legacy is at capacity."
                        ),
                    ))
                    logger.info(
                        "MEMORY_LEARNING source=%s stage=discarded "
                        "candidate_count=1 saved_count=0 "
                        "discard_reason=memory_quota_exceeded",
                        source_type.value,
                    )
                    return
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
                    if will_auto_approve:
                        memory.review_status = MemoryReviewStatus.APPROVED
                        memory.reviewed_at = datetime.now(timezone.utc)
                        memory.reviewed_by_user_id = user_id
                        db.flush()
                        IdentityFactProjectionService().project_memory(db, memory)
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
        self._record_operation(report, operation)
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
        auto_learned: bool = False,
    ) -> bool:
        return (
            ((source_type == MemoryPipelineSourceType.STORY_SESSION
              and story_session is not None
              and story_session.status == StorySessionStatus.COMPLETED
              and story_session.created_by_user_id == user_id)
             or (source_type in {MemoryPipelineSourceType.CONVERSATION,
                                 MemoryPipelineSourceType.LIVE_CALL} and auto_learned))
            and legacy_status == LegacyStatus.ACTIVE
            and validation_status in {
                MemoryValidationStatus.ACCEPTED,
                MemoryValidationStatus.POSSIBLE_ENRICHMENT,
                MemoryValidationStatus.POSSIBLE_DUPLICATE,
                MemoryValidationStatus.CONTRADICTION,
            }
            and memory.review_status == MemoryReviewStatus.CANDIDATE
            and memory.superseded_by_memory_id is None
        )

    @staticmethod
    def _durable_candidates(candidates: Sequence) -> list:
        """Conservatively admit only high-confidence, future-useful candidates."""
        return [
            candidate for candidate in candidates
            if (candidate.importance or 0) >= 4
            and float(candidate.extraction_confidence or 0) >= 0.85
        ]

    @staticmethod
    def _protected_identity_claims(candidate) -> list[dict]:
        if candidate is None:
            return []
        details = (
            candidate.details.model_dump(mode="python")
            if hasattr(candidate.details, "model_dump") else candidate.details
        )
        claims = details.get("identity_facts", []) if isinstance(details, dict) else []
        protected = {fact_type.value for fact_type in IdentityFactType}
        return [
            claim for claim in claims
            if isinstance(claim, dict)
            and str(getattr(claim.get("fact_type"), "value", claim.get("fact_type")))
            in protected
        ]

    @classmethod
    def authority_class(cls, candidate) -> MemoryAuthorityClass:
        return (
            MemoryAuthorityClass.PROTECTED_IDENTITY
            if cls._protected_identity_claims(candidate)
            else MemoryAuthorityClass.NORMAL_MEMORY
        )

    @staticmethod
    def _should_auto_approve_candidate(
        *, user_id: int, legacy_status: LegacyStatus,
        source_type: MemoryPipelineSourceType,
        story_session: StorySession | None,
        validation_status: MemoryValidationStatus,
        auto_learned: bool = False,
    ) -> bool:
        return (
            ((source_type == MemoryPipelineSourceType.STORY_SESSION
              and story_session is not None
              and story_session.status == StorySessionStatus.COMPLETED
              and story_session.created_by_user_id == user_id)
             or (source_type in {MemoryPipelineSourceType.CONVERSATION,
                                 MemoryPipelineSourceType.LIVE_CALL}
                 and auto_learned))
            and legacy_status == LegacyStatus.ACTIVE
            and validation_status in {
                MemoryValidationStatus.ACCEPTED,
                MemoryValidationStatus.POSSIBLE_ENRICHMENT,
                MemoryValidationStatus.POSSIBLE_DUPLICATE,
                MemoryValidationStatus.CONTRADICTION,
            }
        )

    @classmethod
    def _merge_normal_memory_enrichment(
        cls,
        *,
        db: Session,
        user_id: int,
        legacy_id: int,
        candidate,
        operation: MemoryOperation,
        related_memory_ids: Sequence[int],
        item: MemoryPipelineItem,
        report: MemoryStorageReport,
    ) -> bool:
        """Revision and enrich one unambiguous non-identity canonical memory."""
        unique_ids = list(dict.fromkeys(related_memory_ids))
        if (
            cls.authority_class(candidate)
            == MemoryAuthorityClass.PROTECTED_IDENTITY
            or len(unique_ids) != 1
            or operation not in {MemoryOperation.ENRICH, MemoryOperation.CORRECT}
        ):
            return False
        memory = db.query(Memory).filter(
            Memory.legacy_id == legacy_id,
            Memory.memory_id == unique_ids[0],
            Memory.review_status == MemoryReviewStatus.APPROVED,
            Memory.superseded_by_memory_id.is_(None),
        ).with_for_update().first()
        if memory is None:
            return False
        if db.query(LegacyIdentityFact.identity_fact_id).filter(
            LegacyIdentityFact.source_memory_id == memory.memory_id,
        ).first() is not None:
            return False

        previous = cls._memory_snapshot(memory)
        next_revision = (
            db.query(MemoryRevision.revision_number)
            .filter(MemoryRevision.memory_id == memory.memory_id)
            .order_by(MemoryRevision.revision_number.desc())
            .limit(1).scalar() or 0
        ) + 1
        try:
            with db.begin_nested():
                db.add(MemoryRevision(
                    memory_id=memory.memory_id,
                    revision_number=next_revision,
                    edited_by_user_id=user_id,
                    previous_content=previous,
                    edit_reason="conversation_enrichment",
                ))
                memory.summary = candidate.summary
                from app.services.memory.review import MemoryReviewService
                MemoryReviewService._reconcile_named_claim_correction(
                    db, memory, previous, explicitly_edited={"summary"},
                )
                memory.title = candidate.title
                memory.details = cls._merge_details(
                    memory.details,
                    candidate.details.model_dump(mode="json")
                    if candidate.details is not None else None,
                )
                memory.emotional_significance = (
                    candidate.emotional_significance
                    or memory.emotional_significance
                )
                memory.importance = max(
                    value for value in (memory.importance, candidate.importance)
                    if value is not None
                )
                memory.extraction_confidence = max(
                    value for value in (
                        memory.extraction_confidence,
                        candidate.extraction_confidence,
                    ) if value is not None
                )
                memory.uncertainty_note = (
                    candidate.uncertainty_note or memory.uncertainty_note
                )
                cls._merge_participants(memory, candidate.participants)
                cls._merge_tags(db, memory, candidate.tags)
                memory.provenance.extend(
                    MemoryCRUD._build_provenance(source)
                    for source in candidate.provenance
                )
                memory.normalized_fingerprint = build_memory_fingerprint(
                    legacy_id, candidate
                )
                memory.embedding = None
                memory.embedding_model = None
                memory.embedding_version = None
                memory.embedding_dimensions = None
                memory.embedded_at = None
                memory.updated_at = datetime.now(timezone.utc)
                db.flush()
            db.commit()
            db.refresh(memory)
        except Exception:
            db.rollback()
            return False
        item.persisted = True
        item.memory_id = memory.memory_id
        report.possible_enrichments_persisted += 1
        cls._record_operation(report, operation)
        return True

    @classmethod
    def _classify_operation(
        cls, db: Session, legacy_id: int, status: MemoryValidationStatus,
        candidate, related_memory_ids: Sequence[int],
    ) -> MemoryOperation:
        summary = " ".join(candidate.summary.casefold().split())
        strong_add = bool(re.search(
            r"\b(?:another|additional|second|third|both|two|three|multiple|several)\b",
            summary,
        ))
        correction = bool(re.search(
            r"\b(?:actually|correction|rather than|instead of|not (?:a|an|the))\b",
            summary,
        ))
        if strong_add:
            return MemoryOperation.ADD_ENTITY
        if status != MemoryValidationStatus.POSSIBLE_ENRICHMENT:
            return MemoryOperation.NEW
        unique_ids = list(dict.fromkeys(related_memory_ids))
        if len(unique_ids) != 1:
            return MemoryOperation.NEW
        memory = db.query(Memory).filter(
            Memory.legacy_id == legacy_id,
            Memory.memory_id == unique_ids[0],
        ).first()
        if memory is None:
            return MemoryOperation.NEW
        from app.services.memory.review import MemoryReviewService
        new_name = (
            MemoryReviewService._named_claim(candidate.summary)
            or MemoryReviewService._named_claim(candidate.title)
        )
        old_name = (
            MemoryReviewService._named_claim(memory.summary)
            or MemoryReviewService._named_claim(memory.title)
        )
        distinct_names = bool(
            new_name and old_name and new_name.casefold() != old_name.casefold()
        )
        if distinct_names and not correction:
            return MemoryOperation.ADD_ENTITY
        if distinct_names or correction:
            return MemoryOperation.CORRECT
        return MemoryOperation.ENRICH

    @staticmethod
    def _record_operation(
        report: MemoryStorageReport, operation: MemoryOperation,
    ) -> None:
        report.operation_counts[operation.value] = (
            report.operation_counts.get(operation.value, 0) + 1
        )

    @staticmethod
    def _merge_details(existing, incoming):
        if incoming is None:
            return existing
        if existing is None:
            return incoming
        if isinstance(existing, dict) and isinstance(incoming, dict):
            merged = dict(existing)
            for key, value in incoming.items():
                if value in (None, "", [], {}):
                    continue
                merged[key] = MemoryStoragePipeline._merge_details(
                    merged.get(key), value
                )
            return merged
        if isinstance(existing, list) and isinstance(incoming, list):
            return existing + [item for item in incoming if item not in existing]
        return incoming

    @staticmethod
    def _merge_participants(memory: Memory, participants: Sequence) -> None:
        known = {
            (item.name.casefold(), (item.relationship or "").casefold(), item.role or "")
            for item in memory.participants
        }
        for participant in participants:
            key = (
                participant.name.casefold(),
                (participant.relationship or "").casefold(),
                participant.role or "",
            )
            if key not in known:
                memory.participants.append(MemoryParticipant(
                    **participant.model_dump()
                ))
                known.add(key)

    @staticmethod
    def _merge_tags(db: Session, memory: Memory, tags: Sequence[str]) -> None:
        existing = {link.tag.name.casefold() for link in memory.tag_links}
        MemoryCRUD._attach_tags(
            db, memory,
            [tag for tag in tags if tag.casefold() not in existing],
        )

    @staticmethod
    def _memory_snapshot(memory: Memory) -> dict:
        return {
            "title": memory.title,
            "summary": memory.summary,
            "category": memory.category,
            "memory_type": getattr(memory.memory_type, "value", memory.memory_type),
            "details": memory.details,
            "emotional_significance": memory.emotional_significance,
            "importance": memory.importance,
            "uncertainty_note": memory.uncertainty_note,
            "participants": [{
                "name": item.name,
                "relationship": item.relationship,
                "role": item.role,
            } for item in memory.participants],
            "tags": [link.tag.name for link in memory.tag_links],
        }

    @classmethod
    def _is_protected_chat_identity_mutation(
        cls, db: Session, legacy_id: int, candidate
    ) -> bool:
        """Protect established canonical names only during automatic Chat writes."""
        claims = cls._protected_identity_claims(candidate)
        legacy = db.query(Legacy).filter(Legacy.legacy_id == legacy_id).first()
        existing = db.query(LegacyIdentityFact).filter(
            LegacyIdentityFact.legacy_id == legacy_id
        ).all()
        if not claims:
            summary = normalize_identity_value(
                str(getattr(candidate, "summary", "") or "")
            )
            keywords = {
                IdentityFactType.FULL_NAME.value: ("full name",),
                IdentityFactType.PREFERRED_NAME.value: ("preferred name", "called"),
                IdentityFactType.SPOUSE_NAME.value: ("spouse", "husband", "wife"),
                IdentityFactType.CHILD_NAME.value: ("child", "son", "daughter"),
                IdentityFactType.PARENT_NAME.value: ("parent", "mother", "father"),
                IdentityFactType.SIBLING_NAME.value: ("sibling", "brother", "sister"),
                IdentityFactType.BIRTH_DATE.value: ("born", "birth date", "birthday"),
                IdentityFactType.BIRTHPLACE.value: ("born in", "birthplace"),
                IdentityFactType.HOMETOWN.value: ("hometown",),
                IdentityFactType.OCCUPATION.value: ("occupation", "worked as", "job"),
                IdentityFactType.EDUCATION.value: ("education", "studied at", "degree"),
            }
            for fact in existing:
                fact_type = str(getattr(fact.fact_type, "value", fact.fact_type))
                if (
                    any(label in summary for label in keywords.get(fact_type, ()))
                    and normalize_identity_value(fact.value) not in summary
                ):
                    return True
            return False
        for claim in claims:
            fact_type = str(getattr(
                claim.get("fact_type"), "value", claim.get("fact_type")
            ))
            value = normalize_identity_value(str(claim.get("value") or ""))
            relationship = normalize_identity_value(
                str(claim.get("relationship") or "")
            )
            if not value:
                continue
            if (
                fact_type == IdentityFactType.FULL_NAME.value
                and legacy is not None
                and normalize_identity_value(legacy.display_name) != value
            ):
                return True
            matching = [
                fact for fact in existing
                if str(getattr(fact.fact_type, "value", fact.fact_type)) == fact_type
            ]
            if fact_type not in {
                IdentityFactType.FULL_NAME.value,
                IdentityFactType.SPOUSE_NAME.value,
            }:
                matching = [
                    fact for fact in matching
                    if normalize_identity_value(fact.relationship or "")
                    == relationship
                ]
            if matching and all(
                normalize_identity_value(fact.normalized_value) != value
                for fact in matching
            ):
                return True
        return False

    @staticmethod
    def _mark_auto_learned(candidates: Sequence) -> list:
        """Retain canonical source types while distinguishing automatic provenance."""
        marked = []
        for candidate in candidates:
            provenance = [
                item.model_copy(update={
                    "extractor_version": f"{item.extractor_version or 'memory-extractor-v1'}-auto"
                })
                for item in candidate.provenance
            ]
            marked.append(candidate.model_copy(update={"provenance": provenance}))
        return marked

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
