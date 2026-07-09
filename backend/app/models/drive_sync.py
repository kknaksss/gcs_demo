"""drive_sync_state / drive_sync_events — SPEC-004, ARCH-003 §6.

- drive_sync_state: connector 이어받기 단일 row(env 값은 저장 안 함).
- drive_sync_events: sync 이력. message에 원문/secret 금지.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

SYNC_EVENT_TYPES = (
    "webhook_received",
    "changes_listed",
    "document_upserted",
    "document_unavailable",
    "candidate_staled",
    "reanalysis_enqueued",
    "sync_failed",
)
SYNC_RESULTS = ("success", "skipped", "failed")


class DriveSyncState(Base):
    __tablename__ = "drive_sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    page_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    watch_channel_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    watch_resource_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    watch_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # connector 1개 — 단일 row 고정(id=1)만 허용.
        CheckConstraint("id = 1", name="ck_drive_sync_state_singleton"),
    )


class DriveSyncEvent(Base):
    __tablename__ = "drive_sync_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    drive_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    result: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('webhook_received', 'changes_listed', "
            "'document_upserted', 'document_unavailable', 'candidate_staled', "
            "'reanalysis_enqueued', 'sync_failed')",
            name="ck_drive_sync_events_event_type",
        ),
        CheckConstraint(
            "result IN ('success', 'skipped', 'failed')",
            name="ck_drive_sync_events_result",
        ),
        # ARCH-003 §6 index.
        Index("ix_drive_sync_events_occurred_at", "occurred_at"),
        Index("ix_drive_sync_events_document", "document_id"),
    )
