"""Allow read-only Legacy conversations alongside Rya builder conversations.

L5 added a PostgreSQL-only Rya constraint; L6 introduced Legacy mode without
updating it. SQLite create_all tests did not exercise that deployed constraint.
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_legacy_conversation_mode"
down_revision = "0011_daily_prompt_conversation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    checks = sa.inspect(op.get_bind()).get_check_constraints("conversations")
    with op.batch_alter_table("conversations") as batch:
        if any(check["name"] == "ck_conversations_mode" for check in checks):
            batch.drop_constraint("ck_conversations_mode", type_="check")
        batch.create_check_constraint("ck_conversations_mode", "mode IN ('rya', 'legacy')")


def downgrade() -> None:
    # Refuse to invalidate existing Legacy chats; never delete or convert them.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM conversations WHERE mode = 'legacy'")):
        raise RuntimeError("Cannot downgrade while Legacy conversations exist.")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_constraint("ck_conversations_mode", type_="check")
        if op.get_bind().dialect.name != "sqlite":
            batch.create_check_constraint("ck_conversations_mode", "mode IN ('rya')")
