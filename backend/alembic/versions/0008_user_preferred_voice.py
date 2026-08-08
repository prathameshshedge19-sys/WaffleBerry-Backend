"""Add nullable user-level preferred Berry voice.

Revision ID: 0008_user_preferred_voice
Revises: 0007_trusted_story_memories
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008_user_preferred_voice"
down_revision: str | None = "0007_trusted_story_memories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.add_column(
            sa.Column("preferred_voice", sa.String(length=20), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_user_settings_preferred_voice",
            "preferred_voice IS NULL OR preferred_voice IN ("
            "'rohan','mani','shubh','varun','cedar',"
            "'rupali','simran','ritu','suhani','marin')",
        )


def downgrade() -> None:
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.drop_constraint(
            "ck_user_settings_preferred_voice",
            type_="check",
        )
        batch_op.drop_column("preferred_voice")
