"""LegaRya L6 read-only Legacy persona access.

Revision ID: 0006_legarya_l6
Revises: 0005_legarya_l5
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006_legarya_l6"
down_revision: str | None = "0005_legarya_l5"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("legacies", sa.Column("viewer_code_digest", sa.String(64), nullable=True))
    op.add_column("legacies", sa.Column("viewer_code_ciphertext", sa.Text(), nullable=True))
    op.add_column("legacies", sa.Column("viewer_code_hint", sa.String(20), nullable=True))
    op.add_column("legacies", sa.Column("viewer_code_enabled", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("legacies", sa.Column("viewer_code_rotated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_legacies_viewer_code_digest", "legacies", ["viewer_code_digest"], unique=True)

    op.create_table(
        "legacy_viewer_accesses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), server_default="active", nullable=False),
        sa.Column("granted_via_code", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("legacy_id", "user_id", name="uq_legacy_viewer_accesses_legacy_user"),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_legacy_viewer_accesses_status"),
    )
    op.create_index("ix_legacy_viewer_accesses_legacy_id", "legacy_viewer_accesses", ["legacy_id"])
    op.create_index("ix_legacy_viewer_accesses_user_id", "legacy_viewer_accesses", ["user_id"])
    op.create_index("ix_legacy_viewer_accesses_user_status", "legacy_viewer_accesses", ["user_id", "status"])


def downgrade() -> None:
    op.drop_table("legacy_viewer_accesses")
    op.drop_index("ix_legacies_viewer_code_digest", table_name="legacies")
    op.drop_column("legacies", "viewer_code_rotated_at")
    op.drop_column("legacies", "viewer_code_enabled")
    op.drop_column("legacies", "viewer_code_hint")
    op.drop_column("legacies", "viewer_code_ciphertext")
    op.drop_column("legacies", "viewer_code_digest")
