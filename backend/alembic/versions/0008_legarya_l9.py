"""LegaRya L9 per-Legacy daily activity.

Revision ID: 0008_legarya_l9
Revises: 0007_legarya_l8
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008_legarya_l9"
down_revision: str | None = "0007_legarya_l8"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("builder_activities") as batch:
        batch.add_column(sa.Column("contribution_count", sa.Integer(), server_default="1", nullable=False))
        batch.add_column(sa.Column("first_contributor_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("last_contributor_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("first_contribution_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        batch.add_column(sa.Column("last_contribution_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.execute("UPDATE builder_activities SET contribution_count = (SELECT COUNT(*) FROM builder_activities b2 WHERE b2.legacy_id = builder_activities.legacy_id AND b2.activity_date = builder_activities.activity_date), first_contributor_user_id = user_id, last_contributor_user_id = user_id")
    op.execute("DELETE FROM builder_activities WHERE id NOT IN (SELECT MIN(id) FROM builder_activities GROUP BY legacy_id, activity_date)")
    with op.batch_alter_table("builder_activities") as batch:
        batch.create_foreign_key("fk_builder_first_contributor", "users", ["first_contributor_user_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_builder_last_contributor", "users", ["last_contributor_user_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_builder_activities_first_contributor_user_id", ["first_contributor_user_id"])
        batch.create_index("ix_builder_activities_last_contributor_user_id", ["last_contributor_user_id"])
        batch.create_unique_constraint("uq_builder_activities_legacy_date", ["legacy_id", "activity_date"])


def downgrade() -> None:
    with op.batch_alter_table("builder_activities") as batch:
        batch.drop_constraint("uq_builder_activities_legacy_date", type_="unique")
        batch.drop_index("ix_builder_activities_last_contributor_user_id")
        batch.drop_index("ix_builder_activities_first_contributor_user_id")
        batch.drop_constraint("fk_builder_last_contributor", type_="foreignkey")
        batch.drop_constraint("fk_builder_first_contributor", type_="foreignkey")
        batch.drop_column("updated_at")
        batch.drop_column("last_contribution_at")
        batch.drop_column("first_contribution_at")
        batch.drop_column("last_contributor_user_id")
        batch.drop_column("first_contributor_user_id")
        batch.drop_column("contribution_count")
