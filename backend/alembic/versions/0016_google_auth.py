"""Add Google account identity.

Revision ID: 0016_google_auth
Revises: 0015_quota_foundation
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_google_auth"
down_revision = "0015_quota_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("google_sub", sa.String(length=255), nullable=True))
        batch_op.create_index("ix_users_google_sub", ["google_sub"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_google_sub")
        batch_op.drop_column("google_sub")
