"""Approve source-grounded memories from completed Guided Stories.

Revision ID: 0007_trusted_story_memories
Revises: 0006_story_auto_approval
"""

from datetime import datetime, timezone
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007_trusted_story_memories"
down_revision: str | None = "0006_story_auto_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _eligible_memories(connection) -> list[tuple[int, int]]:
    """Find candidate memories with verified completed-Story provenance."""
    excerpt_match = (
        "instr(sm.content, mp.excerpt) > 0"
        if connection.dialect.name == "sqlite"
        else "position(mp.excerpt in sm.content) > 0"
    )
    rows = connection.execute(sa.text(f"""
        SELECT DISTINCT m.memory_id, ss.created_by_user_id
        FROM memories AS m
        JOIN legacies AS l
          ON l.legacy_id = m.legacy_id
        JOIN memory_provenance AS mp
          ON mp.memory_id = m.memory_id
         AND mp.source_type = 'story_session'
        JOIN story_sessions AS ss
          ON ss.story_session_id = mp.story_session_id
         AND ss.legacy_id = m.legacy_id
         AND ss.status = 'completed'
         AND ss.created_by_user_id = l.owner_user_id
        JOIN story_messages AS sm
          ON sm.story_message_id = mp.story_message_id
         AND sm.story_session_id = ss.story_session_id
         AND sm.role = 'user'
        WHERE m.review_status = 'candidate'
          AND m.superseded_by_memory_id IS NULL
          AND mp.speaker = 'user'
          AND mp.excerpt IS NOT NULL
          AND length(trim(mp.excerpt)) > 0
          AND {excerpt_match}
        ORDER BY m.memory_id
    """))
    return [(row.memory_id, row.created_by_user_id) for row in rows]


def upgrade() -> None:
    connection = op.get_bind()
    reviewed_at = datetime.now(timezone.utc)
    update = sa.text("""
        UPDATE memories
        SET review_status = 'approved',
            reviewed_at = :reviewed_at,
            reviewed_by_user_id = :reviewed_by_user_id
        WHERE memory_id = :memory_id
          AND review_status = 'candidate'
          AND superseded_by_memory_id IS NULL
    """)
    for memory_id, owner_user_id in _eligible_memories(connection):
        connection.execute(update, {
            "memory_id": memory_id,
            "reviewed_at": reviewed_at,
            "reviewed_by_user_id": owner_user_id,
        })


def downgrade() -> None:
    # A safe downgrade cannot distinguish these approvals from later valid use.
    pass
