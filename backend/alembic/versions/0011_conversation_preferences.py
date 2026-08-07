"""Add authoritative user conversation presentation preferences.

Revision ID: 0011_conversation_preferences
Revises: 0010_legacy_identity_facts
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011_conversation_preferences"
down_revision: str | None = "0010_legacy_identity_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.add_column(sa.Column(
            "conversation_style", sa.String(length=20), nullable=False,
            server_default="natural",
        ))
        batch_op.add_column(sa.Column(
            "response_length", sa.String(length=20), nullable=False,
            server_default="balanced",
        ))
        batch_op.create_check_constraint(
            "ck_user_settings_conversation_style",
            "conversation_style IN ('natural','gentle','expressive')",
        )
        batch_op.create_check_constraint(
            "ck_user_settings_response_length",
            "response_length IN ('short','balanced','detailed')",
        )


def downgrade() -> None:
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.drop_constraint("ck_user_settings_response_length", type_="check")
        batch_op.drop_constraint("ck_user_settings_conversation_style", type_="check")
        batch_op.drop_column("response_length")
        batch_op.drop_column("conversation_style")
