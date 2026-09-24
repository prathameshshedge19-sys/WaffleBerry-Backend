"""Minimal durable deletion obligations; no email, content or credentials."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AccountDeletion(Base):
    __tablename__ = "account_deletions"
    __table_args__ = (
        CheckConstraint("state IN ('queued','waiting_for_purge','completed')", name="ck_account_deletion_state"),
        CheckConstraint("attempts >= 0", name="ck_account_deletion_attempts"),
        CheckConstraint("(state = 'completed' AND completed_at IS NOT NULL AND user_id IS NULL) OR (state <> 'completed' AND completed_at IS NULL AND user_id IS NOT NULL)", name="ck_account_deletion_terminal"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), unique=True)
    # Non-reusable opaque identity needed to reapply obligations after a restore.
    target_user_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    request_proof_hash: Mapped[str | None] = mapped_column(String(64))
    session_hash: Mapped[str | None] = mapped_column(String(64))
    requested_via: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    safe_error_code: Mapped[str | None] = mapped_column(String(48))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountDeletionReauth(Base):
    __tablename__ = "account_deletion_reauth"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    token_hash: Mapped[str | None] = mapped_column(String(64))
    session_hash: Mapped[str | None] = mapped_column(String(64))
    credential_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class DeletionLineage(Base):
    __tablename__ = "deletion_lineage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lineage: Mapped[str] = mapped_column(String(36), nullable=False)
    __table_args__ = (CheckConstraint("id = 1", name="ck_deletion_lineage_singleton"),)
