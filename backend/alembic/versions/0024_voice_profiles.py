"""Add the L21 preserved-voice control plane; no media/model processing.

Downgrade is schema acceptance only. Production erasure must complete through
the durable registry before these tables are removed.
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_voice_profiles"
down_revision = "0023_plan_shadow_usage"
branch_labels = None
depends_on = None


def upgrade():
    sqlite = op.get_bind().dialect.name == "sqlite"
    inline_profile_pointers = (
        sa.ForeignKeyConstraint(
            ["legacy_id", "id", "current_version_id"],
            ["voice_profile_versions.legacy_id", "voice_profile_versions.voice_profile_id", "voice_profile_versions.id"],
            name="fk_voice_profiles_current_scope", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["legacy_id", "id", "desired_version_id"],
            ["voice_profile_versions.legacy_id", "voice_profile_versions.voice_profile_id", "voice_profile_versions.id"],
            name="fk_voice_profiles_desired_scope", ondelete="RESTRICT"),
    ) if sqlite else ()
    op.create_table(
        "voice_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="empty"),
        sa.Column("current_version_id", sa.String(36)),
        sa.Column("desired_version_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("legacy_id", "id", name="uq_voice_profiles_scope"),
        sa.CheckConstraint("revision >= 1", name="ck_voice_profiles_revision"),
        sa.CheckConstraint("status IN ('empty','processing','active','revoked','deleting','deleted')", name="ck_voice_profiles_status"),
        sa.CheckConstraint("status <> 'active' OR current_version_id IS NOT NULL", name="ck_voice_profiles_active_pointer"),
        sa.CheckConstraint("status NOT IN ('revoked','deleting','deleted') OR (current_version_id IS NULL AND desired_version_id IS NULL)", name="ck_voice_profiles_unselectable"),
        sa.CheckConstraint("status <> 'revoked' OR revoked_at IS NOT NULL", name="ck_voice_profiles_revoked_at"),
        sa.CheckConstraint("status NOT IN ('deleting','deleted') OR deletion_requested_at IS NOT NULL", name="ck_voice_profiles_deletion_at"),
        sa.CheckConstraint("status <> 'deleted' OR deleted_at IS NOT NULL", name="ck_voice_profiles_deleted_at"),
        *inline_profile_pointers,
    )
    op.create_index("uq_voice_profiles_live_legacy", "voice_profiles", ["legacy_id"], unique=True,
        postgresql_where=sa.text("status <> 'deleted'"), sqlite_where=sa.text("status <> 'deleted'"))

    op.create_table(
        "voice_consent_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("voice_profile_id", sa.String(36), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("copy_version", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("authority_basis", sa.String(40), nullable=False),
        sa.Column("source_category", sa.String(40), nullable=False),
        sa.Column("presented_copy_digest", sa.String(64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("legacy_id", "voice_profile_id", "id", name="uq_voice_consent_scope"),
        sa.ForeignKeyConstraint(["legacy_id", "voice_profile_id"], ["voice_profiles.legacy_id", "voice_profiles.id"],
            name="fk_voice_consent_profile_scope", ondelete="RESTRICT"),
        sa.CheckConstraint("length(trim(copy_version)) BETWEEN 1 AND 64", name="ck_voice_consent_copy"),
        sa.CheckConstraint("length(trim(policy_version)) BETWEEN 1 AND 64", name="ck_voice_consent_policy"),
        sa.CheckConstraint("authority_basis IN ('self','authorized_representative','estate_representative')", name="ck_voice_consent_authority"),
        sa.CheckConstraint("source_category IN ('self_recording','authorized_recording')", name="ck_voice_consent_source"),
        sa.CheckConstraint("length(presented_copy_digest) = 64", name="ck_voice_consent_digest"),
    )

    inline_reference = (
        sa.ForeignKeyConstraint(
            ["legacy_id", "voice_profile_id", "id", "reference_asset_id"],
            ["voice_assets.legacy_id", "voice_assets.voice_profile_id", "voice_assets.version_id", "voice_assets.id"],
            name="fk_voice_versions_reference_asset_scope", ondelete="RESTRICT"),
    ) if sqlite else ()
    op.create_table(
        "voice_profile_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("voice_profile_id", sa.String(36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("operation_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="uploading"),
        sa.Column("provider_name", sa.String(80), nullable=False, server_default="indicf5"),
        sa.Column("language", sa.String(8), nullable=False, server_default="mr"),
        sa.Column("reference_asset_id", sa.String(36)),
        sa.Column("reference_transcript", sa.Text()),
        sa.Column("reference_transcript_digest", sa.String(64)),
        sa.Column("reference_audio_digest", sa.String(64)),
        sa.Column("binding_digest", sa.String(64)),
        sa.Column("model_manifest_json", sa.JSON(none_as_null=True)),
        sa.Column("model_manifest_digest", sa.String(64)),
        sa.Column("asr_manifest_json", sa.JSON(none_as_null=True)),
        sa.Column("asr_manifest_digest", sa.String(64)),
        sa.Column("inference_config_json", sa.JSON(none_as_null=True)),
        sa.Column("inference_config_digest", sa.String(64)),
        sa.Column("preparation_recipe_revision", sa.String(64)),
        sa.Column("consent_receipt_id", sa.String(36), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.Column("safe_failure_code", sa.String(64)),
        sa.UniqueConstraint("legacy_id", "voice_profile_id", "id", name="uq_voice_versions_scope"),
        sa.UniqueConstraint("voice_profile_id", "version_number", name="uq_voice_versions_number"),
        sa.UniqueConstraint("voice_profile_id", "request_key", name="uq_voice_versions_request"),
        sa.UniqueConstraint("consent_receipt_id", name="uq_voice_versions_consent"),
        sa.ForeignKeyConstraint(["legacy_id", "voice_profile_id"], ["voice_profiles.legacy_id", "voice_profiles.id"],
            name="fk_voice_versions_profile_scope", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["legacy_id", "voice_profile_id", "consent_receipt_id"],
            ["voice_consent_receipts.legacy_id", "voice_consent_receipts.voice_profile_id", "voice_consent_receipts.id"],
            name="fk_voice_versions_consent_scope", ondelete="RESTRICT"),
        sa.CheckConstraint("version_number >= 1", name="ck_voice_versions_number"),
        sa.CheckConstraint("operation_generation >= 1", name="ck_voice_versions_generation"),
        sa.CheckConstraint("status IN ('uploading','queued','preparing','ready','failed','superseded','purge_pending','purged')", name="ck_voice_versions_status"),
        sa.CheckConstraint("language = 'mr'", name="ck_voice_versions_language"),
        sa.CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="ck_voice_versions_request_key"),
        sa.CheckConstraint("length(request_digest) = 64", name="ck_voice_versions_request_digest"),
        sa.CheckConstraint("status <> 'ready' OR (reference_asset_id IS NOT NULL AND reference_transcript IS NOT NULL AND reference_transcript_digest IS NOT NULL AND reference_audio_digest IS NOT NULL AND binding_digest IS NOT NULL AND model_manifest_digest IS NOT NULL AND asr_manifest_digest IS NOT NULL AND preparation_recipe_revision IS NOT NULL)", name="ck_voice_versions_ready_binding"),
        sa.CheckConstraint("reference_transcript_digest IS NULL OR length(reference_transcript_digest) = 64", name="ck_voice_versions_transcript_digest"),
        sa.CheckConstraint("reference_audio_digest IS NULL OR length(reference_audio_digest) = 64", name="ck_voice_versions_audio_digest"),
        sa.CheckConstraint("binding_digest IS NULL OR length(binding_digest) = 64", name="ck_voice_versions_binding_digest"),
        sa.CheckConstraint("model_manifest_digest IS NULL OR length(model_manifest_digest) = 64", name="ck_voice_versions_model_digest"),
        sa.CheckConstraint("asr_manifest_digest IS NULL OR length(asr_manifest_digest) = 64", name="ck_voice_versions_asr_digest"),
        *inline_reference,
    )
    op.create_index("ix_voice_versions_profile_state", "voice_profile_versions", ["legacy_id", "voice_profile_id", "status"])

    op.create_table(
        "voice_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("voice_profile_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operation_generation", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("writer_deadline", sa.DateTime(timezone=True)),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("safe_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("legacy_id", "voice_profile_id", "version_id", "id", name="uq_voice_jobs_scope"),
        sa.UniqueConstraint("voice_profile_id", "kind", "request_key", name="uq_voice_jobs_request"),
        sa.ForeignKeyConstraint(
            ["legacy_id", "voice_profile_id", "version_id"],
            ["voice_profile_versions.legacy_id", "voice_profile_versions.voice_profile_id", "voice_profile_versions.id"],
            name="fk_voice_jobs_version_scope", ondelete="RESTRICT"),
        sa.CheckConstraint("kind IN ('prepare','synthesize','purge')", name="ck_voice_jobs_kind"),
        sa.CheckConstraint("state IN ('queued','running','retry_wait','succeeded','failed','cancelled')", name="ck_voice_jobs_state"),
        sa.CheckConstraint("priority BETWEEN 0 AND 100", name="ck_voice_jobs_priority"),
        sa.CheckConstraint("attempts >= 0 AND (kind = 'purge' OR attempts <= 3)", name="ck_voice_jobs_attempts"),
        sa.CheckConstraint("operation_generation >= 1", name="ck_voice_jobs_generation"),
        sa.CheckConstraint("length(trim(request_key)) BETWEEN 1 AND 128", name="ck_voice_jobs_request_key"),
        sa.CheckConstraint("length(request_digest) = 64", name="ck_voice_jobs_request_digest"),
        sa.CheckConstraint("(lease_token IS NULL AND lease_expires_at IS NULL) OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)", name="ck_voice_jobs_lease_pair"),
    )
    op.create_index("ix_voice_jobs_claim", "voice_jobs", ["kind", "state", "priority", "next_attempt_at", "lease_expires_at"])

    op.create_table(
        "voice_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("voice_profile_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("job_id", sa.String(36)),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="reserved"),
        sa.Column("storage_backend", sa.String(32), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("object_version", sa.String(255)),
        sa.Column("storage_bucket", sa.String(255)),
        sa.Column("encryption_key_id", sa.String(128)),
        sa.Column("sha256", sa.String(64)),
        sa.Column("byte_count", sa.Integer()),
        sa.Column("mime_type", sa.String(127)),
        sa.Column("sample_rate", sa.Integer()),
        sa.Column("channels", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("writer_deadline", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("purge_requested_at", sa.DateTime(timezone=True)),
        sa.Column("absent_since", sa.DateTime(timezone=True)),
        sa.Column("absence_checks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("legacy_id", "voice_profile_id", "version_id", "id", name="uq_voice_assets_scope"),
        sa.UniqueConstraint("storage_backend", "object_key", name="uq_voice_assets_storage_key"),
        sa.ForeignKeyConstraint(
            ["legacy_id", "voice_profile_id", "version_id"],
            ["voice_profile_versions.legacy_id", "voice_profile_versions.voice_profile_id", "voice_profile_versions.id"],
            name="fk_voice_assets_version_scope", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["legacy_id", "voice_profile_id", "version_id", "job_id"],
            ["voice_jobs.legacy_id", "voice_jobs.voice_profile_id", "voice_jobs.version_id", "voice_jobs.id"],
            name="fk_voice_assets_job_scope", ondelete="RESTRICT"),
        sa.CheckConstraint("kind IN ('original','reference','generated')", name="ck_voice_assets_kind"),
        sa.CheckConstraint("state IN ('reserved','dispatching','available','purge_pending','purged')", name="ck_voice_assets_state"),
        sa.CheckConstraint("byte_count IS NULL OR byte_count >= 0", name="ck_voice_assets_bytes"),
        sa.CheckConstraint("sample_rate IS NULL OR sample_rate BETWEEN 8000 AND 192000", name="ck_voice_assets_sample_rate"),
        sa.CheckConstraint("channels IS NULL OR channels BETWEEN 1 AND 8", name="ck_voice_assets_channels"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_voice_assets_duration"),
        sa.CheckConstraint("sha256 IS NULL OR length(sha256) = 64", name="ck_voice_assets_digest"),
        sa.CheckConstraint("state <> 'available' OR (sha256 IS NOT NULL AND byte_count IS NOT NULL AND mime_type IS NOT NULL)", name="ck_voice_assets_available"),
        sa.CheckConstraint("absence_checks >= 0", name="ck_voice_assets_absence"),
    )
    op.create_index("ix_voice_assets_cleanup", "voice_assets", ["state", "writer_deadline", "created_at"])

    if not sqlite:
        op.create_foreign_key("fk_voice_profiles_current_scope", "voice_profiles", "voice_profile_versions",
            ["legacy_id", "id", "current_version_id"], ["legacy_id", "voice_profile_id", "id"], ondelete="RESTRICT")
        op.create_foreign_key("fk_voice_profiles_desired_scope", "voice_profiles", "voice_profile_versions",
            ["legacy_id", "id", "desired_version_id"], ["legacy_id", "voice_profile_id", "id"], ondelete="RESTRICT")
        op.create_foreign_key("fk_voice_versions_reference_asset_scope", "voice_profile_versions", "voice_assets",
            ["legacy_id", "voice_profile_id", "id", "reference_asset_id"],
            ["legacy_id", "voice_profile_id", "version_id", "id"], ondelete="RESTRICT")

    _guards(sqlite)


def _guards(sqlite):
    if sqlite:
        op.execute(sa.text("""
