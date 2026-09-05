"""Link builder conversations to their seeded daily prompt.

Revision ID: 0011_daily_prompt_conversation
Revises: 0010_legarya_l11
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011_daily_prompt_conversation"
down_revision: str | None = "0010_legarya_l11"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(sa.Column("source_daily_prompt_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_conversations_source_daily_prompt_id",
            "daily_prompts",
            ["source_daily_prompt_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_conversations_source_daily_prompt_id", ["source_daily_prompt_id"])
        batch_op.create_unique_constraint(
            "uq_conversations_user_daily_prompt",
            ["user_id", "source_daily_prompt_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_constraint("uq_conversations_user_daily_prompt", type_="unique")
        batch_op.drop_index("ix_conversations_source_daily_prompt_id")
        batch_op.drop_constraint("fk_conversations_source_daily_prompt_id", type_="foreignkey")
        batch_op.drop_column("source_daily_prompt_id")
