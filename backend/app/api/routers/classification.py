"""classification job admin 라우트 — WORK-004 (SPEC-007 API Contract).

- POST /admin/documents/{id}/classify           : 수동 AI 분류/재분석 (admin)
- GET  /admin/classification-jobs/{id}          : job 상태 조회 (admin)
- GET  /admin/documents/{id}/classification-jobs: 문서별 분석 이력 (admin)

백그라운드 submit은 worker가 수행한다 — 이 API는 DB job 원장만 만지고
open-kknaks를 직접 호출하지 않는다 (ARCH-001 §4).
에러봉투 {detail:{error_code,message}} — SPEC-007 Case Matrix.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin_only
from app.api.errors import SPEC007_ERRORS, spec007_http_error
from app.dtos.user import UserDTO
from app.models.ai_queue import AiQueueJob
from app.schemas.classification import (
    ClassificationJobListResponse,
    ClassificationJobOut,
)
from app.services.ai_jobs import AiJobsService

router = APIRouter(tags=["classification"])


def _job_out(job: AiQueueJob) -> ClassificationJobOut:
    return ClassificationJobOut(
        id=job.id,
        job_type=job.job_type,  # type: ignore[arg-type]
        status=job.status,  # type: ignore[arg-type]
        document_id=job.document_id,
        candidate_id=job.candidate_id,
        drive_file_id=job.drive_file_id,
        fingerprint=job.fingerprint,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        external_task_id=job.external_task_id,
        last_error_code=job.last_error_code,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post(
    "/admin/documents/{document_id}/classify",
    response_model=ClassificationJobOut,
    status_code=202,
)
async def classify_document(
    document_id: int,
    admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> ClassificationJobOut:
    """수동 분류/재분석 — SPEC-005 candidate reanalyze가 위임되는 지점."""
    service = AiJobsService(session)
    try:
        job = await service.enqueue_reanalysis(document_id, requested_by=admin.id)
    except SPEC007_ERRORS as exc:
        raise spec007_http_error(exc)
    await session.commit()
    return _job_out(job)


@router.get(
    "/admin/classification-jobs/{job_id}", response_model=ClassificationJobOut
)
async def get_classification_job(
    job_id: int,
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> ClassificationJobOut:
    try:
        job = await AiJobsService(session).get_job(job_id)
    except SPEC007_ERRORS as exc:
        raise spec007_http_error(exc)
    return _job_out(job)


@router.get(
    "/admin/documents/{document_id}/classification-jobs",
    response_model=ClassificationJobListResponse,
)
async def list_document_classification_jobs(
    document_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> ClassificationJobListResponse:
    try:
        jobs, total = await AiJobsService(session).list_document_jobs(
            document_id, limit=limit, offset=offset
        )
    except SPEC007_ERRORS as exc:
        raise spec007_http_error(exc)
    return ClassificationJobListResponse(
        jobs=[_job_out(j) for j in jobs], total=total, limit=limit, offset=offset
    )
