"""Persist the selected standard Rya voice."""

from alembic import op
import sqlalchemy as sa


revision = "0013_voice_preference"
down_revision = "0012_legacy_conversation_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("voice_preference", sa.String(length=16), nullable=False, server_default="marin"))
        batch.create_check_constraint("ck_users_voice_preference", "voice_preference IN ('marin', 'cedar')")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_voice_preference", type_="check")
        batch.drop_column("voice_preference")
