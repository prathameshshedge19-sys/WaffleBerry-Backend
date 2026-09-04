"""LegaRya L3 multilingual living memory.

Revision ID: 0003_legarya_l3
Revises: 0002_legarya_l2
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_legarya_l3"
down_revision: str | None = "0002_legarya_l2"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("subject_reference", sa.String(255), nullable=True),
        sa.Column("source_conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_language", sa.String(80), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column("normalized_fingerprint", sa.String(64), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(120), nullable=True),
        sa.Column("embedding_version", sa.String(40), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(canonical_text)) > 0", name="ck_memories_canonical_text_not_blank"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memories_confidence_range"),
        sa.CheckConstraint("status IN ('active', 'superseded', 'deleted')", name="ck_memories_status"),
        sa.UniqueConstraint("legacy_id", "normalized_fingerprint", name="uq_memories_legacy_fingerprint"),
    )
    op.create_index("ix_memories_legacy_id", "memories", ["legacy_id"])
    op.create_index("ix_memories_source_conversation_id", "memories", ["source_conversation_id"])
    op.create_index("ix_memories_source_message_id", "memories", ["source_message_id"])
    op.create_index("ix_memories_legacy_status", "memories", ["legacy_id", "status"])
    op.create_index("ix_memories_legacy_category", "memories", ["legacy_id", "category"])


def downgrade() -> None:
    op.drop_table("memories")
