"""LegaRya L4 connected, editable memory intelligence.

Revision ID: 0004_legarya_l4
Revises: 0003_legarya_l3
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_legarya_l4"
down_revision: str | None = "0003_legarya_l3"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memories") as batch:
        batch.drop_constraint("uq_memories_legacy_fingerprint", type_="unique")
        batch.add_column(sa.Column("operation_type", sa.String(32), server_default="new", nullable=False))
        batch.add_column(sa.Column("explicit_save", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("superseded_by_memory_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("story_key", sa.String(120), nullable=True))
        batch.create_foreign_key("fk_memories_superseded_by", "memories", ["superseded_by_memory_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_memories_superseded_by_memory_id", ["superseded_by_memory_id"])
        batch.create_index("ix_memories_story_key", ["story_key"])
        batch.create_index("ix_memories_legacy_fingerprint", ["legacy_id", "normalized_fingerprint"])

    op.create_table(
        "memory_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("memory_id", sa.Integer(), sa.ForeignKey("memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("previous_text", sa.Text(), nullable=True),
        sa.Column("new_text", sa.Text(), nullable=True),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("changed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_memory_revisions_memory_id", "memory_revisions", ["memory_id"])
    op.create_index("ix_memory_revisions_changed_by_user_id", "memory_revisions", ["changed_by_user_id"])
    op.create_index("ix_memory_revisions_memory_changed", "memory_revisions", ["memory_id", "changed_at"])

    op.create_table(
        "memory_entities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("legacy_id", "normalized_name", name="uq_memory_entities_legacy_name"),
    )
    op.create_index("ix_memory_entities_legacy_id", "memory_entities", ["legacy_id"])
    op.create_index("ix_memory_entities_legacy_type", "memory_entities", ["legacy_id", "entity_type"])

    op.create_table(
        "memory_entity_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("memory_id", sa.Integer(), sa.ForeignKey("memories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("memory_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(80), server_default="mentioned", nullable=False),
        sa.UniqueConstraint("memory_id", "entity_id", "role", name="uq_memory_entity_links_role"),
    )
    op.create_index("ix_memory_entity_links_memory_id", "memory_entity_links", ["memory_id"])
    op.create_index("ix_memory_entity_links_entity_id", "memory_entity_links", ["entity_id"])


def downgrade() -> None:
    op.drop_table("memory_entity_links")
    op.drop_table("memory_entities")
    op.drop_table("memory_revisions")
    with op.batch_alter_table("memories") as batch:
        batch.drop_index("ix_memories_legacy_fingerprint")
        batch.drop_index("ix_memories_story_key")
        batch.drop_index("ix_memories_superseded_by_memory_id")
        batch.drop_constraint("fk_memories_superseded_by", type_="foreignkey")
        batch.drop_column("story_key")
        batch.drop_column("superseded_by_memory_id")
        batch.drop_column("explicit_save")
        batch.drop_column("operation_type")
        batch.create_unique_constraint("uq_memories_legacy_fingerprint", ["legacy_id", "normalized_fingerprint"])
