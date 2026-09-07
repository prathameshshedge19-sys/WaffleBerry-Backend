"""L16 Phase C evidence, review candidates and canonical provenance links."""

import enum
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EvidenceKind(str, enum.Enum):
    TEXT_SPAN = "text_span"
    TRANSCRIPT_SPAN = "transcript_span"
    VISUAL_OBSERVATION = "visual_observation"
    HUMAN_ANNOTATION = "human_annotation"


class CandidateReviewState(str, enum.Enum):
    PENDING = "pending"
    PRESERVED = "preserved"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class CandidateReviewAction(str, enum.Enum):
    PRESERVE = "preserve"
    EDIT_PRESERVE = "edit_preserve"
    SKIP = "skip"


class SupportState(str, enum.Enum):
    APPROVED = "approved"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class SourceEvidence(Base):
    __tablename__ = "source_evidence"
    __table_args__ = (
        UniqueConstraint("legacy_id", "source_id", "id", name="uq_source_evidence_id_scope"),
        UniqueConstraint("legacy_id", "source_id", "generation", "stable_key", name="uq_source_evidence_stable"),
        ForeignKeyConstraint(("legacy_id", "source_id"), ("media_sources.legacy_id", "media_sources.id"), ondelete="RESTRICT", name="fk_source_evidence_source_scope"),
        ForeignKeyConstraint(("legacy_id", "source_id", "generation", "job_id"), ("media_processing_jobs.legacy_id", "media_processing_jobs.source_id", "media_processing_jobs.generation", "media_processing_jobs.id"), ondelete="RESTRICT", name="fk_source_evidence_job_scope"),
        ForeignKeyConstraint(("legacy_id", "source_id", "generation", "artifact_id"), ("media_artifacts.legacy_id", "media_artifacts.source_id", "media_artifacts.generation", "media_artifacts.id"), ondelete="RESTRICT", name="fk_source_evidence_artifact_scope"),
        CheckConstraint("length(text) <= 4000", name="ck_source_evidence_text_bound"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_source_evidence_confidence"),
        Index("ix_source_evidence_source", "legacy_id", "source_id", "generation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(String(36))
    stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    locator_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    language: Mapped[str | None] = mapped_column(String(80))
    confidence: Mapped[float | None]
    origin_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    asserted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source = relationship("MediaSource")


class SourceMemoryCandidate(Base):
    __tablename__ = "source_memory_candidates"
    __table_args__ = (
        UniqueConstraint("legacy_id", "source_id", "generation", "stable_key", name="uq_source_candidates_stable"),
        UniqueConstraint("legacy_id", "source_id", "id", name="uq_source_candidates_id_scope"),
        ForeignKeyConstraint(("legacy_id", "source_id"), ("media_sources.legacy_id", "media_sources.id"), ondelete="RESTRICT", name="fk_source_candidates_source_scope"),
        ForeignKeyConstraint(("legacy_id", "source_id", "generation", "job_id"), ("media_processing_jobs.legacy_id", "media_processing_jobs.source_id", "media_processing_jobs.generation", "media_processing_jobs.id"), ondelete="RESTRICT", name="fk_source_candidates_job_scope"),
        ForeignKeyConstraint(("legacy_id", "canonical_memory_id"), ("memories.legacy_id", "memories.id"), ondelete="SET NULL", name="fk_source_candidates_memory_scope"),
        CheckConstraint("version >= 1", name="ck_source_candidates_version"),
        CheckConstraint("length(CAST(proposal_json AS VARCHAR)) <= 16384", name="ck_source_candidates_proposal_bound"),
        Index("ix_source_candidates_review", "legacy_id", "review_state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    review_draft_json: Mapped[dict | None] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    review_state: Mapped[str] = mapped_column(String(16), nullable=False, default=CandidateReviewState.PENDING.value, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    review_action: Mapped[str | None] = mapped_column(String(24))
    review_request_key: Mapped[str | None] = mapped_column(String(36))
    review_digest: Mapped[str | None] = mapped_column(String(64))
    canonical_memory_id: Mapped[int | None] = mapped_column(Integer)
    promotion_outcome: Mapped[str | None] = mapped_column(String(24))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source = relationship("MediaSource")
    evidence = relationship("SourceEvidence", secondary="source_candidate_evidence", viewonly=True)


class SourceCandidateEvidence(Base):
    __tablename__ = "source_candidate_evidence"
    __table_args__ = (
        ForeignKeyConstraint(("legacy_id", "source_id", "candidate_id"), ("source_memory_candidates.legacy_id", "source_memory_candidates.source_id", "source_memory_candidates.id"), ondelete="CASCADE", name="fk_candidate_evidence_candidate_scope"),
        ForeignKeyConstraint(("legacy_id", "source_id", "evidence_id"), ("source_evidence.legacy_id", "source_evidence.source_id", "source_evidence.id"), ondelete="CASCADE", name="fk_candidate_evidence_evidence_scope"),
        Index("ix_source_candidate_evidence_candidate", "candidate_id"),
    )

    legacy_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)


class MemorySourceLink(Base):
    __tablename__ = "memory_source_links"
    __table_args__ = (
        ForeignKeyConstraint(("legacy_id", "source_id"), ("media_sources.legacy_id", "media_sources.id"), ondelete="RESTRICT", name="fk_memory_source_links_source_scope"),
        ForeignKeyConstraint(("legacy_id", "source_id", "candidate_id"), ("source_memory_candidates.legacy_id", "source_memory_candidates.source_id", "source_memory_candidates.id"), ondelete="RESTRICT", name="fk_memory_source_links_candidate_scope"),
        ForeignKeyConstraint(("legacy_id", "source_id", "evidence_id"), ("source_evidence.legacy_id", "source_evidence.source_id", "source_evidence.id"), ondelete="RESTRICT", name="fk_memory_source_links_evidence_scope"),
        ForeignKeyConstraint(("legacy_id", "memory_id"), ("memories.legacy_id", "memories.id"), ondelete="RESTRICT", name="fk_memory_source_links_memory_scope"),
        UniqueConstraint("legacy_id", "memory_id", "candidate_id", "evidence_id", name="uq_memory_source_link_support"),
        Index("ix_memory_source_links_memory", "legacy_id", "memory_id", "support_state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_id: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(36), nullable=False)
    approved_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    support_state: Mapped[str] = mapped_column(String(16), nullable=False, default=SupportState.APPROVED.value, server_default="approved")
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
