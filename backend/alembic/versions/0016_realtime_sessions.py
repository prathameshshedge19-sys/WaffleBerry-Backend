"""L15 connection metadata only; no transcript, memory or historical mutations."""
from alembic import op
import sqlalchemy as sa

revision = "0016_realtime_sessions"
down_revision = "0015_conversation_turns"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('realtime_sessions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('actor_user_id', sa.Integer(), nullable=False),
    sa.Column('legacy_id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=True),
    sa.Column('active_actor_id', sa.Integer(), nullable=True),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('origin', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('auth_expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('auth_issued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lease_owner', sa.String(length=36), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reconnect_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('connection_generation', sa.Integer(), nullable=False),
    sa.Column('end_reason', sa.String(length=40), nullable=True),
    sa.Column('ticket_hash', sa.String(length=64), nullable=True),
    sa.Column('ticket_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ticket_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(mode = 'rya' AND role IN ('owner', 'collaborator')) OR (mode = 'legacy' AND role = 'viewer')", name='ck_realtime_role'),
    sa.CheckConstraint("(state IN ('connecting', 'connected') AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR (state NOT IN ('connecting', 'connected') AND lease_owner IS NULL AND lease_expires_at IS NULL)", name='ck_realtime_lease'),
    sa.CheckConstraint("(state IN ('ended', 'revoked', 'failed') AND ended_at IS NOT NULL AND active_actor_id IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL) OR (state IN ('authorized', 'connecting', 'connected', 'reconnecting') AND ended_at IS NULL AND active_actor_id IS NOT NULL AND active_actor_id = actor_user_id)", name='ck_realtime_terminal'),
    sa.CheckConstraint("mode IN ('rya', 'legacy')", name='ck_realtime_mode'),
    sa.CheckConstraint("state IN ('authorized', 'connecting', 'connected', 'reconnecting', 'ended', 'revoked', 'failed')", name='ck_realtime_state'),
    sa.CheckConstraint('connection_generation >= 0', name='ck_realtime_generation'),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['legacy_id'], ['legacies.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('active_actor_id'),
    sa.UniqueConstraint('ticket_hash')
    )
    op.create_index('ix_realtime_actor_created', 'realtime_sessions', ['actor_user_id', 'created_at'], unique=False)
    op.create_index('ix_realtime_expiry', 'realtime_sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_realtime_sessions_conversation_id'), 'realtime_sessions', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_realtime_sessions_legacy_id'), 'realtime_sessions', ['legacy_id'], unique=False)

def downgrade():
    op.drop_table("realtime_sessions")