CREATE TRIGGER trg_l21_voice_profile_ready_insert BEFORE INSERT ON voice_profiles
WHEN NEW.status = 'active' AND NOT EXISTS (
  SELECT 1 FROM voice_profile_versions v WHERE v.legacy_id=NEW.legacy_id
  AND v.voice_profile_id=NEW.id AND v.id=NEW.current_version_id AND v.status='ready')
BEGIN SELECT RAISE(ABORT, 'voice_active_version_not_ready'); END
"""))
        op.execute(sa.text("""
CREATE TRIGGER trg_l21_voice_profile_ready_update BEFORE UPDATE ON voice_profiles
WHEN NEW.status = 'active' AND NOT EXISTS (
  SELECT 1 FROM voice_profile_versions v WHERE v.legacy_id=NEW.legacy_id
  AND v.voice_profile_id=NEW.id AND v.id=NEW.current_version_id AND v.status='ready')
BEGIN SELECT RAISE(ABORT, 'voice_active_version_not_ready'); END
"""))
        op.execute(sa.text("""
CREATE TRIGGER trg_l21_voice_version_selected BEFORE UPDATE OF status ON voice_profile_versions
WHEN NEW.status <> 'ready' AND EXISTS (
  SELECT 1 FROM voice_profiles p WHERE p.legacy_id=OLD.legacy_id
  AND p.id=OLD.voice_profile_id AND p.current_version_id=OLD.id AND p.status='active')
