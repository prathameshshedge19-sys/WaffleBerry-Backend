"""Add the isolated L13 derived personality projection and durable rebuild queue."""

from alembic import op
import sqlalchemy as sa

revision = "0014_legacy_personality"
down_revision = "0013_voice_preference"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "legacy_personality_profiles",
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("built_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("profile_json", sa.JSON(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("policy_version", sa.String(64), nullable=False, server_default="l13-conservative-v1"),
        sa.Column("builder_id", sa.String(64), nullable=False, server_default="deterministic-evidence-v1"),
        sa.Column("build_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("built_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("source_generation >= 1 AND built_generation >= 0 AND built_generation <= source_generation", name="ck_personality_generations"),
        sa.CheckConstraint("build_status IN ('pending', 'building', 'ready', 'failed')", name="ck_personality_build_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_personality_attempts"),
    )
    op.create_index("ix_legacy_personality_profiles_build_status", "legacy_personality_profiles", ["build_status"])


def downgrade():
    op.drop_index("ix_legacy_personality_profiles_build_status", table_name="legacy_personality_profiles")
    op.drop_table("legacy_personality_profiles")
