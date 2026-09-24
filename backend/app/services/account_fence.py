"""Fence authenticated writes against account deletion, including late requests.

Read-only authentication does not hold a row lock across provider I/O. Before
the first write we take the actor fence. NOWAIT avoids an inverted lock-order
deadlock with a request that already holds a Legacy/turn lock. A losing writer
rolls back; it must never publish against a retired identity.
"""
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.user import User


def new_account_id(db):
    """PostgreSQL sequences never reuse IDs; cover pre-AUTOINCREMENT SQLite too."""
    if db.bind.dialect.name != "sqlite":
        return None
    from app.models.account_deletion import AccountDeletion
    return max(db.scalar(select(func.max(User.id))) or 0,
               db.scalar(select(func.max(AccountDeletion.target_user_id))) or 0) + 1


def require_active(db, actor_id, *, lock=False):
    query = select(User.id).where(User.id == actor_id, User.deletion_requested_at.is_(None))
    if lock:
        query = query.with_for_update(nowait=True)
    try:
        present = db.scalar(query)
    except OperationalError:
        raise HTTPException(409, detail="Account operation in progress. Please retry.") from None
    if present is None:
        raise HTTPException(401, detail="This session is no longer available.")


def _fence(db):
    actor = db.info.get("account_actor_id")
    if actor is not None and not db.info.get("account_deletion_cleanup"):
        require_active(db, actor, lock=True)


@event.listens_for(Session, "before_flush")
def _before_flush(db, *_):
    if db.new or db.dirty or db.deleted:
        _fence(db)


@event.listens_for(Session, "do_orm_execute")
def _before_bulk_write(state):
    if state.is_insert or state.is_update or state.is_delete:
        _fence(state.session)
