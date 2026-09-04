"""LegaRya L8 daily habit and progression.

Revision ID: 0007_legarya_l8
Revises: 0006_legarya_l6
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007_legarya_l8"
down_revision: str | None = "0006_legarya_l6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "builder_activities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_type", sa.String(32), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("memory_id", sa.Integer(), sa.ForeignKey("memories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_builder_activities_user_id", "builder_activities", ["user_id"])
    op.create_index("ix_builder_activities_legacy_id", "builder_activities", ["legacy_id"])
    op.create_index("ix_builder_activities_memory_id", "builder_activities", ["memory_id"])
    op.create_index("ix_builder_activities_user_date", "builder_activities", ["user_id", "activity_date"])
    op.create_index("ix_builder_activities_legacy_date", "builder_activities", ["legacy_id", "activity_date"])

    op.create_table(
        "daily_prompts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("shown_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("answered_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'answered', 'skipped')", name="ck_daily_prompts_status"),
    )
    op.create_index("ix_daily_prompts_legacy_id", "daily_prompts", ["legacy_id"])
    op.create_index("ix_daily_prompts_answered_by_user_id", "daily_prompts", ["answered_by_user_id"])
    op.create_index("ix_daily_prompts_legacy_date", "daily_prompts", ["legacy_id", "shown_date"])
    op.create_table(
        "message_web_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("publication_date", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("message_id", "url", name="uq_message_web_sources_url"),
    )
    op.create_index("ix_message_web_sources_message", "message_web_sources", ["message_id"])


def downgrade() -> None:
    op.drop_table("message_web_sources")
    op.drop_table("daily_prompts")
    op.drop_table("builder_activities")
