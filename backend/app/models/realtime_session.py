"""Connection metadata only. Messages and turns remain in their existing tables."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RealtimeSession(Base):
    __tablename__ = "realtime_sessions"
    __table_args__ = (
        CheckConstraint("mode IN ('rya', 'legacy')", name="ck_realtime_mode"),
        CheckConstraint("(mode = 'rya' AND role IN ('owner', 'collaborator')) OR (mode = 'legacy' AND role = 'viewer')", name="ck_realtime_role"),
        CheckConstraint("state IN ('authorized', 'connecting', 'connected', 'reconnecting', 'ended', 'revoked', 'failed')", name="ck_realtime_state"),
        CheckConstraint("connection_generation >= 0", name="ck_realtime_generation"),
        CheckConstraint("(state IN ('ended', 'revoked', 'failed') AND ended_at IS NOT NULL AND active_actor_id IS NULL AND lease_owner IS NULL AND lease_expires_at IS NULL) OR (state IN ('authorized', 'connecting', 'connected', 'reconnecting') AND ended_at IS NULL AND active_actor_id IS NOT NULL AND active_actor_id = actor_user_id)", name="ck_realtime_terminal"),
        CheckConstraint("(state IN ('connecting', 'connected') AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR (state NOT IN ('connecting', 'connected') AND lease_owner IS NULL AND lease_expires_at IS NULL)", name="ck_realtime_lease"),
        Index("ix_realtime_actor_created", "actor_user_id", "created_at"),
        Index("ix_realtime_expiry", "expires_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    legacy_id: Mapped[int] = mapped_column(ForeignKey("legacies.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    # NULL for terminal sessions; UNIQUE is the cross-worker active-call limit.
    active_actor_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    mode: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    origin: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    auth_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    auth_issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconnect_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connection_generation: Mapped[int] = mapped_column(Integer, default=0)
    end_reason: Mapped[str | None] = mapped_column(String(40))
    ticket_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    ticket_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ticket_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
