"""LegaRya L2 conversational Legacy identity foundation."""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_legarya_l2"
down_revision: str | None = "0001_legarya_l1"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legacies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_name", sa.String(255), nullable=True),
        sa.Column("relationship_to_owner", sa.String(80), nullable=True),
        sa.Column("is_self", sa.Boolean(), nullable=True),
        sa.Column("setup_status", sa.String(32), server_default="collecting_identity", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "setup_status IN ('collecting_identity', 'active', 'archived')",
            name="ck_legacies_setup_status",
        ),
    )
    op.create_index("ix_legacies_owner_user_id", "legacies", ["owner_user_id"])
    op.create_index("ix_legacies_owner_status", "legacies", ["owner_user_id", "setup_status"])
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE users ADD COLUMN active_legacy_id INTEGER REFERENCES legacies(id)")
    else:
        op.add_column("users", sa.Column("active_legacy_id", sa.Integer(), sa.ForeignKey("legacies.id", name="fk_users_active_legacy_id"), nullable=True))
    op.create_index("ix_users_active_legacy_id", "users", ["active_legacy_id"])
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE conversations ADD COLUMN legacy_id INTEGER REFERENCES legacies(id)")
    else:
        op.add_column("conversations", sa.Column("legacy_id", sa.Integer(), sa.ForeignKey("legacies.id"), nullable=True))
    op.create_index("ix_conversations_legacy_id", "conversations", ["legacy_id"])


def downgrade() -> None:
    op.drop_index("ix_conversations_legacy_id", table_name="conversations")
    op.drop_column("conversations", "legacy_id")
    op.drop_index("ix_users_active_legacy_id", table_name="users")
    op.drop_column("users", "active_legacy_id")
    op.drop_table("legacies")
