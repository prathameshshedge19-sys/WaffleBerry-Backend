"""Add durable message source and idempotency keys.

Revision ID: 0014_message_source_idempotency
Revises: 0013_users_is_verified
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_message_source_idempotency"
down_revision = "0013_users_is_verified"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("source_session_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("source_event_id", sa.String(length=200), nullable=True))
        batch_op.create_check_constraint(
            "ck_messages_source_supported",
            "source IS NULL OR source IN ('chat', 'live_call')",
        )
        batch_op.create_unique_constraint(
            "uq_messages_source_event_role",
            ["conversation_id", "source", "source_session_id", "source_event_id", "role"],
        )


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_constraint("uq_messages_source_event_role", type_="unique")
        batch_op.drop_constraint("ck_messages_source_supported", type_="check")
        batch_op.drop_column("source_event_id")
        batch_op.drop_column("source_session_id")
        batch_op.drop_column("source")
