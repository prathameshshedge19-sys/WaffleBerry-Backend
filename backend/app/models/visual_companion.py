"""L19 presentation metadata; originals and factual records stay in L16.

Publication/approval eligibility and source lifecycle snapshots are revalidated
by commands. FKs enforce scope, not authorization. Physical deletion is restricted
so tombstones and registered cleanup work cannot disappear through a cascade.
"""

from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, String, Text, UniqueConstraint, false, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class VisualCompanion(Base):
    __tablename__ = "visual_companions"
    __table_args__ = (
        UniqueConstraint("legacy_id", name="uq_visual_companions_legacy"),
        UniqueConstraint("legacy_id", "id", name="uq_visual_companions_scope"),
        CheckConstraint("revision >= 1", name="ck_visual_companions_revision"),
        CheckConstraint(
            "NOT enabled OR (deleted_at IS NULL AND current_version_id IS NOT NULL)",
            name="ck_visual_companions_enabled",
        ),
        CheckConstraint(
            "deleted_at IS NULL OR (NOT enabled AND current_version_id IS NULL AND desired_version_id IS NULL)",
            name="ck_visual_companions_deleted",
        ),
        # Named, deferred-to-ALTER DDL breaks the PostgreSQL creation/drop cycle.
        # SQLite emits these inline because it cannot ALTER ADD CONSTRAINT.
        ForeignKeyConstraint(
            ("legacy_id", "id", "current_version_id"),
            ("visual_companion_versions.legacy_id", "visual_companion_versions.companion_id", "visual_companion_versions.id"),
            name="fk_visual_companions_current_scope", ondelete="RESTRICT", use_alter=True,
        ),
        ForeignKeyConstraint(
            ("legacy_id", "id", "desired_version_id"),
            ("visual_companion_versions.legacy_id", "visual_companion_versions.companion_id", "visual_companion_versions.id"),
            name="fk_visual_companions_desired_scope", ondelete="RESTRICT", use_alter=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    current_version_id: Mapped[str | None] = mapped_column(String(36))
    desired_version_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VisualCompanionVersion(Base):
    __tablename__ = "visual_companion_versions"
    __table_args__ = (
        UniqueConstraint("legacy_id", "companion_id", "id", name="uq_visual_versions_scope"),
        UniqueConstraint("companion_id", "version_number", name="uq_visual_versions_number"),
        UniqueConstraint("companion_id", "request_key", name="uq_visual_versions_request"),
        ForeignKeyConstraint(
            ("legacy_id", "companion_id"), ("visual_companions.legacy_id", "visual_companions.id"),
            name="fk_visual_versions_companion_scope", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("legacy_id", "source_id"), ("media_sources.legacy_id", "media_sources.id"),
            name="fk_visual_versions_source_scope", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("legacy_id", "source_id", "source_artifact_generation", "source_artifact_id"),
            ("media_artifacts.legacy_id", "media_artifacts.source_id", "media_artifacts.generation", "media_artifacts.id"),
            name="fk_visual_versions_artifact_scope", ondelete="RESTRICT",
        ),
        CheckConstraint("version_number >= 1", name="ck_visual_versions_number"),
        CheckConstraint("source_generation >= 1 AND source_artifact_generation >= 1", name="ck_visual_versions_generations"),
        CheckConstraint("state IN ('queued','preparing','ready','failed','cancelled','purge_pending','purged')", name="ck_visual_versions_state"),
        CheckConstraint(
            "(approved_by_user_id IS NULL AND approved_at IS NULL) OR (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_visual_versions_approval_pair",
        ),
        CheckConstraint("state <> 'ready' OR bundle_digest IS NOT NULL", name="ck_visual_versions_ready_bundle"),
        CheckConstraint("length(trim(request_key)) > 0 AND length(request_key) <= 128", name="ck_visual_versions_request_key"),
        Index("ix_visual_versions_source", "legacy_id", "source_id"),
        Index("ix_visual_versions_owner_admission", "confirmed_by_user_id", "created_at"),
        Index("ix_visual_versions_legacy_admission", "legacy_id", "created_at"),
        Index("ix_visual_versions_expiry", "state", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    companion_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    source_artifact_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_artifact_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Nullable for erasure; normalized rotation is part of this JSON and digest.
    crop_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    crop_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmation_copy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    recipe_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    recipe_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", server_default="queued")
    bundle_digest: Mapped[str | None] = mapped_column(String(64))
    # RESTRICT preserves the complete approval pair; account erasure is a
    # separate explicit workflow, not an incidental cascade in this schema.
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))


class VisualCompanionAsset(Base):
    __tablename__ = "visual_companion_assets"
    __table_args__ = (
        UniqueConstraint("storage_backend", "object_key", name="uq_visual_assets_storage_key"),
        UniqueConstraint("version_id", "attempt_id", "logical_role", name="uq_visual_assets_attempt_role"),
        ForeignKeyConstraint(
            ("legacy_id", "companion_id", "version_id"),
            ("visual_companion_versions.legacy_id", "visual_companion_versions.companion_id", "visual_companion_versions.id"),
            name="fk_visual_assets_version_scope", ondelete="RESTRICT",
        ),
        CheckConstraint("logical_role IN ('poster','texture_atlas','rig')", name="ck_visual_assets_role"),
        CheckConstraint("state IN ('reserved','available','purge_pending','purged')", name="ck_visual_assets_state"),
        CheckConstraint("write_state IN ('reserved','dispatching','confirmed')", name="ck_visual_assets_write_state"),
        CheckConstraint("absence_checks >= 0", name="ck_visual_assets_absence_checks"),
        CheckConstraint("byte_size IS NULL OR (byte_size >= 0 AND byte_size <= 2097152)", name="ck_visual_assets_size"),
        CheckConstraint(
            "(width IS NULL AND height IS NULL) OR (width IS NOT NULL AND height IS NOT NULL AND width BETWEEN 1 AND 1024 AND height BETWEEN 1 AND 1024)",
            name="ck_visual_assets_dimensions",
        ),
        CheckConstraint(
            "state <> 'available' OR (sha256 IS NOT NULL AND byte_size IS NOT NULL AND byte_size > 0 AND mime_type IS NOT NULL)",
            name="ck_visual_assets_available_metadata",
        ),
        Index(
            "uq_visual_assets_available_role", "version_id", "logical_role", unique=True,
            postgresql_where=text("state = 'available'"), sqlite_where=text("state = 'available'"),
        ),
        Index("ix_visual_assets_version_scope", "legacy_id", "companion_id", "version_id"),
        Index("ix_visual_assets_cleanup", "state", "writer_deadline", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    companion_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    logical_role: Mapped[str] = mapped_column(String(24), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    object_version: Mapped[str | None] = mapped_column(String(255))
    storage_bucket: Mapped[str | None] = mapped_column(String(255))
    write_state: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved", server_default="reserved")
    write_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    absent_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    absence_checks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    encryption_key_id: Mapped[str | None] = mapped_column(String(128))
    sha256: Mapped[str | None] = mapped_column(String(64))
    byte_size: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(127))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved", server_default="reserved")
    writer_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VisualGenerationJob(Base):
    __tablename__ = "visual_generation_jobs"
    __table_args__ = (
        UniqueConstraint("version_id", "kind", name="uq_visual_jobs_version_kind"),
        ForeignKeyConstraint(
            ("legacy_id", "companion_id", "version_id"),
            ("visual_companion_versions.legacy_id", "visual_companion_versions.companion_id", "visual_companion_versions.id"),
            name="fk_visual_jobs_version_scope", ondelete="RESTRICT",
        ),
        CheckConstraint("kind IN ('prepare','purge')", name="ck_visual_jobs_kind"),
        CheckConstraint("state IN ('queued','running','retry_wait','succeeded','failed','cancelled')", name="ck_visual_jobs_state"),
        # Purge has no preparation retry cap: cleanup must remain retryable.
        CheckConstraint("attempts >= 0 AND (kind = 'purge' OR attempts <= 2)", name="ck_visual_jobs_attempts"),
        CheckConstraint(
            "(lease_token IS NULL AND lease_expires_at IS NULL) OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_visual_jobs_lease_pair",
        ),
        Index("ix_visual_jobs_claim", "kind", "state", "next_attempt_at", "lease_expires_at"),
        Index("ix_visual_jobs_version_scope", "legacy_id", "companion_id", "version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    companion_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    writer_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
