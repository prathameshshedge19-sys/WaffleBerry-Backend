"""L21 preserved-voice control-plane metadata.

The web process stores only scope, lifecycle, immutable intent/consent evidence,
and exact private-object registrations. It never stores encryption material,
worker paths, or model payloads. Composite foreign keys make a UUID useless as
an authority token and keep every pointer inside one Legacy/profile/version.
"""

from datetime import datetime

from sqlalchemy import (
    JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index,
    Integer, String, Text, UniqueConstraint, event, false, func, inspect, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"
    __table_args__ = (
        UniqueConstraint("legacy_id", "id", name="uq_voice_profiles_scope"),
        CheckConstraint("revision >= 1", name="ck_voice_profiles_revision"),
        CheckConstraint("status IN ('empty','processing','active','revoked','deleting','deleted')", name="ck_voice_profiles_status"),
        CheckConstraint("status <> 'active' OR current_version_id IS NOT NULL", name="ck_voice_profiles_active_pointer"),
        CheckConstraint(
            "status NOT IN ('revoked','deleting','deleted') OR (current_version_id IS NULL AND desired_version_id IS NULL)",
            name="ck_voice_profiles_unselectable",
        ),
        CheckConstraint("status <> 'revoked' OR revoked_at IS NOT NULL", name="ck_voice_profiles_revoked_at"),
        CheckConstraint("status NOT IN ('deleting','deleted') OR deletion_requested_at IS NOT NULL", name="ck_voice_profiles_deletion_at"),
        CheckConstraint("status <> 'deleted' OR deleted_at IS NOT NULL", name="ck_voice_profiles_deleted_at"),
        ForeignKeyConstraint(
            ("legacy_id", "id", "current_version_id"),
            ("voice_profile_versions.legacy_id", "voice_profile_versions.voice_profile_id", "voice_profile_versions.id"),
            name="fk_voice_profiles_current_scope", ondelete="RESTRICT", use_alter=True,
        ),
        ForeignKeyConstraint(
            ("legacy_id", "id", "desired_version_id"),
            ("voice_profile_versions.legacy_id", "voice_profile_versions.voice_profile_id", "voice_profile_versions.id"),
            name="fk_voice_profiles_desired_scope", ondelete="RESTRICT", use_alter=True,
        ),
        Index(
            "uq_voice_profiles_live_legacy", "legacy_id", unique=True,
            postgresql_where=text("status <> 'deleted'"),
            sqlite_where=text("status <> 'deleted'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="empty", server_default="empty")
    current_version_id: Mapped[str | None] = mapped_column(String(36))
    desired_version_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VoiceConsentReceipt(Base):
    __tablename__ = "voice_consent_receipts"
    __table_args__ = (
        UniqueConstraint("legacy_id", "voice_profile_id", "id", name="uq_voice_consent_scope"),
        ForeignKeyConstraint(
            ("legacy_id", "voice_profile_id"), ("voice_profiles.legacy_id", "voice_profiles.id"),
            name="fk_voice_consent_profile_scope", ondelete="RESTRICT",
        ),
        CheckConstraint("length(trim(copy_version)) BETWEEN 1 AND 64", name="ck_voice_consent_copy"),
        CheckConstraint("length(trim(policy_version)) BETWEEN 1 AND 64", name="ck_voice_consent_policy"),
        CheckConstraint("authority_basis IN ('self','authorized_representative','estate_representative')", name="ck_voice_consent_authority"),
        CheckConstraint("source_category IN ('self_recording','authorized_recording')", name="ck_voice_consent_source"),
        CheckConstraint("length(presented_copy_digest) = 64", name="ck_voice_consent_digest"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    voice_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    copy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    authority_basis: Mapped[str] = mapped_column(String(40), nullable=False)
    source_category: Mapped[str] = mapped_column(String(40), nullable=False)
    presented_copy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VoiceProfileVersion(Base):
    __tablename__ = "voice_profile_versions"
    __table_args__ = (
        UniqueConstraint("legacy_id", "voice_profile_id", "id", name="uq_voice_versions_scope"),
        UniqueConstraint("voice_profile_id", "version_number", name="uq_voice_versions_number"),
        UniqueConstraint("voice_profile_id", "request_key", name="uq_voice_versions_request"),
        UniqueConstraint("consent_receipt_id", name="uq_voice_versions_consent"),
        ForeignKeyConstraint(
            ("legacy_id", "voice_profile_id"), ("voice_profiles.legacy_id", "voice_profiles.id"),
            name="fk_voice_versions_profile_scope", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("legacy_id", "voice_profile_id", "consent_receipt_id"),
            ("voice_consent_receipts.legacy_id", "voice_consent_receipts.voice_profile_id", "voice_consent_receipts.id"),
            name="fk_voice_versions_consent_scope", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("legacy_id", "voice_profile_id", "id", "reference_asset_id"),
            ("voice_assets.legacy_id", "voice_assets.voice_profile_id", "voice_assets.version_id", "voice_assets.id"),
            name="fk_voice_versions_reference_asset_scope", ondelete="RESTRICT", use_alter=True,
        ),
        CheckConstraint("version_number >= 1", name="ck_voice_versions_number"),
        CheckConstraint("operation_generation >= 1", name="ck_voice_versions_generation"),
        CheckConstraint("status IN ('uploading','queued','preparing','ready','failed','superseded','purge_pending','purged')", name="ck_voice_versions_status"),
        CheckConstraint("language = 'mr'", name="ck_voice_versions_language"),
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="ck_voice_versions_request_key"),
        CheckConstraint("length(request_digest) = 64", name="ck_voice_versions_request_digest"),
        CheckConstraint(
            "status <> 'ready' OR (reference_asset_id IS NOT NULL AND reference_transcript IS NOT NULL AND reference_transcript_digest IS NOT NULL AND reference_audio_digest IS NOT NULL AND binding_digest IS NOT NULL AND model_manifest_digest IS NOT NULL AND asr_manifest_digest IS NOT NULL AND preparation_recipe_revision IS NOT NULL)",
            name="ck_voice_versions_ready_binding",
        ),
        CheckConstraint("reference_transcript_digest IS NULL OR length(reference_transcript_digest) = 64", name="ck_voice_versions_transcript_digest"),
        CheckConstraint("reference_audio_digest IS NULL OR length(reference_audio_digest) = 64", name="ck_voice_versions_audio_digest"),
        CheckConstraint("binding_digest IS NULL OR length(binding_digest) = 64", name="ck_voice_versions_binding_digest"),
        CheckConstraint("model_manifest_digest IS NULL OR length(model_manifest_digest) = 64", name="ck_voice_versions_model_digest"),
        CheckConstraint("asr_manifest_digest IS NULL OR length(asr_manifest_digest) = 64", name="ck_voice_versions_asr_digest"),
        Index("ix_voice_versions_profile_state", "legacy_id", "voice_profile_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    voice_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploading", server_default="uploading")
    provider_name: Mapped[str] = mapped_column(String(80), nullable=False, default="indicf5", server_default="indicf5")
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="mr", server_default="mr")
    reference_asset_id: Mapped[str | None] = mapped_column(String(36))
    reference_transcript: Mapped[str | None] = mapped_column(Text)
    reference_transcript_digest: Mapped[str | None] = mapped_column(String(64))
    reference_audio_digest: Mapped[str | None] = mapped_column(String(64))
    binding_digest: Mapped[str | None] = mapped_column(String(64))
    model_manifest_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    model_manifest_digest: Mapped[str | None] = mapped_column(String(64))
    asr_manifest_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    asr_manifest_digest: Mapped[str | None] = mapped_column(String(64))
    inference_config_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    inference_config_digest: Mapped[str | None] = mapped_column(String(64))
    preparation_recipe_revision: Mapped[str | None] = mapped_column(String(64))
    consent_receipt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_failure_code: Mapped[str | None] = mapped_column(String(64))


class VoiceJob(Base):
    __tablename__ = "voice_jobs"
    __table_args__ = (
        UniqueConstraint("legacy_id", "voice_profile_id", "version_id", "id", name="uq_voice_jobs_scope"),
        UniqueConstraint("voice_profile_id", "kind", "request_key", name="uq_voice_jobs_request"),
        ForeignKeyConstraint(
            ("legacy_id", "voice_profile_id", "version_id"),
            ("voice_profile_versions.legacy_id", "voice_profile_versions.voice_profile_id", "voice_profile_versions.id"),
            name="fk_voice_jobs_version_scope", ondelete="RESTRICT",
        ),
        CheckConstraint("kind IN ('prepare','synthesize','purge')", name="ck_voice_jobs_kind"),
        CheckConstraint("state IN ('queued','running','retry_wait','succeeded','failed','cancelled')", name="ck_voice_jobs_state"),
        CheckConstraint("priority BETWEEN 0 AND 100", name="ck_voice_jobs_priority"),
        CheckConstraint("attempts >= 0 AND (kind = 'purge' OR attempts <= 3)", name="ck_voice_jobs_attempts"),
        CheckConstraint("operation_generation >= 1", name="ck_voice_jobs_generation"),
        CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="ck_voice_jobs_request_key"),
        CheckConstraint("length(request_digest) = 64", name="ck_voice_jobs_request_digest"),
        CheckConstraint(
            "(lease_token IS NULL AND lease_expires_at IS NULL) OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_voice_jobs_lease_pair",
        ),
        CheckConstraint(
            "kind <> 'synthesize' OR (purpose IN ('preview','message') AND authoritative_text IS NOT NULL "
            "AND length(authoritative_text) BETWEEN 1 AND 4096 AND length(authoritative_text_digest) = 64 "
            "AND length(model_manifest_digest) = 64 AND length(inference_config_digest) = 64 "
            "AND requested_by_user_id IS NOT NULL)",
            name="ck_voice_jobs_synthesis_payload",
        ),
        CheckConstraint(
            "kind <> 'synthesize' OR purpose <> 'message' OR (conversation_id IS NOT NULL AND message_id IS NOT NULL)",
            name="ck_voice_jobs_message_context",
        ),
        Index("ix_voice_jobs_claim", "kind", "state", "priority", "next_attempt_at", "lease_expires_at"),
        Index("ix_voice_jobs_requester", "requested_by_user_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    voice_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", server_default="queued")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    operation_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    writer_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purpose: Mapped[str | None] = mapped_column(String(16))
    authoritative_text: Mapped[str | None] = mapped_column(Text)
    authoritative_text_digest: Mapped[str | None] = mapped_column(String(64))
    model_manifest_digest: Mapped[str | None] = mapped_column(String(64))
    inference_config_digest: Mapped[str | None] = mapped_column(String(64))
    requested_by_user_id: Mapped[int | None] = mapped_column(Integer)
    conversation_id: Mapped[int | None] = mapped_column(Integer)
    message_id: Mapped[int | None] = mapped_column(Integer)


class VoiceAsset(Base):
    __tablename__ = "voice_assets"
    __table_args__ = (
        UniqueConstraint("legacy_id", "voice_profile_id", "version_id", "id", name="uq_voice_assets_scope"),
        UniqueConstraint("storage_backend", "object_key", name="uq_voice_assets_storage_key"),
        ForeignKeyConstraint(
            ("legacy_id", "voice_profile_id", "version_id"),
            ("voice_profile_versions.legacy_id", "voice_profile_versions.voice_profile_id", "voice_profile_versions.id"),
            name="fk_voice_assets_version_scope", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("legacy_id", "voice_profile_id", "version_id", "job_id"),
            ("voice_jobs.legacy_id", "voice_jobs.voice_profile_id", "voice_jobs.version_id", "voice_jobs.id"),
            name="fk_voice_assets_job_scope", ondelete="RESTRICT",
        ),
        CheckConstraint("kind IN ('original','reference','generated')", name="ck_voice_assets_kind"),
        CheckConstraint("state IN ('reserved','dispatching','available','purge_pending','purged')", name="ck_voice_assets_state"),
        CheckConstraint("byte_count IS NULL OR byte_count >= 0", name="ck_voice_assets_bytes"),
        CheckConstraint("sample_rate IS NULL OR sample_rate BETWEEN 8000 AND 192000", name="ck_voice_assets_sample_rate"),
        CheckConstraint("channels IS NULL OR channels BETWEEN 1 AND 8", name="ck_voice_assets_channels"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_voice_assets_duration"),
        CheckConstraint("sha256 IS NULL OR length(sha256) = 64", name="ck_voice_assets_digest"),
        CheckConstraint("state <> 'available' OR (sha256 IS NOT NULL AND byte_count IS NOT NULL AND mime_type IS NOT NULL)", name="ck_voice_assets_available"),
        CheckConstraint("absence_checks >= 0", name="ck_voice_assets_absence"),
        Index("ix_voice_assets_cleanup", "state", "writer_deadline", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    voice_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved", server_default="reserved")
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    object_version: Mapped[str | None] = mapped_column(String(255))
    storage_bucket: Mapped[str | None] = mapped_column(String(255))
    encryption_key_id: Mapped[str | None] = mapped_column(String(128))
    sha256: Mapped[str | None] = mapped_column(String(64))
    byte_count: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(127))
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    channels: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    writer_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    absent_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    absence_checks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@event.listens_for(VoiceConsentReceipt, "before_update")
def _immutable_consent(_mapper, _connection, receipt):
    state = inspect(receipt)
    changed = {item.key for item in state.attrs if item.history.has_changes()}
    if changed - {"revoked_at"}:
        raise ValueError("voice_consent_immutable")
    history = state.attrs.revoked_at.history
    if history.has_changes() and (not history.added or history.added[0] is None or (history.deleted and history.deleted[0] is not None)):
        raise ValueError("voice_consent_revocation_immutable")


@event.listens_for(VoiceConsentReceipt, "before_delete")
def _retain_consent(_mapper, _connection, _receipt):
    raise ValueError("voice_consent_delete_requires_legacy_erasure")
