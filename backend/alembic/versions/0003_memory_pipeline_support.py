"""Add deterministic idempotency and durable memory relationships.

Revision ID: 0003_memory_pipeline
Revises: 0002_memory_engine
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_memory_pipeline"
down_revision: str | None = "0002_memory_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable fingerprints and enrichment relationship records."""
    with op.batch_alter_table("memories") as batch_op:
        batch_op.add_column(
            sa.Column(
                "normalized_fingerprint",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch_op.create_unique_constraint(
            "uq_memories_legacy_fingerprint",
            ["legacy_id", "normalized_fingerprint"],
        )

    op.create_table(
        "memory_links",
        sa.Column("memory_link_id", sa.Integer(), nullable=False),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("source_memory_id", sa.Integer(), nullable=False),
        sa.Column("target_memory_id", sa.Integer(), nullable=False),
        sa.Column("link_type", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_memory_id <> target_memory_id",
            name="ck_memory_links_not_self_linked",
        ),
        sa.ForeignKeyConstraint(
            ["legacy_id"],
            ["legacies.legacy_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_memory_id"],
            ["memories.memory_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_memory_id"],
            ["memories.memory_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("memory_link_id"),
        sa.UniqueConstraint(
            "source_memory_id",
            "target_memory_id",
            "link_type",
            name="uq_memory_links_source_target_type",
        ),
    )
    op.create_index(
        "ix_memory_links_memory_link_id",
        "memory_links",
        ["memory_link_id"],
    )
    op.create_index(
        "ix_memory_links_legacy_id",
        "memory_links",
        ["legacy_id"],
    )
    op.create_index(
        "ix_memory_links_source_memory_id",
        "memory_links",
        ["source_memory_id"],
    )
    op.create_index(
        "ix_memory_links_target_memory_id",
        "memory_links",
        ["target_memory_id"],
    )
    op.create_index(
        "ix_memory_links_legacy_type",
        "memory_links",
        ["legacy_id", "link_type"],
    )


def downgrade() -> None:
    """Remove pipeline relationship and fingerprint support."""
    op.drop_table("memory_links")
    with op.batch_alter_table("memories") as batch_op:
        batch_op.drop_constraint(
            "uq_memories_legacy_fingerprint",
            type_="unique",
        )
        batch_op.drop_column("normalized_fingerprint")
