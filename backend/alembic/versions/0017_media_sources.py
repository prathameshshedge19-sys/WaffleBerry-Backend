"""Additive L16 media-library foundation; no extraction or memory backfill."""

from alembic import op
import sqlalchemy as sa


revision = "0017_media_sources"
down_revision = "0016_realtime_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "media_sources",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("uploader_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("declared_mime_type", sa.String(length=127), nullable=False),
        sa.Column("detected_mime_type", sa.String(length=127), nullable=True),
        sa.Column("declared_size_bytes", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="uploading"),
        sa.Column("safety_state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("upload_request_key", sa.String(length=36), nullable=False),
        sa.Column("upload_request_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("upload_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("legacy_id", "id", name="uq_media_sources_legacy_id_id"),
        sa.UniqueConstraint("legacy_id", "uploader_user_id", "upload_request_key", name="uq_media_sources_upload_request"),
        sa.CheckConstraint("declared_size_bytes >= 0", name="ck_media_sources_declared_size"),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_media_sources_size"),
        sa.CheckConstraint("generation >= 1", name="ck_media_sources_generation"),
        sa.CheckConstraint("state IN ('uploading','queued','processing','ready','partially_ready','failed','deleting','deleted')", name="ck_media_sources_state"),
        sa.CheckConstraint("safety_state IN ('pending','clean','rejected')", name="ck_media_sources_safety"),
    )
    op.create_index("ix_media_sources_legacy_created", "media_sources", ["legacy_id", "created_at", "id"])
    op.create_index("ix_media_sources_legacy_uploader_created", "media_sources", ["legacy_id", "uploader_user_id", "created_at", "id"])
    op.create_index("ix_media_sources_state_expiry", "media_sources", ["state", "upload_expires_at"])
    op.create_index("ix_media_sources_legacy_sha256", "media_sources", ["legacy_id", "sha256"])
    op.create_index("ix_media_sources_uploader_user_id", "media_sources", ["uploader_user_id"])

    op.create_table(
        "media_artifacts",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("logical_key", sa.String(length=120), nullable=False),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("object_version", sa.String(length=255), nullable=True),
        sa.Column("encryption_key_id", sa.String(length=128), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="reserved"),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("mime_type", sa.String(length=127), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["legacy_id", "source_id"], ["media_sources.legacy_id", "media_sources.id"], ondelete="RESTRICT", name="fk_media_artifacts_source_scope"),
        sa.UniqueConstraint("legacy_id", "source_id", "generation", "logical_key", name="uq_media_artifacts_source_logical"),
        sa.UniqueConstraint("storage_backend", "object_key", name="uq_media_artifacts_storage_key"),
        sa.CheckConstraint("state IN ('reserved','available','purge_pending','purged')", name="ck_media_artifacts_state"),
        sa.CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="ck_media_artifacts_size"),
    )
    op.create_index("ix_media_artifacts_source_state", "media_artifacts", ["source_id", "state"])

    op.create_table(
        "media_processing_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(length=48), nullable=False, server_default="admitted"),
        sa.Column("checkpoint_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["legacy_id", "source_id"], ["media_sources.legacy_id", "media_sources.id"], ondelete="RESTRICT", name="fk_media_jobs_source_scope"),
        sa.UniqueConstraint("source_id", "generation", "kind", "pipeline_version", name="uq_media_jobs_source_generation_kind_pipeline"),
        sa.CheckConstraint("attempts >= 0", name="ck_media_jobs_attempts"),
        sa.CheckConstraint("state IN ('queued','running','retry_wait','succeeded','partial','failed','cancelled')", name="ck_media_jobs_state"),
        sa.CheckConstraint("(lease_token IS NULL AND lease_expires_at IS NULL) OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)", name="ck_media_jobs_lease_pair"),
    )
    op.create_index("ix_media_jobs_claim", "media_processing_jobs", ["kind", "state", "next_attempt_at", "lease_expires_at"])
    op.create_index("ix_media_jobs_source_generation", "media_processing_jobs", ["legacy_id", "source_id", "generation"])


def downgrade():
    op.drop_table("media_processing_jobs")
    op.drop_table("media_artifacts")
    op.drop_table("media_sources")