BEGIN SELECT RAISE(ABORT, 'voice_selected_version_must_remain_ready'); END
"""))
        op.execute(sa.text("""
CREATE TRIGGER trg_l21_voice_consent_immutable BEFORE UPDATE ON voice_consent_receipts
WHEN NOT (
  NEW.id IS OLD.id AND NEW.legacy_id IS OLD.legacy_id AND NEW.voice_profile_id IS OLD.voice_profile_id
  AND NEW.actor_user_id IS OLD.actor_user_id AND NEW.copy_version IS OLD.copy_version
  AND NEW.policy_version IS OLD.policy_version AND NEW.authority_basis IS OLD.authority_basis
  AND NEW.source_category IS OLD.source_category AND NEW.presented_copy_digest IS OLD.presented_copy_digest
  AND NEW.accepted_at IS OLD.accepted_at AND OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'voice_consent_immutable'); END
"""))
        op.execute(sa.text("""
CREATE TRIGGER trg_l21_voice_consent_retain BEFORE DELETE ON voice_consent_receipts
WHEN EXISTS (SELECT 1 FROM legacies l WHERE l.id=OLD.legacy_id AND l.deletion_requested_at IS NULL)
BEGIN SELECT RAISE(ABORT, 'voice_consent_delete_requires_legacy_erasure'); END
"""))
        return
    op.execute(sa.text("""
