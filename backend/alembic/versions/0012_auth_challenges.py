"""Add purpose-bound authentication challenges.

Revision ID: 0012_auth_challenges
Revises: 0011_conversation_preferences
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_auth_challenges"
down_revision = "0011_conversation_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_challenges",
        sa.Column("challenge_id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("purpose", sa.String(50), nullable=False),
        sa.Column("otp_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_consumed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("authorization_hash", sa.String(255), nullable=True, unique=True),
        sa.Column("authorization_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorization_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_auth_challenges_challenge_id", "auth_challenges", ["challenge_id"])
    op.create_index("ix_auth_challenges_email", "auth_challenges", ["email"])
    op.create_index("ix_auth_challenges_purpose", "auth_challenges", ["purpose"])


def downgrade() -> None:
    op.drop_table("auth_challenges")
