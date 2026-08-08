"""Add the legacy, Story Session, and Memory Engine persistence schema.

Revision ID: 0002_memory_engine
Revises: 0001_existing_schema
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_memory_engine"
down_revision: str | None = "0001_existing_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    """Create the same portable constrained enums used by the ORM."""
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
    )


def upgrade() -> None:
    """Create the non-destructive Memory Engine persistence foundation."""
    op.create_table(
        "legacies",
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("relationship", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            _enum("legacy_status", "active", "archived"),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(display_name)) > 0",
            name="ck_legacies_display_name_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(relationship)) > 0",
            name="ck_legacies_relationship_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("legacy_id"),
    )
    op.create_index("ix_legacies_legacy_id", "legacies", ["legacy_id"])
    op.create_index(
        "ix_legacies_owner_user_id",
        "legacies",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_legacies_owner_status",
        "legacies",
        ["owner_user_id", "status"],
    )

    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(
            sa.Column("legacy_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_conversations_legacy_id_legacies",
            "legacies",
            ["legacy_id"],
            ["legacy_id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_conversations_legacy_id",
            ["legacy_id"],
        )

    op.create_table(
        "story_sessions",
        sa.Column("story_session_id", sa.Integer(), nullable=False),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("chapter_key", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            _enum(
                "story_session_status",
                "in_progress",
                "paused",
                "completed",
            ),
            server_default="in_progress",
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "length(trim(chapter_key)) > 0",
            name="ck_story_sessions_chapter_key_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["legacy_id"],
            ["legacies.legacy_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("story_session_id"),
    )
    op.create_index(
        "ix_story_sessions_story_session_id",
        "story_sessions",
        ["story_session_id"],
    )
    op.create_index(
        "ix_story_sessions_legacy_id",
        "story_sessions",
        ["legacy_id"],
    )
    op.create_index(
        "ix_story_sessions_created_by_user_id",
        "story_sessions",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_story_sessions_legacy_chapter_status",
        "story_sessions",
        ["legacy_id", "chapter_key", "status"],
    )

    op.create_table(
        "story_messages",
        sa.Column("story_message_id", sa.Integer(), nullable=False),
        sa.Column("story_session_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            _enum("story_message_role", "user", "assistant"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_story_messages_sequence_positive",
        ),
        sa.CheckConstraint(
            "length(trim(content)) > 0",
            name="ck_story_messages_content_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["story_session_id"],
            ["story_sessions.story_session_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("story_message_id"),
        sa.UniqueConstraint(
            "story_session_id",
            "sequence",
            name="uq_story_messages_session_sequence",
        ),
    )
    op.create_index(
        "ix_story_messages_story_message_id",
        "story_messages",
        ["story_message_id"],
    )
    op.create_index(
        "ix_story_messages_story_session_id",
        "story_messages",
        ["story_session_id"],
    )
    op.create_index(
        "ix_story_messages_session_sequence",
        "story_messages",
        ["story_session_id", "sequence"],
    )

    op.create_table(
        "memory_contradiction_groups",
        sa.Column("contradiction_group_id", sa.Integer(), nullable=False),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column(
            "resolution_status",
            sa.String(length=40),
            server_default="unresolved",
            nullable=False,
        ),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "length(trim(topic)) > 0",
            name="ck_memory_contradiction_groups_topic_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["legacy_id"],
            ["legacies.legacy_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("contradiction_group_id"),
    )
    op.create_index(
        "ix_memory_contradiction_groups_contradiction_group_id",
        "memory_contradiction_groups",
        ["contradiction_group_id"],
    )
    op.create_index(
        "ix_memory_contradiction_groups_legacy_id",
        "memory_contradiction_groups",
        ["legacy_id"],
    )

    op.create_table(
        "memories",
        sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column(
            "memory_type",
            _enum("memory_type", "atomic", "narrative"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("emotional_significance", sa.Text(), nullable=True),
        sa.Column("importance", sa.Integer(), nullable=True),
        sa.Column(
            "extraction_confidence",
            sa.Numeric(precision=4, scale=3),
            nullable=True,
        ),
        sa.Column(
            "review_status",
            _enum(
                "memory_review_status",
                "candidate",
                "approved",
                "rejected",
                "superseded",
            ),
            server_default="candidate",
            nullable=False,
        ),
        sa.Column("uncertainty_note", sa.Text(), nullable=True),
        sa.Column("contradiction_group_id", sa.Integer(), nullable=True),
        sa.Column("superseded_by_memory_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_memories_title_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(summary)) > 0",
            name="ck_memories_summary_not_blank",
        ),
        sa.CheckConstraint(
            "importance IS NULL OR (importance >= 1 AND importance <= 5)",
            name="ck_memories_importance_range",
        ),
        sa.CheckConstraint(
            "extraction_confidence IS NULL OR "
            "(extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name="ck_memories_extraction_confidence_range",
        ),
        sa.CheckConstraint(
            "superseded_by_memory_id IS NULL OR "
            "superseded_by_memory_id <> memory_id",
            name="ck_memories_not_self_superseded",
        ),
        sa.CheckConstraint(
            "review_status <> 'superseded' OR "
            "superseded_by_memory_id IS NOT NULL",
            name="ck_memories_superseded_requires_replacement",
        ),
        sa.ForeignKeyConstraint(
            ["contradiction_group_id"],
            ["memory_contradiction_groups.contradiction_group_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["legacy_id"],
            ["legacies.legacy_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_memory_id"],
            ["memories.memory_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("memory_id"),
    )
    for index_name, columns in (
        ("ix_memories_memory_id", ["memory_id"]),
        ("ix_memories_legacy_id", ["legacy_id"]),
        ("ix_memories_category", ["category"]),
        ("ix_memories_review_status", ["review_status"]),
        ("ix_memories_contradiction_group_id", ["contradiction_group_id"]),
        ("ix_memories_superseded_by_memory_id", ["superseded_by_memory_id"]),
        (
            "ix_memories_legacy_review_status",
            ["legacy_id", "review_status"],
        ),
        (
            "ix_memories_legacy_category_type",
            ["legacy_id", "category", "memory_type"],
        ),
    ):
        op.create_index(index_name, "memories", columns)

    op.create_table(
        "memory_provenance",
        sa.Column("provenance_id", sa.Integer(), nullable=False),
        sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("story_session_id", sa.Integer(), nullable=True),
        sa.Column("story_message_id", sa.Integer(), nullable=True),
        sa.Column("source_locator", sa.JSON(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("speaker", sa.String(length=80), nullable=True),
        sa.Column("chapter", sa.String(length=120), nullable=True),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("extractor_version", sa.String(length=100), nullable=True),
        sa.CheckConstraint(
            "length(trim(source_type)) > 0",
            name="ck_memory_provenance_source_type_not_blank",
        ),
        sa.CheckConstraint(
            "excerpt IS NULL OR length(trim(excerpt)) > 0",
            name="ck_memory_provenance_excerpt_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.conversation_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memories.memory_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.message_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["story_message_id"],
            ["story_messages.story_message_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["story_session_id"],
            ["story_sessions.story_session_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("provenance_id"),
    )
    for index_name, columns in (
        ("ix_memory_provenance_provenance_id", ["provenance_id"]),
        ("ix_memory_provenance_memory_id", ["memory_id"]),
        ("ix_memory_provenance_source_type", ["source_type"]),
        ("ix_memory_provenance_conversation_id", ["conversation_id"]),
        ("ix_memory_provenance_message_id", ["message_id"]),
        ("ix_memory_provenance_story_session_id", ["story_session_id"]),
        ("ix_memory_provenance_story_message_id", ["story_message_id"]),
        (
            "ix_memory_provenance_conversation_source",
            ["conversation_id", "message_id"],
        ),
        (
            "ix_memory_provenance_story_source",
            ["story_session_id", "story_message_id"],
        ),
    ):
        op.create_index(index_name, "memory_provenance", columns)

    op.create_table(
        "memory_revisions",
        sa.Column("memory_revision_id", sa.Integer(), nullable=False),
        sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("edited_by_user_id", sa.Integer(), nullable=True),
        sa.Column("previous_content", sa.JSON(), nullable=False),
        sa.Column("edit_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_memory_revisions_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["edited_by_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memories.memory_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("memory_revision_id"),
        sa.UniqueConstraint(
            "memory_id",
            "revision_number",
            name="uq_memory_revisions_memory_number",
        ),
    )
    op.create_index(
        "ix_memory_revisions_memory_revision_id",
        "memory_revisions",
        ["memory_revision_id"],
    )
    op.create_index(
        "ix_memory_revisions_memory_id",
        "memory_revisions",
        ["memory_id"],
    )

    op.create_table(
        "memory_participants",
        sa.Column("memory_participant_id", sa.Integer(), nullable=False),
        sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("relationship", sa.String(length=100), nullable=True),
        sa.Column("role", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_memory_participants_name_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memories.memory_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("memory_participant_id"),
    )
    op.create_index(
        "ix_memory_participants_memory_participant_id",
        "memory_participants",
        ["memory_participant_id"],
    )
    op.create_index(
        "ix_memory_participants_memory_id",
        "memory_participants",
        ["memory_id"],
    )

    op.create_table(
        "tags",
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("normalized_name", sa.String(length=80), nullable=False),
        sa.CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_tags_name_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(normalized_name)) > 0",
            name="ck_tags_normalized_name_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["legacy_id"],
            ["legacies.legacy_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tag_id"),
        sa.UniqueConstraint(
            "legacy_id",
            "normalized_name",
            name="uq_tags_legacy_normalized_name",
        ),
    )
    op.create_index("ix_tags_tag_id", "tags", ["tag_id"])
    op.create_index("ix_tags_legacy_id", "tags", ["legacy_id"])

    op.create_table(
        "memory_tags",
        sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memories.memory_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.tag_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("memory_id", "tag_id"),
    )


def downgrade() -> None:
    """Remove Memory Engine tables while preserving the baseline schema."""
    op.drop_table("memory_tags")
    op.drop_table("tags")
    op.drop_table("memory_participants")
    op.drop_table("memory_revisions")
    op.drop_table("memory_provenance")
    op.drop_table("memories")
    op.drop_table("memory_contradiction_groups")
    op.drop_table("story_messages")
    op.drop_table("story_sessions")

    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_index("ix_conversations_legacy_id")
        batch_op.drop_constraint(
            "fk_conversations_legacy_id_legacies",
            type_="foreignkey",
        )
        batch_op.drop_column("legacy_id")

    op.drop_table("legacies")
