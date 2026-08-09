"""Add legacy-safe users email verification state.

Revision ID: 0013
Revises: 0012
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_users_is_verified"
down_revision = "0012_auth_challenges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing accounts predate verification and must remain able to log in.
    op.add_column(
        "users",
        sa.Column(
            "is_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "is_verified",
            existing_type=sa.Boolean(),
            server_default=sa.false(),
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("is_verified")
