"""Add approved Legacy identity fact projections.

Revision ID: 0010_legacy_identity_facts
Revises: 0009_memory_embeddings
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0010_legacy_identity_facts"
down_revision: str | None = "0009_memory_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legacy_identity_facts",
        sa.Column("identity_fact_id", sa.Integer(), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("fact_type", sa.String(20), nullable=False),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("normalized_value", sa.String(255), nullable=False),
        sa.Column("relationship", sa.String(100), server_default="", nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("uncertainty_note", sa.String(500), nullable=True),
        sa.Column("source_memory_id", sa.Integer(), nullable=False),
        sa.Column("source_provenance_id", sa.Integer(), nullable=False),
        sa.Column("contradiction_group_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(value)) > 0", name="ck_identity_facts_value_not_blank"),
        sa.ForeignKeyConstraint(["legacy_id"], ["legacies.legacy_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_memory_id"], ["memories.memory_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_provenance_id"], ["memory_provenance.provenance_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contradiction_group_id"], ["memory_contradiction_groups.contradiction_group_id"], ondelete="SET NULL"),
        sa.UniqueConstraint("legacy_id", "fact_type", "normalized_value", "relationship", name="uq_identity_facts_legacy_type_value_relationship"),
    )
    op.create_index("ix_identity_facts_legacy_status_type", "legacy_identity_facts", ["legacy_id", "status", "fact_type"])
    for column in ("identity_fact_id", "legacy_id", "fact_type", "source_memory_id", "source_provenance_id", "contradiction_group_id"):
        op.create_index(f"ix_legacy_identity_facts_{column}", "legacy_identity_facts", [column])


def downgrade() -> None:
    op.drop_table("legacy_identity_facts")