CREATE FUNCTION l21_voice_profile_ready_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'active' AND NOT EXISTS (
    SELECT 1 FROM voice_profile_versions v WHERE v.legacy_id=NEW.legacy_id
    AND v.voice_profile_id=NEW.id AND v.id=NEW.current_version_id AND v.status='ready')
  THEN RAISE EXCEPTION 'voice_active_version_not_ready'; END IF;
  RETURN NEW;
END $$
"""))
    op.execute(sa.text("CREATE TRIGGER trg_l21_voice_profile_ready BEFORE INSERT OR UPDATE ON voice_profiles FOR EACH ROW EXECUTE FUNCTION l21_voice_profile_ready_guard()"))
    op.execute(sa.text("""
CREATE FUNCTION l21_voice_version_selected_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status <> 'ready' AND EXISTS (
    SELECT 1 FROM voice_profiles p WHERE p.legacy_id=OLD.legacy_id
    AND p.id=OLD.voice_profile_id AND p.current_version_id=OLD.id AND p.status='active')
  THEN RAISE EXCEPTION 'voice_selected_version_must_remain_ready'; END IF;
  RETURN NEW;
END $$
"""))
    op.execute(sa.text("CREATE TRIGGER trg_l21_voice_version_selected BEFORE UPDATE OF status ON voice_profile_versions FOR EACH ROW EXECUTE FUNCTION l21_voice_version_selected_guard()"))
    op.execute(sa.text("""
