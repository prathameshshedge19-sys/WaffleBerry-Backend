"""L16 private source-library metadata and durable processing state.

Binary data is deliberately outside PostgreSQL. These rows are control-plane
records only; no extraction result can write the canonical memory tables.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SourceKind(str, enum.Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"


class SourceState(str, enum.Enum):
    UPLOADING = "uploading"
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    PARTIALLY_READY = "partially_ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class SourceSafetyState(str, enum.Enum):
    PENDING = "pending"
    CLEAN = "clean"
    REJECTED = "rejected"


class ArtifactKind(str, enum.Enum):
    ORIGINAL = "original"
    STAGING = "staging"
    PREVIEW = "preview"
    TEXT = "text"
    TRANSCRIPT = "transcript"
    EXTRACTED_AUDIO = "extracted_audio"


class ArtifactState(str, enum.Enum):
    RESERVED = "reserved"
    AVAILABLE = "available"
    PURGE_PENDING = "purge_pending"
    PURGED = "purged"


class ProcessingJobKind(str, enum.Enum):
    EXTRACT = "extract"
    PURGE = "purge"


class ProcessingJobState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MediaSource(Base):
    __tablename__ = "media_sources"
    __table_args__ = (
        UniqueConstraint("legacy_id", "id", name="uq_media_sources_legacy_id_id"),
        UniqueConstraint("legacy_id", "uploader_user_id", "upload_request_key", name="uq_media_sources_upload_request"),
        CheckConstraint("declared_size_bytes >= 0", name="ck_media_sources_declared_size"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_media_sources_size"),
        CheckConstraint("generation >= 1", name="ck_media_sources_generation"),
        CheckConstraint("state IN ('uploading','queued','processing','ready','partially_ready','failed','deleting','deleted')", name="ck_media_sources_state"),
        CheckConstraint("safety_state IN ('pending','clean','rejected')", name="ck_media_sources_safety"),
        Index("ix_media_sources_legacy_created", "legacy_id", "created_at", "id"),
        Index("ix_media_sources_legacy_uploader_created", "legacy_id", "uploader_user_id", "created_at", "id"),
        Index("ix_media_sources_state_expiry", "state", "upload_expires_at"),
        Index("ix_media_sources_legacy_sha256", "legacy_id", "sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False)
    uploader_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    detected_mime_type: Mapped[str | None] = mapped_column(String(127))
    declared_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), nullable=False, default=SourceState.UPLOADING.value, server_default=SourceState.UPLOADING.value)
    safety_state: Mapped[str] = mapped_column(String(16), nullable=False, default=SourceSafetyState.PENDING.value, server_default=SourceSafetyState.PENDING.value)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    upload_request_key: Mapped[str] = mapped_column(String(36), nullable=False)
    upload_request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    upload_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))

    artifacts = relationship("MediaArtifact", back_populates="source", cascade="all, delete-orphan")
    jobs = relationship("MediaProcessingJob", back_populates="source", cascade="all, delete-orphan")


class MediaArtifact(Base):
    __tablename__ = "media_artifacts"
    __table_args__ = (
        UniqueConstraint("legacy_id", "source_id", "generation", "id", name="uq_media_artifacts_identity_scope"),
        UniqueConstraint("legacy_id", "source_id", "generation", "logical_key", name="uq_media_artifacts_source_logical"),
        UniqueConstraint("storage_backend", "object_key", name="uq_media_artifacts_storage_key"),
        CheckConstraint("state IN ('reserved','available','purge_pending','purged')", name="ck_media_artifacts_state"),
        CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="ck_media_artifacts_size"),
        ForeignKeyConstraint(("legacy_id", "source_id"), ("media_sources.legacy_id", "media_sources.id"), ondelete="RESTRICT", name="fk_media_artifacts_source_scope"),
        Index("ix_media_artifacts_source_state", "source_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    logical_key: Mapped[str] = mapped_column(String(120), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    object_version: Mapped[str | None] = mapped_column(String(255))
    encryption_key_id: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=ArtifactState.RESERVED.value, server_default=ArtifactState.RESERVED.value)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    mime_type: Mapped[str | None] = mapped_column(String(127))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source = relationship("MediaSource", back_populates="artifacts", foreign_keys=[source_id], primaryjoin="and_(MediaArtifact.source_id == MediaSource.id, MediaArtifact.legacy_id == MediaSource.legacy_id)")


class MediaProcessingJob(Base):
    __tablename__ = "media_processing_jobs"
    __table_args__ = (
        UniqueConstraint("legacy_id", "source_id", "generation", "id", name="uq_media_jobs_identity_scope"),
        UniqueConstraint("source_id", "generation", "kind", "pipeline_version", name="uq_media_jobs_source_generation_kind_pipeline"),
        CheckConstraint("attempts >= 0", name="ck_media_jobs_attempts"),
        CheckConstraint("state IN ('queued','running','retry_wait','succeeded','partial','failed','cancelled')", name="ck_media_jobs_state"),
        CheckConstraint("(lease_token IS NULL AND lease_expires_at IS NULL) OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)", name="ck_media_jobs_lease_pair"),
        ForeignKeyConstraint(("legacy_id", "source_id"), ("media_sources.legacy_id", "media_sources.id"), ondelete="RESTRICT", name="fk_media_jobs_source_scope"),
        Index("ix_media_jobs_claim", "kind", "state", "next_attempt_at", "lease_expires_at"),
        Index("ix_media_jobs_source_generation", "legacy_id", "source_id", "generation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=ProcessingJobState.QUEUED.value, server_default=ProcessingJobState.QUEUED.value)
    stage: Mapped[str] = mapped_column(String(48), nullable=False, default="admitted", server_default="admitted")
    checkpoint_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))

    source = relationship("MediaSource", back_populates="jobs", foreign_keys=[source_id], primaryjoin="and_(MediaProcessingJob.source_id == MediaSource.id, MediaProcessingJob.legacy_id == MediaSource.legacy_id)")
