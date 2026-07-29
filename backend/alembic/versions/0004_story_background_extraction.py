"""Persist frontend legacy correlation and Story extraction runs.

Revision ID: 0004_story_background
Revises: 0003_memory_pipeline
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_story_background"
down_revision: str | None = "0003_memory_pipeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("legacies") as batch:
        batch.add_column(
            sa.Column("client_correlation_id", sa.String(100), nullable=True)
        )
        batch.create_unique_constraint(
            "uq_legacies_owner_client_correlation",
            ["owner_user_id", "client_correlation_id"],
        )
    with op.batch_alter_table("story_messages") as batch:
        batch.add_column(
            sa.Column("client_message_id", sa.String(120), nullable=True)
        )
        batch.create_unique_constraint(
            "uq_story_messages_session_client_message",
            ["story_session_id", "client_message_id"],
        )
    op.create_table(
        "memory_extraction_runs",
        sa.Column("extraction_run_id", sa.Integer(), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("story_session_id", sa.Integer(), nullable=False),
        sa.Column("message_boundary", sa.Integer(), nullable=False),
        sa.Column("trigger_type", sa.String(40), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "running", "completed", "failed",
                name="memory_extraction_run_status",
                native_enum=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=True),
        sa.Column("memories_created", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("message_boundary > 0", name="ck_extraction_runs_boundary_positive"),
        sa.ForeignKeyConstraint(["legacy_id"], ["legacies.legacy_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_session_id"], ["story_sessions.story_session_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("story_session_id", "message_boundary", "trigger_type", name="uq_extraction_runs_session_boundary_trigger"),
    )
    op.create_index("ix_extraction_runs_legacy_status", "memory_extraction_runs", ["legacy_id", "status"])
    op.create_index("ix_memory_extraction_runs_legacy_id", "memory_extraction_runs", ["legacy_id"])
    op.create_index("ix_memory_extraction_runs_story_session_id", "memory_extraction_runs", ["story_session_id"])


def downgrade() -> None:
    op.drop_table("memory_extraction_runs")
    with op.batch_alter_table("story_messages") as batch:
        batch.drop_constraint("uq_story_messages_session_client_message", type_="unique")
        batch.drop_column("client_message_id")
    with op.batch_alter_table("legacies") as batch:
        batch.drop_constraint("uq_legacies_owner_client_correlation", type_="unique")
        batch.drop_column("client_correlation_id")
