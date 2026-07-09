"""SPEC-007 classification job API schema — WORK-004.

FE는 Redis가 아니라 이 DB job 상태 응답을 폴링한다 (ARCH-002 §6).
응답 최소 필드는 ARCH-002 §6 API Polling Contract를 따른다.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel

JobType = Literal["classification", "stale_reanalysis"]
JobStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "candidate_saved",
    "validation_failed",
    "failed",
    "timeout",
    "stale",
]


class ClassificationJobOut(BaseModel):
    id: int
    job_type: JobType
    status: JobStatus
    document_id: int
    candidate_id: int | None = None
    drive_file_id: str
    fingerprint: str
    attempt_count: int
    max_attempts: int
    external_task_id: str | None = None
    last_error_code: str | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ClassificationJobListResponse(BaseModel):
    jobs: list[ClassificationJobOut]
    total: int
    limit: int
    offset: int