CREATE FUNCTION l21_voice_consent_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF EXISTS (SELECT 1 FROM legacies l WHERE l.id=OLD.legacy_id AND l.deletion_requested_at IS NULL)
    THEN RAISE EXCEPTION 'voice_consent_delete_requires_legacy_erasure'; END IF;
    RETURN OLD;
  END IF;
  IF ROW(NEW.id,NEW.legacy_id,NEW.voice_profile_id,NEW.actor_user_id,NEW.copy_version,
    NEW.policy_version,NEW.authority_basis,NEW.source_category,NEW.presented_copy_digest,NEW.accepted_at)
    IS DISTINCT FROM ROW(OLD.id,OLD.legacy_id,OLD.voice_profile_id,OLD.actor_user_id,OLD.copy_version,
    OLD.policy_version,OLD.authority_basis,OLD.source_category,OLD.presented_copy_digest,OLD.accepted_at)
    OR OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL
  THEN RAISE EXCEPTION 'voice_consent_immutable'; END IF;
  RETURN NEW;
END $$
"""))
    op.execute(sa.text("CREATE TRIGGER trg_l21_voice_consent_update BEFORE UPDATE ON voice_consent_receipts FOR EACH ROW EXECUTE FUNCTION l21_voice_consent_guard()"))
    op.execute(sa.text("CREATE TRIGGER trg_l21_voice_consent_delete BEFORE DELETE ON voice_consent_receipts FOR EACH ROW EXECUTE FUNCTION l21_voice_consent_guard()"))


def downgrade():
    sqlite = op.get_bind().dialect.name == "sqlite"
    # Break circular selected/reference pointers before table removal. This is
    # schema acceptance only; it is not permission to skip production erasure.
    op.execute(sa.text("UPDATE voice_profiles SET status='processing', current_version_id=NULL, desired_version_id=NULL WHERE status <> 'deleted'"))
    op.execute(sa.text("UPDATE voice_profile_versions SET status='failed', reference_asset_id=NULL"))
    if sqlite:
        for name in ("trg_l21_voice_consent_retain", "trg_l21_voice_consent_immutable",
                     "trg_l21_voice_version_selected", "trg_l21_voice_profile_ready_update",
                     "trg_l21_voice_profile_ready_insert"):
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS {name}"))
    else:
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_l21_voice_consent_delete ON voice_consent_receipts"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_l21_voice_consent_update ON voice_consent_receipts"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_l21_voice_version_selected ON voice_profile_versions"))
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_l21_voice_profile_ready ON voice_profiles"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS l21_voice_consent_guard()"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS l21_voice_version_selected_guard()"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS l21_voice_profile_ready_guard()"))
        op.drop_constraint("fk_voice_versions_reference_asset_scope", "voice_profile_versions", type_="foreignkey")
        op.drop_constraint("fk_voice_profiles_desired_scope", "voice_profiles", type_="foreignkey")
        op.drop_constraint("fk_voice_profiles_current_scope", "voice_profiles", type_="foreignkey")
    op.drop_table("voice_assets")
    op.drop_table("voice_jobs")
    op.drop_index("ix_voice_versions_profile_state", table_name="voice_profile_versions")
    op.drop_table("voice_profile_versions")
    op.drop_table("voice_consent_receipts")
    op.drop_index("uq_voice_profiles_live_legacy", table_name="voice_profiles")
    op.drop_table("voice_profiles")
