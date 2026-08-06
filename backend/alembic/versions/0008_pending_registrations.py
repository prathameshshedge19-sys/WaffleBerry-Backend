"""Add pending_registrations table for email-verified registration.

Revision ID: 0008_pending_registrations
Revises: 0007_trusted_story_memories
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0008_pending_registrations"
down_revision: str | None = "0007_trusted_story_memories"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create pending_registrations table."""
    op.create_table(
        "pending_registrations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("email_normalized", sa.String(255), nullable=False),
        sa.Column("otp_hash", sa.String(255), nullable=True),
        sa.Column("otp_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("otp_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("otp_send_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_otp_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registration_token_hash", sa.String(255), nullable=True),
        sa.Column("registration_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="pending_email_verification",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # Create indices for common queries
    op.create_index(
        "ix_pending_registrations_email_normalized",
        "pending_registrations",
        ["email_normalized"],
    )
    op.create_index(
        "ix_pending_registrations_status",
        "pending_registrations",
        ["status"],
    )
    op.create_index(
        "ix_pending_registrations_created_at",
        "pending_registrations",
        ["created_at"],
    )


def downgrade() -> None:
    """Drop pending_registrations table."""
    op.drop_index("ix_pending_registrations_created_at", table_name="pending_registrations")
    op.drop_index("ix_pending_registrations_status", table_name="pending_registrations")
    op.drop_index("ix_pending_registrations_email_normalized", table_name="pending_registrations")
    op.drop_table("pending_registrations")
