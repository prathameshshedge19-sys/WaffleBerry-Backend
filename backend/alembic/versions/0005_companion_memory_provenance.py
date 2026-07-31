"""Track internal Companion grounding provenance.

Revision ID: 0005_companion_provenance
Revises: 0004_story_background
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_companion_provenance"
down_revision: str | None = "0004_story_background"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companion_memory_provenance",
        sa.Column("assistant_message_id", sa.Integer(), nullable=False),
        sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("retrieval_order", sa.Integer(), nullable=False),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "retrieval_order >= 0",
            name="ck_companion_provenance_order_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["messages.message_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memories.memory_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("assistant_message_id", "memory_id"),
        sa.UniqueConstraint(
            "assistant_message_id",
            "retrieval_order",
            name="uq_companion_provenance_message_order",
        ),
    )
    op.create_index(
        "ix_companion_memory_provenance_memory_id",
        "companion_memory_provenance",
        ["memory_id"],
    )


def downgrade() -> None:
    op.drop_table("companion_memory_provenance")
