"""AI classification job 원장 service — WORK-004 (SPEC-007, ARCH-002).

- 상태 SoT는 PostgreSQL `ai_queue_jobs`. Redis는 open-kknaks dispatch 전용.
- 멱등 enqueue: (job_type, document_id, fingerprint, idempotency_key) unique.
  자동 enqueue는 idempotency_key="auto" — 같은 기준 재호출 시 기존 job 반환.
- 상태 전이는 ARCH-002 §5 state machine만 허용한다. terminal:
  candidate_saved / stale. failed/timeout은 수동 재시도, validation_failed는
  자동 재분석으로 queued 복귀 (attempt_count < max_attempts 게이트).
- fingerprint는 canonical JSON 문자열로 저장한다 (dict 비교 순서 무관).
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.ai_queue import AiQueueJob
from app.repos.ai_jobs import AiQueueJobRepository
from app.repos.documents import DocumentRepository

# 자동(파이프라인) enqueue 공용 idempotency key — 같은 doc+fingerprint 중복 방지.
AUTO_IDEMPOTENCY_KEY = "auto"

DEFAULT_MAX_ATTEMPTS = 3

# ARCH-002 §5 state machine (SPEC-007 State / Lifecycle + queued→stale 보강).
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "queued": ("running", "stale"),
    "running": ("succeeded", "failed", "timeout"),
    "succeeded": ("candidate_saved", "validation_failed", "stale"),
    "validation_failed": ("queued",),
    "failed": ("queued",),
    "timeout": ("queued",),
    "candidate_saved": (),
    "stale": (),
}

TERMINAL_STATUSES = ("candidate_saved", "stale")


class ClassificationJobNotFoundError(Exception):
    """CLASSIFICATION_JOB_NOT_FOUND — job 없음."""


class DocumentUnavailableError(Exception):
    """DOCUMENT_UNAVAILABLE — trashed/removed/out_of_scope 문서는 분석 제외."""


class ClassificationRetryExhaustedError(Exception):
    """CLASSIFICATION_RETRY_EXHAUSTED — attempt_count >= max_attempts."""


class InvalidJobTransitionError(Exception):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"invalid job transition: {current} -> {target}")
        self.current = current
        self.target = target


def fingerprint_key(fingerprint: dict | str) -> str:
    """composite fingerprint dict → canonical 비교/저장 문자열.

    `version`은 비교에서 제외한다 — Drive가 내용 변경 없이 자체 인덱싱으로
    version을 올리는 churn이 있어 불필요한 stale 재분석을 유발한다 (2026-07-09
    실검증 배치에서 관측). mirror의 fingerprint dict에는 version이 보관되지만
    stale 판정 축은 file_id/modified_time/name/mime(/content)이다 (DEC-023).
    """
    if isinstance(fingerprint, str):
        return fingerprint
    comparable = {k: v for k, v in fingerprint.items() if k != "version"}
    return json.dumps(
        comparable, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class AiJobsService:
    def __init__(
        self, session: AsyncSession, *, settings: Settings | None = None
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._jobs = AiQueueJobRepository(session)
        self._docs = DocumentRepository(session)

    # ------------------------------------------------------------------
    # enqueue (멱등)
    # ------------------------------------------------------------------

    async def _enqueue(
        self,
        *,
        job_type: str,
        document_id: int,
        fingerprint: dict | str,
        idempotency_key: str,
        created_by: int | None = None,
    ) -> tuple[AiQueueJob, bool]:
        document = await self._docs.get(document_id)
        if document is None:
            raise ClassificationJobNotFoundError(
                f"document not found: {document_id}"
            )
        if document.drive_state != "active":
            # unavailable 문서는 자동 재분석 제외 (WORK-004 Scope).
            raise DocumentUnavailableError(document.drive_state)

        key = fingerprint_key(fingerprint)
        existing = await self._jobs.get_by_idempotency(
            job_type=job_type,
            document_id=document_id,
            fingerprint=key,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing, False
        row = await self._jobs.create(
            job_type=job_type,
            document_id=document_id,
            drive_file_id=document.drive_file_id,
            fingerprint=key,
            idempotency_key=idempotency_key,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            created_by=created_by,
        )
        return row, True

    async def enqueue_classification(
        self,
        document_id: int,
        fingerprint: dict | str,
        *,
        requested_by: int | None = None,
    ) -> tuple[AiQueueJob, bool]:
        """document upsert 훅 — 같은 doc+fingerprint 재호출 시 기존 job 반환."""
        return await self._enqueue(
            job_type="classification",
            document_id=document_id,
            fingerprint=fingerprint,
            idempotency_key=AUTO_IDEMPOTENCY_KEY,
            created_by=requested_by,
        )

    async def enqueue_stale_reanalysis(
        self,
        document_id: int,
        fingerprint: dict | str,
        *,
        requested_by: int | None = None,
    ) -> tuple[AiQueueJob, bool]:
        """fingerprint 변경(stale) 자동 재분석 훅 (DEC-022)."""
        return await self._enqueue(
            job_type="stale_reanalysis",
            document_id=document_id,
            fingerprint=fingerprint,
            idempotency_key=AUTO_IDEMPOTENCY_KEY,
            created_by=requested_by,
        )

    async def enqueue_reanalysis(
        self, document_id: int, *, requested_by: int | None = None
    ) -> AiQueueJob:
        """수동 분류/재분석 — POST /admin/documents/{id}/classify.

        SPEC-005 candidate reanalyze CTA가 이 지점으로 위임된다.
        - 진행 중 job이 있으면 그대로 반환 (멱등).
        - failed/timeout/validation_failed job이 있으면 queued로 복귀 (수동 재시도).
        - 재시도 소진(attempt >= max) job은 terminal로 취급 — 새 manual job을 만든다
          (실검증 이슈: 소진 상태에서 수동 재분석이 409로 막히던 버그 수정).
        - terminal(candidate_saved/stale)뿐이면 새 job을 만든다.
        """
        from app.services.documents import DocumentNotFoundError

        document = await self._docs.get(document_id)
        if document is None:
            raise DocumentNotFoundError
        if document.drive_state != "active":
            raise DocumentUnavailableError(document.drive_state)

        key = fingerprint_key(document.drive_fingerprint)
        active = await self._jobs.find_active_for_fingerprint(
            document_id=document_id, fingerprint=key
        )
        if active is not None:
            return active

        retryable = await self._jobs.find_retryable_for_fingerprint(
            document_id=document_id, fingerprint=key
        )
        if retryable is not None and retryable.attempt_count < retryable.max_attempts:
            return await self.retry(retryable)
        # 소진된 retryable은 아래 manual 신규 job 생성으로 진행한다.

        row, _ = await self._enqueue(
            job_type="classification",
            document_id=document_id,
            fingerprint=key,
            idempotency_key=f"manual-{uuid.uuid4().hex[:12]}",
            created_by=requested_by,
        )
        return row

    # ------------------------------------------------------------------
    # 상태 전이 (ARCH-002 §5)
    # ------------------------------------------------------------------

    def _transition(self, job: AiQueueJob, target: str) -> None:
        if target not in ALLOWED_TRANSITIONS.get(job.status, ()):
            raise InvalidJobTransitionError(job.status, target)
        job.status = target

    async def retry(self, job: AiQueueJob) -> AiQueueJob:
        """failed/timeout/validation_failed → queued. attempt 게이트 적용."""
        if job.attempt_count >= job.max_attempts:
            raise ClassificationRetryExhaustedError
        self._transition(job, "queued")
        job.next_run_at = None
        job.finished_at = None
        await self._session.flush()
        return job

    async def mark_running(
        self, job: AiQueueJob, *, provider: str | None, model: str | None
    ) -> AiQueueJob:
        self._transition(job, "running")
        job.attempt_count += 1
        job.started_at = _now()
        job.provider = provider
        job.model = model
        await self._session.flush()
        return job

    async def mark_succeeded(self, job: AiQueueJob) -> AiQueueJob:
        """open-kknaks 결과 수신 — 검증 진행 상태."""
        self._transition(job, "succeeded")
        await self._session.flush()
        return job

    async def mark_candidate_saved(
        self, job: AiQueueJob, *, candidate_id: int
    ) -> AiQueueJob:
        self._transition(job, "candidate_saved")
        job.candidate_id = candidate_id
        job.finished_at = _now()
        job.last_error_code = None
        job.last_error_message = None
        await self._session.flush()
        return job

    async def mark_validation_failed(
        self, job: AiQueueJob, *, message: str
    ) -> AiQueueJob:
        """CLASSIFICATION_RESULT_INVALID — attempts 남으면 자동 queued 복귀."""
        self._transition(job, "validation_failed")
        job.last_error_code = "CLASSIFICATION_RESULT_INVALID"
        job.last_error_message = message[:500]
        job.finished_at = _now()
        await self._session.flush()
        if job.attempt_count < job.max_attempts:
            self._transition(job, "queued")
            job.next_run_at = _now() + dt.timedelta(
                seconds=self._settings.ai_jobs_retry_backoff_sec * job.attempt_count
            )
            job.finished_at = None
            await self._session.flush()
        return job

    async def mark_failed(self, job: AiQueueJob, *, message: str) -> AiQueueJob:
        self._transition(job, "failed")
        job.last_error_code = "CLASSIFICATION_TASK_FAILED"
        job.last_error_message = message[:500]
        job.finished_at = _now()
        await self._session.flush()
        return job

    async def mark_timeout(self, job: AiQueueJob, *, message: str) -> AiQueueJob:
        self._transition(job, "timeout")
        job.last_error_code = "CLASSIFICATION_TIMEOUT"
        job.last_error_message = message[:500]
        job.finished_at = _now()
        await self._session.flush()
        return job

    async def mark_stale(
        self,
        job: AiQueueJob,
        *,
        reason: str,
        reenqueue_fingerprint: dict | str | None = None,
    ) -> tuple[AiQueueJob, AiQueueJob | None]:
        """fingerprint mismatch 폐기 (terminal).

        reenqueue_fingerprint가 주어지면 최신 기준 stale_reanalysis job을
        자동 enqueue한다 (DEC-022). 문서가 unavailable이면 재enqueue 없이 종료.
        """
        self._transition(job, "stale")
        job.last_error_code = "CLASSIFICATION_FINGERPRINT_STALE"
        job.last_error_message = reason[:500]
        job.finished_at = _now()
        await self._session.flush()

        new_job: AiQueueJob | None = None
        if reenqueue_fingerprint is not None:
            try:
                new_job, _ = await self.enqueue_stale_reanalysis(
                    job.document_id, reenqueue_fingerprint
                )
            except DocumentUnavailableError:
                new_job = None
        return job, new_job

    async def defer(
        self, job: AiQueueJob, *, error_code: str, delay_sec: int
    ) -> AiQueueJob:
        """설정 미비 등 — queued 유지, next_run_at만 미룬다 (attempt 미소모)."""
        job.last_error_code = error_code
        job.next_run_at = _now() + dt.timedelta(seconds=delay_sec)
        await self._session.flush()
        return job

    # ------------------------------------------------------------------
    # 조회 (FE 폴링 원천 — ARCH-002 §6)
    # ------------------------------------------------------------------

    async def get_job(self, job_id: int) -> AiQueueJob:
        job = await self._jobs.get(job_id)
        if job is None:
            raise ClassificationJobNotFoundError(str(job_id))
        return job

    async def list_document_jobs(
        self, document_id: int, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[AiQueueJob], int]:
        from app.services.documents import DocumentNotFoundError

        document = await self._docs.get(document_id)
        if document is None:
            raise DocumentNotFoundError
        return await self._jobs.list_by_document(
            document_id, limit=limit, offset=offset
        )

    async def pick_due_jobs(self, *, limit: int) -> list[AiQueueJob]:
        return await self._jobs.list_due_queued(now=_now(), limit=limit)
