"""ai_queue_jobs repository — WORK-004 (SPEC-007, ARCH-002).

job 원장 DB access 전용. 멱등 enqueue의 unique 판정 키는
(job_type, document_id, fingerprint, idempotency_key)다. 상태 전이 규칙은
services/ai_jobs가 담당하고, 여기는 조회/생성/필드 갱신만 제공한다.
stmt는 이 repo 안에서만 (ARCH-001 §4).
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_queue import AiQueueJob


class AiQueueJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, job_id: int) -> AiQueueJob | None:
        return await self._session.get(AiQueueJob, job_id)

    async def get_by_idempotency(
        self,
        *,
        job_type: str,
        document_id: int,
        fingerprint: str,
        idempotency_key: str,
    ) -> AiQueueJob | None:
        return await self._session.scalar(
            sa.select(AiQueueJob).where(
                AiQueueJob.job_type == job_type,
                AiQueueJob.document_id == document_id,
                AiQueueJob.fingerprint == fingerprint,
                AiQueueJob.idempotency_key == idempotency_key,
            )
        )

    async def find_active_for_fingerprint(
        self, *, document_id: int, fingerprint: str
    ) -> AiQueueJob | None:
        """진행 중(non-terminal 실행 경로) job — job_type 무관 최신 1건."""
        return await self._session.scalar(
            sa.select(AiQueueJob)
            .where(
                AiQueueJob.document_id == document_id,
                AiQueueJob.fingerprint == fingerprint,
                AiQueueJob.status.in_(("queued", "running", "succeeded")),
            )
            .order_by(AiQueueJob.id.desc())
        )

    async def find_retryable_for_fingerprint(
        self, *, document_id: int, fingerprint: str
    ) -> AiQueueJob | None:
        """수동/자동 재시도로 queued 복귀 가능한 최신 job."""
        return await self._session.scalar(
            sa.select(AiQueueJob)
            .where(
                AiQueueJob.document_id == document_id,
                AiQueueJob.fingerprint == fingerprint,
                AiQueueJob.status.in_(("failed", "timeout", "validation_failed")),
            )
            .order_by(AiQueueJob.id.desc())
        )

    async def get_latest_by_document(self, document_id: int) -> AiQueueJob | None:
        """문서의 최신 job 1건 — stale 후보 표시용 reanalysis_status 파생 입력
        (WORK-005, DEC-022 Admin UI States)."""
        return await self._session.scalar(
            sa.select(AiQueueJob)
            .where(AiQueueJob.document_id == document_id)
            .order_by(AiQueueJob.id.desc())
            .limit(1)
        )

    async def create(
        self,
        *,
        job_type: str,
        document_id: int,
        drive_file_id: str,
        fingerprint: str,
        idempotency_key: str,
        max_attempts: int,
        created_by: int | None = None,
    ) -> AiQueueJob:
        row = AiQueueJob(
            job_type=job_type,
            status="queued",
            document_id=document_id,
            drive_file_id=drive_file_id,
            fingerprint=fingerprint,
            idempotency_key=idempotency_key,
            attempt_count=0,
            max_attempts=max_attempts,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_by_document(
        self, document_id: int, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[AiQueueJob], int]:
        total = await self._session.scalar(
            sa.select(sa.func.count())
            .select_from(AiQueueJob)
            .where(AiQueueJob.document_id == document_id)
        )
        rows = await self._session.scalars(
            sa.select(AiQueueJob)
            .where(AiQueueJob.document_id == document_id)
            .order_by(AiQueueJob.created_at.desc(), AiQueueJob.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total or 0)

    async def list_due_queued(
        self, *, now: dt.datetime, limit: int
    ) -> list[AiQueueJob]:
        """worker pick — queued이고 next_run_at이 없거나 지난 job (오래된 순)."""
        rows = await self._session.scalars(
            sa.select(AiQueueJob)
            .where(
                AiQueueJob.status == "queued",
                sa.or_(
                    AiQueueJob.next_run_at.is_(None),
                    AiQueueJob.next_run_at <= now,
                ),
            )
            .order_by(AiQueueJob.id.asc())
            .limit(limit)
        )
        return list(rows)
