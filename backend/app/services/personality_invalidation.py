"""Transactional projection invalidation, independent of any request/visitor path.

The application uses ORM canonical-memory mutations. Bulk SQL is deliberately not
intercepted: maintenance SQL must explicitly call invalidate_in_transaction.
"""

from datetime import datetime, timezone

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.models.legacy import Legacy
from app.models.memory import Memory, MemoryEntity, MemoryEntityLink
from app.models.personality import LegacyPersonalityProfile

_KEY = "l13_pending_invalidations"
_FIELDS = (
    "canonical_text", "category", "subject_reference", "source_language",
    "source_excerpt", "source_conversation_id", "source_message_id",
    "contributor_user_id", "status", "story_key", "operation_type", "legacy_id",
)


def invalidate_in_transaction(connection, legacy_ids):
    """Atomic upsert on the same connection/transaction as the factual mutation."""
    dialect = connection.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError("Personality invalidation requires PostgreSQL or SQLite")
    table = LegacyPersonalityProfile.__table__
    now = datetime.now(timezone.utc)
    for legacy_id in sorted(set(legacy_ids)):
        # A cascading Legacy deletion must not recreate its derived row.
        if connection.scalar(select(Legacy.id).where(Legacy.id == legacy_id)) is None:
            continue
        statement = insert(table).values(
            legacy_id=legacy_id, source_generation=1, built_generation=0,
            profile_json=None, build_status="pending", attempts=0, updated_at=now,
        )
        connection.execute(statement.on_conflict_do_update(
            index_elements=[table.c.legacy_id],
            set_={
                "source_generation": table.c.source_generation + 1,
                "profile_json": None,
                "build_status": "pending",
                "attempts": 0,
                "next_attempt_at": None,
                "last_error_code": None,
                "updated_at": now,
                # Keep a live lease until its worker releases it (or it expires).
            },
        ))


def _before_flush(session, _context, _instances):
    legacy_ids, links, entities = set(), [], []
    for item in set(session.new) | set(session.dirty) | set(session.deleted):
        state = inspect(item)
        if isinstance(item, Memory):
            changed = item in session.new or item in session.deleted or any(
                state.attrs[name].history.has_changes() for name in _FIELDS
            )
            old_status = state.attrs.status.history.deleted
            active = item.status in (None, "active") or "active" in old_status
            if changed and active:
                legacy_ids.update(value for value in [item.legacy_id, *state.attrs.legacy_id.history.deleted] if value is not None)
        elif isinstance(item, MemoryEntityLink):
            if item in session.new or item in session.deleted or session.is_modified(item, include_collections=False):
                links.extend(value for value in [item.memory_id, *state.attrs.memory_id.history.deleted] if value is not None)
                # New relationship-assigned links may receive their FK during flush.
                if item in session.new:
                    entities.append(item)
        elif isinstance(item, MemoryEntity):
            if item in session.new or item in session.deleted or session.is_modified(item, include_collections=False):
                legacy_ids.add(item.legacy_id)
    if legacy_ids or links or entities:
        prior = session.info.setdefault(_KEY, (set(), [], []))
        prior[0].update(value for value in legacy_ids if value is not None)
        prior[1].extend(links)
        prior[2].extend(entities)


def _after_flush(session, _context):
    pending = session.info.pop(_KEY, None)
    if pending is None:
        return
    legacy_ids, memory_ids, links = pending
    memory_ids.extend(link.memory_id for link in links if link.memory_id is not None)
    connection = session.connection()
    if memory_ids:
        legacy_ids.update(connection.scalars(select(Memory.legacy_id).where(Memory.id.in_(memory_ids), Memory.status == "active")))
    invalidate_in_transaction(connection, legacy_ids)


def _rollback(session, *_args):
    session.info.pop(_KEY, None)


def install_invalidation_hooks():
    if not event.contains(Session, "before_flush", _before_flush):
        event.listen(Session, "before_flush", _before_flush)
        event.listen(Session, "after_flush_postexec", _after_flush)
        event.listen(Session, "after_rollback", _rollback)
        event.listen(Session, "after_soft_rollback", _rollback)


install_invalidation_hooks()
