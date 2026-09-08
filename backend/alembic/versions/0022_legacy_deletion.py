"""Durable, fail-closed Legacy erasure marker; no existing data is deleted."""
from alembic import op
import sqlalchemy as sa

revision = "0022_legacy_deletion"
down_revision = "0021_visual_companions"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("legacies", sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_legacies_deletion_requested_at", "legacies", ["deletion_requested_at"])

def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM legacies WHERE deletion_requested_at IS NOT NULL")).scalar():
        raise RuntimeError("Finish pending Legacy erasure before removing its durable marker")
    op.drop_index("ix_legacies_deletion_requested_at", table_name="legacies")
    op.drop_column("legacies", "deletion_requested_at")
