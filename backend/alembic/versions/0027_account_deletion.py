"""Durable account erasure and single-use, credential-verified confirmation.

Revision ID: 0027_account_deletion
Revises: 0026_voice_live_synthesis
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_account_deletion"
down_revision = "0026_voice_live_synthesis"
branch_labels = depends_on = None


def upgrade():
    op.create_table("deletion_lineage", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lineage", sa.String(36), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_deletion_lineage_singleton"))
    op.create_table("private_storage_writes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("upload_id", sa.String(2048)),
        sa.Column("writer_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("erased_at", sa.DateTime(timezone=True)),
        sa.Column("reconcile_after", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('reserved','writing','confirmed','closing','erased')", name="ck_private_storage_write_state"))
    op.create_index("ix_private_storage_writes_scope", "private_storage_writes", ["scope"])
    op.create_index("ix_private_storage_writes_reconcile_after", "private_storage_writes", ["reconcile_after"])
    op.add_column("users", sa.Column("deletion_requested_at", sa.DateTime(timezone=True)))
    op.create_index("ix_users_deletion_requested_at", "users", ["deletion_requested_at"])
    op.create_table("account_deletions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), unique=True),
        sa.Column("target_user_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("request_proof_hash", sa.String(64)), sa.Column("session_hash", sa.String(64)),
        sa.Column("requested_via", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("safe_error_code", sa.String(48)),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("state IN ('queued','waiting_for_purge','completed')", name="ck_account_deletion_state"),
        sa.CheckConstraint("attempts >= 0", name="ck_account_deletion_attempts"),
        sa.CheckConstraint("(state = 'completed' AND completed_at IS NOT NULL AND user_id IS NULL) OR (state <> 'completed' AND completed_at IS NULL AND user_id IS NOT NULL)", name="ck_account_deletion_terminal"))
    op.create_index("ix_account_deletions_next_attempt_at", "account_deletions", ["next_attempt_at"])
    op.create_table("account_deletion_reauth",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("token_hash", sa.String(64)), sa.Column("session_hash", sa.String(64)),
        sa.Column("credential_hash", sa.String(64)), sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False))


def downgrade():
    # Even completed receipts may be necessary to prevent backup resurrection.
    if op.get_context().as_sql:
        raise RuntimeError("Account deletion downgrade requires an online safety check")
    if op.get_bind().execute(sa.text("SELECT 1 FROM private_storage_writes LIMIT 1")).first():
        raise RuntimeError("Cannot discard private storage cancellation obligations")
    if op.get_bind().execute(sa.text("SELECT 1 FROM account_deletions LIMIT 1")).first() or op.get_bind().execute(sa.text("SELECT 1 FROM users WHERE deletion_requested_at IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Cannot discard account deletion obligations; retain revision 0027")
    op.drop_table("account_deletion_reauth")
    op.drop_index("ix_account_deletions_next_attempt_at", table_name="account_deletions")
    op.drop_table("account_deletions")
    op.drop_index("ix_users_deletion_requested_at", table_name="users")
    op.drop_column("users", "deletion_requested_at")
    op.drop_index("ix_private_storage_writes_scope", table_name="private_storage_writes")
    op.drop_index("ix_private_storage_writes_reconcile_after", table_name="private_storage_writes")
    op.drop_table("private_storage_writes")
    op.drop_table("deletion_lineage")
