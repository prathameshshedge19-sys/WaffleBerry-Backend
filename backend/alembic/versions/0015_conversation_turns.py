"""Add durable L14 turns and atomic per-effect receipts; no historical backfill."""
from alembic import op
import sqlalchemy as sa

revision = "0015_conversation_turns"
down_revision = "0014_legacy_personality"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversation_turns",
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('legacy_id', sa.Integer(), sa.ForeignKey('legacies.id', ondelete='CASCADE'), nullable=True),
        sa.Column('actor_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mode', sa.String(length=24), nullable=False),
        sa.Column('client_turn_id', sa.String(length=128), nullable=True),
        sa.Column('request_digest', sa.String(length=64), nullable=False),
        sa.Column('input_mode', sa.String(length=24), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('claim_token', sa.String(length=36), nullable=True),
        sa.Column('user_message_id', sa.Integer(), sa.ForeignKey('messages.id', ondelete='CASCADE'), nullable=True),
        sa.Column('assistant_message_id', sa.Integer(), sa.ForeignKey('messages.id', ondelete='CASCADE'), nullable=True),
        sa.Column('safe_error_code', sa.String(length=32), nullable=True),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('assistant_message_id', name=None),
        sa.UniqueConstraint('user_message_id', name=None),
        sa.CheckConstraint("assistant_message_id IS NULL OR state IN ('completed', 'interrupted')", name='ck_turn_assistant_state'),
        sa.CheckConstraint("state != 'completed' OR (user_message_id IS NOT NULL AND assistant_message_id IS NOT NULL)", name='ck_turn_completed_links'),
        sa.CheckConstraint("(state IN ('pending', 'streaming') AND finished_at IS NULL) OR (state IN ('completed', 'interrupted', 'failed') AND finished_at IS NOT NULL)", name='ck_turn_finished'),
        sa.CheckConstraint("input_mode IN ('text', 'voice', 'realtime_voice')", name='ck_turn_input_mode'),
        sa.CheckConstraint("mode IN ('rya', 'legacy')", name='ck_turn_mode'),
        sa.CheckConstraint("state IN ('pending', 'streaming', 'completed', 'interrupted', 'failed')", name='ck_turn_state'),
        sa.UniqueConstraint('actor_user_id', 'conversation_id', 'client_turn_id', name='uq_turn_client_key'),
    )
    op.create_index('ix_conversation_turns_conversation_id', 'conversation_turns', ['conversation_id'])
    op.create_index('ix_conversation_turns_legacy_id', 'conversation_turns', ['legacy_id'])
    op.create_table(
        "turn_effects",
        sa.Column('turn_id', sa.Integer(), sa.ForeignKey('conversation_turns.id', ondelete='CASCADE'), primary_key=True, nullable=False),
        sa.Column('kind', sa.String(length=16), primary_key=True, nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('memory', 'activity')", name='ck_turn_effect_kind'),
    )


def downgrade():
    op.drop_table("turn_effects")
    op.drop_table("conversation_turns")
