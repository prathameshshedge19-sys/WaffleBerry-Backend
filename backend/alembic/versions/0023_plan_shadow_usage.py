"""Add isolated shadow plan accounting; never updates existing customer content."""
from alembic import op
import sqlalchemy as sa

revision = "0023_plan_shadow_usage"
down_revision = "0022_legacy_deletion"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("plan_entitlements",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("plan", sa.String(8), nullable=False, server_default="free"),
        sa.Column("quota_exempt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("plan IN ('free','plus','pro')", name="ck_entitlement_plan"))
    op.create_table("plan_usage",
        sa.Column("operation_key", sa.String(160), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feature", sa.String(40), nullable=False),
        sa.Column("usage_day", sa.Date(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("reserved", sa.BigInteger(), nullable=False),
        sa.Column("released", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("plan_version", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount >= 0 AND reserved >= 0 AND released >= 0", name="ck_plan_usage_amounts"),
        sa.CheckConstraint("state IN ('pending','completed','failed','interrupted')", name="ck_plan_usage_state"))
    op.create_index("ix_plan_usage_user_day_feature", "plan_usage", ["user_id", "usage_day", "feature"])
    op.create_table("plan_voice_intervals",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("generation", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feature", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("uncertain_tail", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("generation >= 0", name="ck_plan_voice_generation"),
        sa.CheckConstraint("observed_until >= started_at", name="ck_plan_voice_order"))
    op.create_index("ix_plan_voice_user_started", "plan_voice_intervals", ["user_id", "started_at"])
    op.create_table("plan_tracking_state",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("cursor_turn_id", sa.Integer(), nullable=False, server_default="0"))
    # No historical text usage is retroactively charged at deployment.
    op.execute(sa.text("INSERT INTO plan_tracking_state (name, started_at, cursor_turn_id) VALUES ('shadow', CURRENT_TIMESTAMP, 0)"))


def downgrade():
    # Operational rollback disables tracking, never discards accounting history.
    raise RuntimeError("Disable shadow tracking to roll back; preserve plan accounting tables")
