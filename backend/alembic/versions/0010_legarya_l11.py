"""LegaRya L11 visitor identity profiles.

Revision ID: 0010_legarya_l11
Revises: 0009_legarya_l10
"""

from typing import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "0010_legarya_l11"
down_revision: str | None = "0009_legarya_l10"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legacy_visitor_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("viewer_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("preferred_name", sa.String(255)),
        sa.Column("claimed_relationship", sa.String(80)),
        sa.Column("matched_entity_id", sa.Integer(), sa.ForeignKey("memory_entities.id", ondelete="SET NULL")),
        sa.Column("relationship_status", sa.String(32), server_default="claimed", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("relationship_status IN ('claimed', 'verified_from_memory', 'unverified')", name="ck_legacy_visitor_profiles_relationship_status"),
        sa.UniqueConstraint("legacy_id", "viewer_user_id", name="uq_legacy_visitor_profiles_legacy_viewer"),
    )
    for name, columns in (
        ("ix_legacy_visitor_profiles_legacy_id", ["legacy_id"]),
        ("ix_legacy_visitor_profiles_viewer_user_id", ["viewer_user_id"]),
        ("ix_legacy_visitor_profiles_matched_entity_id", ["matched_entity_id"]),
        ("ix_legacy_visitor_profiles_legacy_status", ["legacy_id", "relationship_status"]),
    ):
        op.create_index(name, "legacy_visitor_profiles", columns)


def downgrade() -> None:
    op.drop_table("legacy_visitor_profiles")
