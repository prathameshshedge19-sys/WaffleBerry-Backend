"""Add Phase 11.1 quota foundation.

Revision ID: 0015_quota_foundation
Revises: 0014_message_source_idempotency
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_quota_foundation"
down_revision = "0014_message_source_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("plan", sa.String(length=5), nullable=False, server_default="free"))
        batch_op.add_column(
            sa.Column("quota_exempt", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("timezone", sa.String(length=255), nullable=False, server_default="UTC")
        )
        batch_op.create_check_constraint(
            "plan_tier", "plan IN ('free', 'plus', 'pro')"
        )

    op.create_table(
        "user_daily_usage",
        sa.Column("usage_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("chat_turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("live_call_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("voice_plays", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("chat_turns >= 0", name="ck_daily_usage_chat_nonnegative"),
        sa.CheckConstraint("live_call_seconds >= 0", name="ck_daily_usage_live_nonnegative"),
        sa.CheckConstraint("voice_plays >= 0", name="ck_daily_usage_voice_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("usage_id"),
        sa.UniqueConstraint("user_id", "usage_date", name="uq_user_daily_usage_user_date"),
    )
    op.create_index("ix_user_daily_usage_user_id", "user_daily_usage", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_daily_usage_user_id", table_name="user_daily_usage")
    op.drop_table("user_daily_usage")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("plan_tier", type_="check")
        batch_op.drop_column("timezone")
        batch_op.drop_column("quota_exempt")
        batch_op.drop_column("plan")
