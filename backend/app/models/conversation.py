from datetime import datetime
from enum import Enum

from sqlalchemy import CheckConstraint, DateTime, Enum as SqlEnum, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("user_id", "source_daily_prompt_id", name="uq_conversations_user_daily_prompt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    legacy_id: Mapped[int | None] = mapped_column(ForeignKey("legacies.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="New chat")
    mode: Mapped[str] = mapped_column(String(24), nullable=False, default="rya", server_default="rya")
    source_daily_prompt_id: Mapped[int | None] = mapped_column(ForeignKey("daily_prompts.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True)

    user = relationship("User", back_populates="conversations")
    legacy = relationship("Legacy", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("length(trim(content)) > 0", name="ck_messages_content_not_blank"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[MessageRole] = mapped_column(SqlEnum(MessageRole, native_enum=False, values_callable=lambda values: [v.value for v in values], name="message_role"))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation = relationship("Conversation", back_populates="messages")
    web_sources = relationship("MessageWebSource", back_populates="message", cascade="all, delete-orphan", order_by="MessageWebSource.id")
