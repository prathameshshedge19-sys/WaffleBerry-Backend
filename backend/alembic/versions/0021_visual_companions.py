"""Add L19 presentation aggregates and L16 presentation-only intake purpose.

This revision deliberately owns its DDL, independently of application models.
Downgrade removes visual metadata; it is not a storage-erasure workflow or a
production rollback strategy. Registered objects must be purged beforehand.
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_visual_companions"
down_revision = "0020_legacy_stories"
branch_labels = None
depends_on = None


def upgrade():
    sqlite = op.get_bind().dialect.name == "sqlite"
    # SQLite supports column-local CHECKs on ADD COLUMN, avoiding reconstruction
    # of the heavily referenced L16 table while foreign keys remain enabled.
    purpose_checks = (
        sa.CheckConstraint("processing_purpose IN ('source_review','visual_reference')", name="ck_media_sources_purpose"),
        sa.CheckConstraint("processing_purpose != 'visual_reference' OR kind = 'image'", name="ck_media_sources_visual_image"),
    )
    op.add_column(
        "media_sources",
        sa.Column("processing_purpose", sa.String(24), *(purpose_checks if sqlite else ()), nullable=False, server_default="source_review"),
    )
    if not sqlite:
        op.create_check_constraint("ck_media_sources_purpose", "media_sources", "processing_purpose IN ('source_review','visual_reference')")
        op.create_check_constraint("ck_media_sources_visual_image", "media_sources", "processing_purpose != 'visual_reference' OR kind = 'image'")

    # SQLite permits inline references to a table created later. PostgreSQL
    # requires adding the two circular pointers after both tables exist.
    inline_pointers = (
        sa.ForeignKeyConstraint(
            ["legacy_id", "id", "current_version_id"],
            ["visual_companion_versions.legacy_id", "visual_companion_versions.companion_id", "visual_companion_versions.id"],
            name="fk_visual_companions_current_scope", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["legacy_id", "id", "desired_version_id"],
            ["visual_companion_versions.legacy_id", "visual_companion_versions.companion_id", "visual_companion_versions.id"],
            name="fk_visual_companions_desired_scope", ondelete="RESTRICT",
        ),
    ) if sqlite else ()
    op.create_table(
        "visual_companions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_version_id", sa.String(36)),
        sa.Column("desired_version_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["legacy_id"], ["legacies.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("legacy_id", name="uq_visual_companions_legacy"),
        sa.UniqueConstraint("legacy_id", "id", name="uq_visual_companions_scope"),
        sa.CheckConstraint("revision >= 1", name="ck_visual_companions_revision"),
        sa.CheckConstraint("NOT enabled OR (deleted_at IS NULL AND current_version_id IS NOT NULL)", name="ck_visual_companions_enabled"),
        sa.CheckConstraint("deleted_at IS NULL OR (NOT enabled AND current_version_id IS NULL AND desired_version_id IS NULL)", name="ck_visual_companions_deleted"),
        *inline_pointers,
    )
    op.create_table(
        "visual_companion_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("companion_id", sa.String(36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("source_generation", sa.Integer(), nullable=False),
        sa.Column("source_artifact_id", sa.String(36), nullable=False),
        sa.Column("source_artifact_generation", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("crop_json", sa.JSON(none_as_null=True)),
        sa.Column("crop_digest", sa.String(64), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmation_copy_version", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("recipe_version", sa.String(64), nullable=False),
        sa.Column("provider_name", sa.String(120), nullable=False),
        sa.Column("model_digest", sa.String(64), nullable=False),
        sa.Column("recipe_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("bundle_digest", sa.String(64)),
        sa.Column("approved_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(64)),
        sa.UniqueConstraint("legacy_id", "companion_id", "id", name="uq_visual_versions_scope"),
        sa.UniqueConstraint("companion_id", "version_number", name="uq_visual_versions_number"),
        sa.UniqueConstraint("companion_id", "request_key", name="uq_visual_versions_request"),
        sa.ForeignKeyConstraint(["legacy_id", "companion_id"], ["visual_companions.legacy_id", "visual_companions.id"], name="fk_visual_versions_companion_scope", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["legacy_id", "source_id"], ["media_sources.legacy_id", "media_sources.id"], name="fk_visual_versions_source_scope", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["legacy_id", "source_id", "source_artifact_generation", "source_artifact_id"],
            ["media_artifacts.legacy_id", "media_artifacts.source_id", "media_artifacts.generation", "media_artifacts.id"],
            name="fk_visual_versions_artifact_scope", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_visual_versions_number"),
        sa.CheckConstraint("source_generation >= 1 AND source_artifact_generation >= 1", name="ck_visual_versions_generations"),
        sa.CheckConstraint("state IN ('queued','preparing','ready','failed','cancelled','purge_pending','purged')", name="ck_visual_versions_state"),
        sa.CheckConstraint("(approved_by_user_id IS NULL AND approved_at IS NULL) OR (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)", name="ck_visual_versions_approval_pair"),
        sa.CheckConstraint("state <> 'ready' OR bundle_digest IS NOT NULL", name="ck_visual_versions_ready_bundle"),
        sa.CheckConstraint("length(trim(request_key)) > 0 AND length(request_key) <= 128", name="ck_visual_versions_request_key"),
    )
    if not sqlite:
        op.create_foreign_key(
            "fk_visual_companions_current_scope", "visual_companions", "visual_companion_versions",
            ["legacy_id", "id", "current_version_id"], ["legacy_id", "companion_id", "id"], ondelete="RESTRICT",
        )
        op.create_foreign_key(
            "fk_visual_companions_desired_scope", "visual_companions", "visual_companion_versions",
            ["legacy_id", "id", "desired_version_id"], ["legacy_id", "companion_id", "id"], ondelete="RESTRICT",
        )
    op.create_index("ix_visual_versions_source", "visual_companion_versions", ["legacy_id", "source_id"])
    op.create_index("ix_visual_versions_owner_admission", "visual_companion_versions", ["confirmed_by_user_id", "created_at"])
    op.create_index("ix_visual_versions_legacy_admission", "visual_companion_versions", ["legacy_id", "created_at"])
    op.create_index("ix_visual_versions_expiry", "visual_companion_versions", ["state", "expires_at"])

    op.create_table(
        "visual_companion_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("companion_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("logical_role", sa.String(24), nullable=False),
        sa.Column("storage_backend", sa.String(32), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("object_version", sa.String(255)),
        sa.Column("storage_bucket", sa.String(255)),
        sa.Column("write_state", sa.String(16), nullable=False, server_default="reserved"),
        sa.Column("write_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("absent_since", sa.DateTime(timezone=True)),
        sa.Column("absence_checks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("encryption_key_id", sa.String(128)),
        sa.Column("sha256", sa.String(64)),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("mime_type", sa.String(127)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("state", sa.String(16), nullable=False, server_default="reserved"),
        sa.Column("writer_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("storage_backend", "object_key", name="uq_visual_assets_storage_key"),
        sa.UniqueConstraint("version_id", "attempt_id", "logical_role", name="uq_visual_assets_attempt_role"),
        sa.ForeignKeyConstraint(
            ["legacy_id", "companion_id", "version_id"],
            ["visual_companion_versions.legacy_id", "visual_companion_versions.companion_id", "visual_companion_versions.id"],
            name="fk_visual_assets_version_scope", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("logical_role IN ('poster','texture_atlas','rig')", name="ck_visual_assets_role"),
        sa.CheckConstraint("state IN ('reserved','available','purge_pending','purged')", name="ck_visual_assets_state"),
        sa.CheckConstraint("write_state IN ('reserved','dispatching','confirmed')", name="ck_visual_assets_write_state"),
        sa.CheckConstraint("absence_checks >= 0", name="ck_visual_assets_absence_checks"),
        sa.CheckConstraint("byte_size IS NULL OR (byte_size >= 0 AND byte_size <= 2097152)", name="ck_visual_assets_size"),
        sa.CheckConstraint("(width IS NULL AND height IS NULL) OR (width IS NOT NULL AND height IS NOT NULL AND width BETWEEN 1 AND 1024 AND height BETWEEN 1 AND 1024)", name="ck_visual_assets_dimensions"),
        sa.CheckConstraint("state <> 'available' OR (sha256 IS NOT NULL AND byte_size IS NOT NULL AND byte_size > 0 AND mime_type IS NOT NULL)", name="ck_visual_assets_available_metadata"),
    )
    op.create_index(
        "uq_visual_assets_available_role", "visual_companion_assets", ["version_id", "logical_role"], unique=True,
        postgresql_where=sa.text("state = 'available'"), sqlite_where=sa.text("state = 'available'"),
    )
    op.create_index("ix_visual_assets_version_scope", "visual_companion_assets", ["legacy_id", "companion_id", "version_id"])
    op.create_index("ix_visual_assets_cleanup", "visual_companion_assets", ["state", "writer_deadline", "created_at"])

    op.create_table(
        "visual_generation_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("companion_id", sa.String(36), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("writer_deadline", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("version_id", "kind", name="uq_visual_jobs_version_kind"),
        sa.ForeignKeyConstraint(
            ["legacy_id", "companion_id", "version_id"],
            ["visual_companion_versions.legacy_id", "visual_companion_versions.companion_id", "visual_companion_versions.id"],
            name="fk_visual_jobs_version_scope", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("kind IN ('prepare','purge')", name="ck_visual_jobs_kind"),
        sa.CheckConstraint("state IN ('queued','running','retry_wait','succeeded','failed','cancelled')", name="ck_visual_jobs_state"),
        sa.CheckConstraint("attempts >= 0 AND (kind = 'purge' OR attempts <= 2)", name="ck_visual_jobs_attempts"),
        sa.CheckConstraint("(lease_token IS NULL AND lease_expires_at IS NULL) OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)", name="ck_visual_jobs_lease_pair"),
    )
    op.create_index("ix_visual_jobs_claim", "visual_generation_jobs", ["kind", "state", "next_attempt_at", "lease_expires_at"])
    op.create_index("ix_visual_jobs_version_scope", "visual_generation_jobs", ["legacy_id", "companion_id", "version_id"])
    _create_purpose_guards(sqlite)


def _create_purpose_guards(sqlite):
    if sqlite:
        op.execute(sa.text("""
            CREATE TRIGGER trg_l19_source_purpose_immutable
            BEFORE UPDATE OF processing_purpose ON media_sources
            WHEN NEW.processing_purpose IS NOT OLD.processing_purpose
            BEGIN SELECT RAISE(ABORT, 'processing_purpose_immutable'); END
        """))
        for table in ("source_evidence", "source_memory_candidates", "source_candidate_evidence", "memory_source_links"):
            for operation in ("INSERT", "UPDATE"):
                op.execute(sa.text(f"""
                    CREATE TRIGGER trg_l19_{table}_{operation.lower()}
                    BEFORE {operation} ON {table}
                    WHEN EXISTS (SELECT 1 FROM media_sources
                        WHERE legacy_id = NEW.legacy_id AND id = NEW.source_id
                        AND processing_purpose = 'visual_reference')
                    BEGIN SELECT RAISE(ABORT, 'visual_reference_has_no_factual_support'); END
                """))
    else:
        op.execute(sa.text("""
            CREATE FUNCTION l19_source_purpose_immutable() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.processing_purpose IS DISTINCT FROM OLD.processing_purpose THEN
                    RAISE EXCEPTION 'processing_purpose_immutable' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END; $$
        """))
        op.execute(sa.text("""
            CREATE TRIGGER trg_l19_source_purpose_immutable
            BEFORE UPDATE OF processing_purpose ON media_sources
            FOR EACH ROW EXECUTE FUNCTION l19_source_purpose_immutable()
        """))
        # Bind lookup to the triggering table's schema, not the caller's search
        # path; the same scoped source columns exist on all four L16 tables.
        op.execute(sa.text("""
            CREATE FUNCTION l19_reject_visual_factual_support() RETURNS trigger
            LANGUAGE plpgsql AS $$
            DECLARE visual_source boolean;
            BEGIN
                EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.media_sources
                    WHERE legacy_id = $1 AND id = $2
                    AND processing_purpose = ''visual_reference'')', TG_TABLE_SCHEMA)
                    INTO visual_source USING NEW.legacy_id, NEW.source_id;
                IF visual_source THEN
                    RAISE EXCEPTION 'visual_reference_has_no_factual_support' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END; $$
        """))
        for table in ("source_evidence", "source_memory_candidates", "source_candidate_evidence", "memory_source_links"):
            op.execute(sa.text(f"""
                CREATE TRIGGER trg_l19_{table}_factual_guard
                BEFORE INSERT OR UPDATE ON {table}
                FOR EACH ROW EXECUTE FUNCTION l19_reject_visual_factual_support()
            """))


def _drop_purpose_guards(sqlite):
    if sqlite:
        op.execute(sa.text("DROP TRIGGER trg_l19_source_purpose_immutable"))
        for table in ("source_evidence", "source_memory_candidates", "source_candidate_evidence", "memory_source_links"):
            for operation in ("insert", "update"):
                op.execute(sa.text(f"DROP TRIGGER trg_l19_{table}_{operation}"))
    else:
        op.execute(sa.text("DROP TRIGGER trg_l19_source_purpose_immutable ON media_sources"))
        for table in ("source_evidence", "source_memory_candidates", "source_candidate_evidence", "memory_source_links"):
            op.execute(sa.text(f"DROP TRIGGER trg_l19_{table}_factual_guard ON {table}"))
        op.execute(sa.text("DROP FUNCTION l19_reject_visual_factual_support()"))
        op.execute(sa.text("DROP FUNCTION l19_source_purpose_immutable()"))


def downgrade():
    sqlite = op.get_bind().dialect.name == "sqlite"
    _drop_purpose_guards(sqlite)
    if sqlite:
        # Clear only L19 pointers before dropping the circular tables, preserving
        # foreign-key enforcement throughout the downgrade, including with rows.
        op.execute(sa.text("UPDATE visual_companions SET enabled = false, current_version_id = NULL, desired_version_id = NULL"))
    else:
        op.drop_constraint("fk_visual_companions_current_scope", "visual_companions", type_="foreignkey")
        op.drop_constraint("fk_visual_companions_desired_scope", "visual_companions", type_="foreignkey")
    op.drop_table("visual_generation_jobs")
    op.drop_table("visual_companion_assets")
    if sqlite:
        # Empty the child while its parent still exists; SQLite's DROP TABLE
        # performs an implicit DELETE and otherwise sees the missing parent.
        op.execute(sa.text("DELETE FROM visual_companion_versions"))
        op.execute(sa.text("DELETE FROM visual_companions"))
    op.drop_table("visual_companion_versions")
    op.drop_table("visual_companions")
    if not sqlite:
        op.drop_constraint("ck_media_sources_visual_image", "media_sources", type_="check")
        op.drop_constraint("ck_media_sources_purpose", "media_sources", type_="check")
    op.drop_column("media_sources", "processing_purpose")
