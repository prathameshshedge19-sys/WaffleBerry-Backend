"""L14 lifecycle; historical messages deliberately have no inferred turns."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint("actor_user_id", "conversation_id", "client_turn_id", name="uq_turn_client_key"),
        CheckConstraint("mode IN ('rya', 'legacy')", name="ck_turn_mode"),
        CheckConstraint("input_mode IN ('text', 'voice', 'realtime_voice')", name="ck_turn_input_mode"),
        CheckConstraint("state IN ('pending', 'streaming', 'completed', 'interrupted', 'failed')", name="ck_turn_state"),
        CheckConstraint("(state IN ('pending', 'streaming') AND finished_at IS NULL) OR (state IN ('completed', 'interrupted', 'failed') AND finished_at IS NOT NULL)", name="ck_turn_finished"),
        CheckConstraint("state != 'completed' OR (user_message_id IS NOT NULL AND assistant_message_id IS NOT NULL)", name="ck_turn_completed_links"),
        CheckConstraint("assistant_message_id IS NULL OR state IN ('completed', 'interrupted')", name="ck_turn_assistant_state"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    # A pre-L2 unlinked conversation may fail before its Legacy is initialized.
    legacy_id: Mapped[int | None] = mapped_column(ForeignKey("legacies.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(24))
    client_turn_id: Mapped[str | None] = mapped_column(String(128))
    request_digest: Mapped[str] = mapped_column(String(64))
    input_mode: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(16), default="pending")
    # CAS ownership token is never supplied by the client and is never reclaimed automatically.
    claim_token: Mapped[str | None] = mapped_column(String(36))
    user_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True)
    assistant_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True)
    safe_error_code: Mapped[str | None] = mapped_column(String(32))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TurnEffect(Base):
    __tablename__ = "turn_effects"
    __table_args__ = (CheckConstraint("kind IN ('memory', 'activity')", name="ck_turn_effect_kind"),)
    turn_id: Mapped[int] = mapped_column(ForeignKey("conversation_turns.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
