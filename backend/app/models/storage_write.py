"""Durable cancellation handles, independent of disposable product rows."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StorageWrite(Base):
    __tablename__ = "private_storage_writes"
    __table_args__ = (CheckConstraint("state IN ('reserved','writing','confirmed','closing','erased')",
                                     name="ck_private_storage_write_state"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    upload_id: Mapped[str | None] = mapped_column(String(2048))
    writer_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
