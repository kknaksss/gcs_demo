"""ai_queue_jobs — ARCH-002 §3. AI classification / stale reanalysis 상태 원장.

Redis는 dispatch만, 상태 SoT는 이 테이블. dispatch/retry 진행은 status가 아니라
attempt_count/next_run_at로 추적. 중복 enqueue 방지는 복합 unique.
원문/secret은 payload_ref/result_ref/last_error_message에 저장 금지.
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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

JOB_TYPES = ("classification", "stale_reanalysis")
JOB_STATUSES = (
    "queued",
    "running",
    "succeeded",
    "candidate_saved",
    "validation_failed",
    "failed",
    "timeout",
    "stale",
)


class AiQueueJob(Base):
    __tablename__ = "ai_queue_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("metadata_candidates.id", ondelete="SET NULL"), nullable=True
    )
    drive_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    external_task_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_run_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "job_type",
            "document_id",
            "fingerprint",
            "idempotency_key",
            name="uq_ai_queue_jobs_idempotency",
        ),
        CheckConstraint(
            "job_type IN ('classification', 'stale_reanalysis')",
            name="ck_ai_queue_jobs_job_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'candidate_saved', "
            "'validation_failed', 'failed', 'timeout', 'stale')",
            name="ck_ai_queue_jobs_status",
        ),
        # ARCH-002 §4 권장 index.
        Index("ix_ai_queue_jobs_status_next_run", "status", "next_run_at"),
        Index("ix_ai_queue_jobs_document_created", "document_id", "created_at"),
        Index("ix_ai_queue_jobs_external_task", "external_task_id"),
        Index("ix_ai_queue_jobs_document_fingerprint", "document_id", "fingerprint"),
    )
