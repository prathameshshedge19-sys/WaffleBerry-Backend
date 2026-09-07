"""Owner-only review and transactional promotion of source candidates."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.legacy import Legacy, LegacySetupStatus
from app.models.collaboration import CollaboratorStatus, LegacyCollaborator
from app.models.media_intelligence import CandidateReviewAction, CandidateReviewState, EvidenceKind, MemorySourceLink, SourceCandidateEvidence, SourceEvidence, SourceMemoryCandidate, SupportState
from app.models.media_source import MediaSource
from app.models.user import User
from app.schemas.media_intelligence import SourceCandidateProposal
from app.services.authorization import legacy_role
from app.services.memory import LivingMemoryService, MemoryCandidate
from app.services.personality_invalidation import invalidate_in_transaction
from app.services.progression import local_date, record_builder_activity


def now() -> datetime:
    return datetime.now(timezone.utc)


class ReviewConflict(HTTPException):
    def __init__(self, message: str = "The candidate review is no longer current."):
        super().__init__(409, detail={"code": "candidate_review_conflict", "message": message})


def _owner_legacy(db: Session, user_id: int, legacy_id: int, *, lock: bool = True) -> Legacy:
    query = select(Legacy).where(Legacy.id == legacy_id)
    legacy = db.scalar(query.with_for_update() if lock else query)
    if legacy is None or legacy.setup_status != LegacySetupStatus.ACTIVE.value:
        raise HTTPException(404, detail="Legacy not found.")
    if legacy_role(db, user_id, legacy) != "owner":
        raise HTTPException(403, detail="Only the Legacy owner can review source candidates.")
    return legacy


def _candidate(db: Session, legacy_id: int, candidate_id: str) -> SourceMemoryCandidate:
    candidate = db.scalar(select(SourceMemoryCandidate).where(SourceMemoryCandidate.id == candidate_id, SourceMemoryCandidate.legacy_id == legacy_id).with_for_update())
    if candidate is None:
        raise HTTPException(404, detail="Candidate not found.")
    return candidate


def _can_view(db: Session, user_id: int, legacy: Legacy, candidate: SourceMemoryCandidate) -> bool:
    if legacy_role(db, user_id, legacy) == "owner":
        return True
    return bool(db.scalar(select(LegacyCollaborator.id).where(
        LegacyCollaborator.legacy_id == legacy.id, LegacyCollaborator.user_id == user_id,
        LegacyCollaborator.status == CollaboratorStatus.ACTIVE.value,
    ))) and bool(db.scalar(select(MediaSource.id).where(
        MediaSource.id == candidate.source_id,
        MediaSource.uploader_user_id == user_id,
        MediaSource.legacy_id == legacy.id,
    )))


def _digest(action: str, version: int, text: str | None = None) -> str:
    return hashlib.sha256(f"{action}\n{version}\n{text or ''}".encode()).hexdigest()


def serialize_candidate(db: Session, candidate: SourceMemoryCandidate, *, include_content: bool = True) -> dict:
    evidence = list(db.scalars(select(SourceEvidence).join(SourceCandidateEvidence, SourceCandidateEvidence.evidence_id == SourceEvidence.id).where(SourceCandidateEvidence.candidate_id == candidate.id, SourceCandidateEvidence.legacy_id == candidate.legacy_id, SourceCandidateEvidence.source_id == candidate.source_id).order_by(SourceEvidence.created_at, SourceEvidence.id)).all())
    return {
        "id": candidate.id, "legacy_id": candidate.legacy_id, "source_id": candidate.source_id,
        "generation": candidate.generation, "proposal": candidate.proposal_json if include_content else {},
        "review_draft": candidate.review_draft_json if include_content else None, "version": candidate.version,
        "review_state": candidate.review_state, "reviewed_at": candidate.reviewed_at,
        "reviewed_by_user_id": candidate.reviewed_by_user_id, "review_action": candidate.review_action,
        "canonical_memory_id": candidate.canonical_memory_id, "promotion_outcome": candidate.promotion_outcome,
        "evidence": [{"id": item.id, "kind": item.kind, "text": item.text, "locator": item.locator_json, "language": item.language, "confidence": item.confidence, "origin": item.origin_json} for item in evidence],
    }


class MediaReviewService:
    def __init__(self, memory_service: LivingMemoryService | None = None):
        self.memory = memory_service

    def list_candidates(self, db: Session, user: User, legacy_id: int, source_id: str | None = None) -> list[dict]:
        legacy = db.get(Legacy, legacy_id)
        if legacy is None or legacy.setup_status != LegacySetupStatus.ACTIVE.value:
            raise HTTPException(404, detail="Legacy not found.")
        query = select(SourceMemoryCandidate).where(SourceMemoryCandidate.legacy_id == legacy.id, SourceMemoryCandidate.removed_at.is_(None))
        if source_id:
            query = query.where(SourceMemoryCandidate.source_id == source_id)
        rows = db.scalars(query.order_by(SourceMemoryCandidate.created_at, SourceMemoryCandidate.id)).all()
        return [serialize_candidate(db, row) for row in rows if _can_view(db, user.id, legacy, row)]

    def get_candidate(self, db: Session, user: User, legacy_id: int, candidate_id: str) -> dict:
        legacy = db.get(Legacy, legacy_id)
        if legacy is None or legacy.setup_status != LegacySetupStatus.ACTIVE.value:
            raise HTTPException(404, detail="Legacy not found.")
        candidate = db.scalar(select(SourceMemoryCandidate).where(SourceMemoryCandidate.id == candidate_id, SourceMemoryCandidate.legacy_id == legacy_id))
        if candidate is None or not _can_view(db, user.id, legacy, candidate):
            raise HTTPException(404, detail="Candidate not found.")
        return serialize_candidate(db, candidate)

    async def prepare_edit(self, db: Session, user: User, legacy_id: int, candidate_id: str, *, canonical_text: str, category: str | None, expected_version: int, entities: list[dict] | None = None, source_language: str | None = None) -> dict:
        legacy = _owner_legacy(db, user.id, legacy_id)
        candidate_preview = db.scalar(select(SourceMemoryCandidate).where(SourceMemoryCandidate.id == candidate_id, SourceMemoryCandidate.legacy_id == legacy.id))
        if candidate_preview is None:
            raise HTTPException(404, detail="Candidate not found.")
        source = db.scalar(select(MediaSource).where(MediaSource.id == candidate_preview.source_id, MediaSource.legacy_id == legacy.id).with_for_update())
        candidate = _candidate(db, legacy.id, candidate_id)
        if source is None or source.state in {"deleting", "deleted"} or candidate.review_state != CandidateReviewState.PENDING.value or candidate.version != expected_version or candidate.generation != source.generation:
            raise ReviewConflict()
        text = " ".join(canonical_text.split()).strip()
        if len(text) < 3 or len(text) > 1200:
            raise HTTPException(422, detail="Edited candidate text is invalid.")
        proposal = SourceCandidateProposal.model_validate(candidate.proposal_json)
        draft = {"canonical_text": text, "category": category or proposal.category, "source_language": source_language or proposal.source_language, "entities": entities if entities is not None else [item.model_dump(mode="json") for item in proposal.entities], "edited_by_user_id": user.id, "human_edited": True}
        candidate.review_draft_json = draft; candidate.version += 1
        db.commit(); db.refresh(candidate)
        return serialize_candidate(db, candidate)

    async def review(self, db: Session, user: User, legacy_id: int, candidate_id: str, *, action: str, expected_version: int, review_request_key: str, timezone_name: str = "UTC") -> dict:
        try:
            request_key = str(UUID(review_request_key))
        except (ValueError, AttributeError):
            raise HTTPException(422, detail={"code": "invalid_review_request_key", "message": "Review request key is invalid."}) from None
        if action not in {item.value for item in CandidateReviewAction}:
            raise HTTPException(422, detail={"code": "invalid_review_action", "message": "Review action is invalid."})
        _owner_legacy(db, user.id, legacy_id, lock=False)
        # Embedding/provider work is deliberately outside the write transaction.
        preview = db.scalar(select(SourceMemoryCandidate).where(SourceMemoryCandidate.id == candidate_id, SourceMemoryCandidate.legacy_id == legacy_id))
        if preview is None:
            raise HTTPException(404, detail="Candidate not found.")
        if preview.review_state != CandidateReviewState.PENDING.value:
            if preview.review_request_key == request_key and preview.review_digest == _digest(action, expected_version):
                return serialize_candidate(db, preview)
            raise ReviewConflict("This candidate already has a final review decision.")
        if preview.version != expected_version:
            raise ReviewConflict()
        if action == CandidateReviewAction.EDIT_PRESERVE.value and not preview.review_draft_json:
            raise ReviewConflict("Prepare an edit preview before preserving edited wording.")
        if action != CandidateReviewAction.SKIP.value and self.memory is None:
            raise ReviewConflict("Canonical memory provider is unavailable; the candidate remains pending.")
        proposal_preview = SourceCandidateProposal.model_validate(preview.proposal_json)
        draft_preview = preview.review_draft_json if action == CandidateReviewAction.EDIT_PRESERVE.value else None
        canonical_preview = (draft_preview or {}).get("canonical_text", proposal_preview.canonical_text)
        embedding = await self.memory.provider.embed([canonical_preview]) if action != CandidateReviewAction.SKIP.value else []
        if action != CandidateReviewAction.SKIP.value and len(embedding) != 1:
            raise ReviewConflict("Canonical embedding is unavailable; the candidate remains pending.")
        db.rollback()
        with db.begin():
            legacy = _owner_legacy(db, user.id, legacy_id)
            candidate_preview = db.scalar(select(SourceMemoryCandidate).where(SourceMemoryCandidate.id == candidate_id, SourceMemoryCandidate.legacy_id == legacy.id))
            if candidate_preview is None:
                raise HTTPException(404, detail="Candidate not found.")
            source = db.scalar(select(MediaSource).where(MediaSource.id == candidate_preview.source_id, MediaSource.legacy_id == legacy.id).with_for_update())
            candidate = _candidate(db, legacy.id, candidate_id)
            if source is None:
                raise HTTPException(404, detail="Source not found.")
            digest = _digest(action, expected_version)
            if candidate.review_state != CandidateReviewState.PENDING.value:
                if candidate.review_request_key == request_key and candidate.review_digest == digest:
                    return serialize_candidate(db, candidate)
                raise ReviewConflict("This candidate already has a final review decision.")
            if candidate.version != expected_version or candidate.generation != source.generation:
                raise ReviewConflict()
            if action == CandidateReviewAction.SKIP.value:
                candidate.review_state = CandidateReviewState.SKIPPED.value; candidate.review_action = action; candidate.reviewed_by_user_id = user.id; candidate.reviewed_at = now(); candidate.review_request_key = request_key; candidate.review_digest = digest
                return serialize_candidate(db, candidate)
            if source.state in {"deleting", "deleted"} or source.safety_state != "clean":
                raise HTTPException(410, detail={"code": "source_unavailable", "message": "The source is no longer available for approval."})
            evidence = list(db.scalars(select(SourceEvidence).join(SourceCandidateEvidence, SourceCandidateEvidence.evidence_id == SourceEvidence.id).where(SourceCandidateEvidence.candidate_id == candidate.id, SourceCandidateEvidence.legacy_id == legacy.id, SourceCandidateEvidence.source_id == source.id, SourceEvidence.removed_at.is_(None))).all())
            if not evidence:
                raise HTTPException(409, detail={"code": "candidate_evidence_unavailable", "message": "This candidate has no remaining evidence."})
            proposal = SourceCandidateProposal.model_validate(candidate.proposal_json)
            draft = candidate.review_draft_json if action == CandidateReviewAction.EDIT_PRESERVE.value else None
            canonical_text = (draft or {}).get("canonical_text", proposal.canonical_text)
            category = (draft or {}).get("category", proposal.category)
            source_language = (draft or {}).get("source_language", proposal.source_language)
            entities = (draft or {}).get("entities", [item.model_dump(mode="json") for item in proposal.entities])
            if canonical_text != canonical_preview:
                raise ReviewConflict("The candidate changed while its review was prepared.")
            if action == CandidateReviewAction.EDIT_PRESERVE.value and draft and draft.get("human_edited"):
                edit_key = hashlib.sha256(f"owner-edit\n{candidate.id}\n{candidate.version}\n{canonical_text}".encode()).hexdigest()
                edit_evidence = db.scalar(select(SourceEvidence).where(
                    SourceEvidence.legacy_id == legacy.id,
                    SourceEvidence.source_id == source.id,
                    SourceEvidence.generation == candidate.generation,
                    SourceEvidence.stable_key == edit_key,
                ).with_for_update())
                if edit_evidence is None:
                    edit_evidence = SourceEvidence(
                        id=str(uuid4()), legacy_id=legacy.id, source_id=source.id,
                        generation=candidate.generation, job_id=candidate.job_id,
                        stable_key=edit_key, kind=EvidenceKind.HUMAN_ANNOTATION.value,
                        text=canonical_text, locator_json={"kind": "owner_edit"},
                        language=source_language, origin_json={"kind": "owner_review_edit"},
                        asserted_by_user_id=user.id,
                    )
                    db.add(edit_evidence)
                    db.flush()
                evidence.append(edit_evidence)
            existing, outcome = self.memory.preserve_reviewed_source(
                db, legacy,
                MemoryCandidate(canonical_text=canonical_text, category=category, confidence=proposal.confidence, entities=entities),
                source_language=source_language, user_id=user.id, vector=embedding[0],
            )
            approved_hash = hashlib.sha256(canonical_text.encode()).hexdigest()
            for item in evidence:
                candidate_evidence = db.get(SourceCandidateEvidence, (legacy.id, source.id, candidate.id, item.id))
                if candidate_evidence is None:
                    db.add(SourceCandidateEvidence(legacy_id=legacy.id, source_id=source.id, candidate_id=candidate.id, evidence_id=item.id))
                link_exists = db.scalar(select(MemorySourceLink).where(MemorySourceLink.legacy_id == legacy.id, MemorySourceLink.memory_id == existing.id, MemorySourceLink.candidate_id == candidate.id, MemorySourceLink.evidence_id == item.id))
                if link_exists is None:
                    db.add(MemorySourceLink(id=str(uuid4()), legacy_id=legacy.id, source_id=source.id, generation=candidate.generation, memory_id=existing.id, candidate_id=candidate.id, evidence_id=item.id, approved_text_sha256=approved_hash, approved_by_user_id=user.id, support_state=SupportState.APPROVED.value))
            if outcome == "linked_existing":
                invalidate_in_transaction(db.connection(), [legacy.id])
            if outcome == "created":
                record_builder_activity(db, user_id=user.id, legacy_id=legacy.id, activity_type="new", memory_id=existing.id, activity_date=local_date(timezone_name), commit=False)
            candidate.review_state = CandidateReviewState.PRESERVED.value; candidate.review_action = action; candidate.reviewed_by_user_id = user.id; candidate.reviewed_at = now(); candidate.review_request_key = request_key; candidate.review_digest = digest; candidate.canonical_memory_id = existing.id; candidate.promotion_outcome = outcome
            db.flush()
            return serialize_candidate(db, candidate)
