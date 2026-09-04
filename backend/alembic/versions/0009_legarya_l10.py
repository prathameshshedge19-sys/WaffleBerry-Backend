"""LegaRya L10 invitations and access audit.

Revision ID: 0009_legarya_l10
Revises: 0008_legarya_l9
"""

from typing import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "0009_legarya_l10"
down_revision: str | None = "0008_legarya_l9"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legacy_access_invites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("invited_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("accepted_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('collaborator', 'viewer')", name="ck_access_invites_role"),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'revoked')", name="ck_access_invites_status"),
        sa.UniqueConstraint("token_digest", name="uq_access_invites_token_digest"),
    )
    for name, columns in (("ix_legacy_access_invites_legacy_id", ["legacy_id"]), ("ix_legacy_access_invites_invited_by_user_id", ["invited_by_user_id"]), ("ix_legacy_access_invites_accepted_by_user_id", ["accepted_by_user_id"]), ("ix_access_invites_legacy_status", ["legacy_id", "status"]), ("ix_access_invites_email_status", ["email", "status"])):
        op.create_index(name, "legacy_access_invites", columns)
    op.create_table(
        "legacy_access_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for name, columns in (("ix_legacy_access_events_legacy_id", ["legacy_id"]), ("ix_legacy_access_events_actor_user_id", ["actor_user_id"]), ("ix_legacy_access_events_target_user_id", ["target_user_id"]), ("ix_access_events_legacy_created", ["legacy_id", "created_at"])):
        op.create_index(name, "legacy_access_events", columns)


def downgrade() -> None:
    op.drop_table("legacy_access_events")
    op.drop_table("legacy_access_invites")
