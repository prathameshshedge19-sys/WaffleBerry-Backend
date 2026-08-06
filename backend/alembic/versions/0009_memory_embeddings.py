"""Add versioned multilingual memory embeddings.

Revision ID: 0009_memory_embeddings
Revises: 0008_user_preferred_voice
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0009_memory_embeddings"
down_revision: str | None = "0008_user_preferred_voice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memories") as batch_op:
        batch_op.add_column(sa.Column("embedding", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("embedding_model", sa.String(120), nullable=True))
        batch_op.add_column(sa.Column("embedding_version", sa.String(40), nullable=True))
        batch_op.add_column(sa.Column("embedding_dimensions", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("memories") as batch_op:
        batch_op.drop_column("embedded_at")
        batch_op.drop_column("embedding_dimensions")
        batch_op.drop_column("embedding_version")
        batch_op.drop_column("embedding_model")
        batch_op.drop_column("embedding")
