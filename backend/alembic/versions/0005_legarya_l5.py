"""LegaRya L5 collaboration and contributor provenance.

Revision ID: 0005_legarya_l5
Revises: 0004_legarya_l4
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_legarya_l5"
down_revision: str | None = "0004_legarya_l4"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    legacy_columns = {column["name"] for column in inspector.get_columns("legacies")}
    legacy_additions = (
        sa.Column("collaborator_code_digest", sa.String(64), nullable=True),
        sa.Column("collaborator_code_ciphertext", sa.Text(), nullable=True),
        sa.Column("collaborator_code_hint", sa.String(20), nullable=True),
        sa.Column("collaborator_code_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("collaborator_code_rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in legacy_additions:
        if column.name not in legacy_columns:
            op.add_column("legacies", column)
    if "ix_legacies_collaborator_code_digest" not in {index["name"] for index in inspector.get_indexes("legacies")}:
        op.create_index("ix_legacies_collaborator_code_digest", "legacies", ["collaborator_code_digest"], unique=True)

    if "legacy_collaborators" not in inspector.get_table_names():
        op.create_table(
        "legacy_collaborators",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(24), server_default="collaborator", nullable=False),
        sa.Column("status", sa.String(24), server_default="active", nullable=False),
        sa.Column("added_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("legacy_id", "user_id", name="uq_legacy_collaborators_legacy_user"),
        sa.CheckConstraint("role = 'collaborator'", name="ck_legacy_collaborators_role"),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_legacy_collaborators_status"),
        )
        op.create_index("ix_legacy_collaborators_legacy_id", "legacy_collaborators", ["legacy_id"])
        op.create_index("ix_legacy_collaborators_user_id", "legacy_collaborators", ["user_id"])
        op.create_index("ix_legacy_collaborators_added_by_user_id", "legacy_collaborators", ["added_by_user_id"])
        op.create_index("ix_legacy_collaborators_user_status", "legacy_collaborators", ["user_id", "status"])

    conversation_columns = {column["name"] for column in sa.inspect(bind).get_columns("conversations")}
    if "mode" not in conversation_columns:
        op.add_column("conversations", sa.Column("mode", sa.String(24), server_default="rya", nullable=False))
    if bind.dialect.name != "sqlite" and "ck_conversations_mode" not in {item["name"] for item in sa.inspect(bind).get_check_constraints("conversations")}:
        op.create_check_constraint("ck_conversations_mode", "conversations", "mode IN ('rya')")

    memory_inspector = sa.inspect(bind)
    memory_columns = {column["name"] for column in memory_inspector.get_columns("memories")}
    if bind.dialect.name == "sqlite":
        if "contributor_user_id" not in memory_columns:
            op.execute(sa.text("ALTER TABLE memories ADD COLUMN contributor_user_id INTEGER REFERENCES users(id)"))
        if "last_contributor_user_id" not in memory_columns:
            op.execute(sa.text("ALTER TABLE memories ADD COLUMN last_contributor_user_id INTEGER REFERENCES users(id)"))
    else:
        if "contributor_user_id" not in memory_columns:
            op.add_column("memories", sa.Column("contributor_user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_memories_contributor_user"), nullable=True))
        if "last_contributor_user_id" not in memory_columns:
            op.add_column("memories", sa.Column("last_contributor_user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_memories_last_contributor_user"), nullable=True))
    memory_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("memories")}
    if "ix_memories_contributor_user_id" not in memory_indexes:
        op.create_index("ix_memories_contributor_user_id", "memories", ["contributor_user_id"])
    if "ix_memories_last_contributor_user_id" not in memory_indexes:
        op.create_index("ix_memories_last_contributor_user_id", "memories", ["last_contributor_user_id"])

    op.execute(sa.text("UPDATE memories SET contributor_user_id = (SELECT user_id FROM conversations WHERE conversations.id = memories.source_conversation_id) WHERE contributor_user_id IS NULL"))
    op.execute(sa.text("UPDATE memories SET last_contributor_user_id = contributor_user_id WHERE last_contributor_user_id IS NULL"))

    revision_inspector = sa.inspect(bind)
    revision_columns = {column["name"] for column in revision_inspector.get_columns("memory_revisions")}
    if "source_conversation_id" not in revision_columns:
        op.add_column("memory_revisions", sa.Column("source_conversation_id", sa.Integer(), nullable=True))
    if "source_message_id" not in revision_columns:
        op.add_column("memory_revisions", sa.Column("source_message_id", sa.Integer(), nullable=True))
    revision_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("memory_revisions")}
    if "ix_memory_revisions_source_conversation_id" not in revision_indexes:
        op.create_index("ix_memory_revisions_source_conversation_id", "memory_revisions", ["source_conversation_id"])
    if "ix_memory_revisions_source_message_id" not in revision_indexes:
        op.create_index("ix_memory_revisions_source_message_id", "memory_revisions", ["source_message_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_revisions_source_message_id", table_name="memory_revisions")
    op.drop_index("ix_memory_revisions_source_conversation_id", table_name="memory_revisions")
    op.drop_column("memory_revisions", "source_message_id")
    op.drop_column("memory_revisions", "source_conversation_id")
    with op.batch_alter_table("memories") as batch:
        batch.drop_index("ix_memories_last_contributor_user_id")
        batch.drop_index("ix_memories_contributor_user_id")
        batch.drop_constraint("fk_memories_last_contributor_user", type_="foreignkey")
        batch.drop_constraint("fk_memories_contributor_user", type_="foreignkey")
        batch.drop_column("last_contributor_user_id")
        batch.drop_column("contributor_user_id")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_constraint("ck_conversations_mode", type_="check")
        batch.drop_column("mode")
    op.drop_table("legacy_collaborators")
    with op.batch_alter_table("legacies") as batch:
        batch.drop_index("ix_legacies_collaborator_code_digest")
        batch.drop_column("collaborator_code_rotated_at")
        batch.drop_column("collaborator_code_enabled")
        batch.drop_column("collaborator_code_hint")
        batch.drop_column("collaborator_code_ciphertext")
        batch.drop_column("collaborator_code_digest")
