"""Baseline the database schema that predates Alembic.

Existing deployments must stamp this revision rather than execute it. Fresh
databases execute it normally before applying later revisions.

Revision ID: 0001_existing_schema
Revises:
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_existing_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the pre-Memory-Engine schema for a fresh database."""
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_id", "projects", ["id"])
    op.create_index("ix_projects_name", "projects", ["name"])

    op.create_table(
        "users",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_user_id", "users", ["user_id"])

    op.create_table(
        "voice_profiles",
        sa.Column("voice_profile_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("voice_name", sa.String(length=255), nullable=False),
        sa.Column("relationship", sa.String(length=100), nullable=False),
        sa.Column("language", sa.String(length=50), nullable=True),
        sa.Column("accent", sa.String(length=100), nullable=True),
        sa.Column("training_status", sa.String(length=20), nullable=True),
        sa.Column("model_path", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("voice_profile_id"),
    )
    op.create_index(
        "ix_voice_profiles_user_id",
        "voice_profiles",
        ["user_id"],
    )
    op.create_index(
        "ix_voice_profiles_voice_profile_id",
        "voice_profiles",
        ["voice_profile_id"],
    )

    op.create_table(
        "voice_samples",
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("voice_profile_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("file_size_mb", sa.Integer(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("sample_id"),
    )
    op.create_index(
        "ix_voice_samples_sample_id",
        "voice_samples",
        ["sample_id"],
    )
    op.create_index(
        "ix_voice_samples_voice_profile_id",
        "voice_samples",
        ["voice_profile_id"],
    )

    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "title",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index(
        "ix_conversations_conversation_id",
        "conversations",
        ["conversation_id"],
    )
    op.create_index(
        "ix_conversations_user_id",
        "conversations",
        ["user_id"],
    )

    message_role = sa.Enum(
        "user",
        "assistant",
        "system",
        name="message_role",
        native_enum=False,
        create_constraint=True,
    )
    op.create_table(
        "messages",
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", message_role, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("audio_path", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.CheckConstraint(
            "length(trim(content)) > 0",
            name="ck_messages_content_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.conversation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index(
        "ix_messages_conversation_id",
        "messages",
        ["conversation_id"],
    )
    op.create_index("ix_messages_message_id", "messages", ["message_id"])

    op.create_table(
        "consent",
        sa.Column("consent_id", sa.Integer(), nullable=False),
        sa.Column("voice_profile_id", sa.Integer(), nullable=False),
        sa.Column("consent_given", sa.Boolean(), nullable=True),
        sa.Column(
            "consent_date",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column(
            "consent_document_path",
            sa.String(length=500),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("consent_id"),
    )
    op.create_index("ix_consent_consent_id", "consent", ["consent_id"])
    op.create_index(
        "ix_consent_voice_profile_id",
        "consent",
        ["voice_profile_id"],
    )

    op.create_table(
        "user_settings",
        sa.Column("setting_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("theme", sa.String(length=20), nullable=True),
        sa.Column("language", sa.String(length=50), nullable=True),
        sa.Column("speech_speed", sa.String(length=20), nullable=True),
        sa.Column("ai_personality", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("setting_id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_user_settings_setting_id",
        "user_settings",
        ["setting_id"],
    )
    op.create_index(
        "ix_user_settings_user_id",
        "user_settings",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    """Remove the baseline schema from a database created by Alembic."""
    op.drop_table("user_settings")
    op.drop_table("consent")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("voice_samples")
    op.drop_table("voice_profiles")
    op.drop_table("users")
    op.drop_table("projects")
