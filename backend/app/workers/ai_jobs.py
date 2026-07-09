"""AI classification job worker — WORK-004 (SPEC-007, ARCH-002).

queued job pick → 제출 전 fingerprint 재검사 → Classification Input 조립 →
open-kknaks task submit → 결과 수신 → 검증/resolve/candidate 저장.

- 상태 원장은 DB(ai_queue_jobs) — Redis(open-kknaks broker)가 죽어도 row로
  상태/재시도를 추적한다.
- OPEN_KKNAKS_* env 미설정이면 job을 소모하지 않고 queued 유지 + next_run_at
  지연 (OPEN_KKNAKS_NOT_CONFIGURED). provider 오류는 failed로 기록한다.
- job 단위 실패는 격리 — worker tick은 죽지 않는다.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.integrations.google_drive import GoogleDriveClient
from app.integrations.open_kknaks import (
    ClassificationTaskClient,
    OpenKknaksNotConfiguredError,
    OpenKknaksProviderInvalidError,
    build_task_client,
)
from app.repos.ai_jobs import AiQueueJobRepository
from app.repos.documents import DocumentRepository
from app.services.ai_jobs import AiJobsService, fingerprint_key
from app.services.classification import ClassificationService
from app.services.drive_sync import build_drive_client

logger = logging.getLogger(__name__)

# env 미설정 시 재확인 지연 (queued 유지 — attempt 미소모).
NOT_CONFIGURED_DEFER_SEC = 60


async def run_ai_jobs_once(
    session_factory: async_sessionmaker,
    settings: Settings,
    *,
    client_factory=None,
    drive_client: GoogleDriveClient | None = None,
) -> int:
    """1 tick: due queued job 배치 처리. 처리한 job 수를 반환한다."""
    client_factory = client_factory or build_task_client

    async with session_factory() as session:
        jobs = AiJobsService(session, settings=settings)
        due = await jobs.pick_due_jobs(limit=settings.ai_jobs_batch_size)
        job_ids = [job.id for job in due]

    processed = 0
    for job_id in job_ids:
        try:
            await process_job(
                session_factory,
                settings,
                job_id,
                client_factory=client_factory,
                drive_client=drive_client,
            )
            processed += 1
        except Exception:  # job 단위 격리
            logger.exception("ai job processing failed: job_id=%s", job_id)
    return processed


async def process_job(
    session_factory: async_sessionmaker,
    settings: Settings,
    job_id: int,
    *,
    client_factory,
    drive_client: GoogleDriveClient | None = None,
) -> None:
    # ── phase A: 제출 전 검사 + running 전이 ─────────────────────────────
    async with session_factory() as session:
        repo = AiQueueJobRepository(session)
        jobs = AiJobsService(session, settings=settings)
        job = await repo.get(job_id)
        if job is None or job.status != "queued":
            return

        docs = DocumentRepository(session)
        document = await docs.get(job.document_id)

        # unavailable 문서 — 자동 재분석 제외, 재enqueue 없이 stale 종료.
        if document is None or document.drive_state != "active":
            await jobs.mark_stale(job, reason="document unavailable")
            await session.commit()
            logger.info("ai job %s stale: document unavailable", job.id)
            return

        # 제출 전 fingerprint 재검사 — 다르면 stale 종료 + 최신 기준 새 job.
        if fingerprint_key(document.drive_fingerprint) != job.fingerprint:
            _, new_job = await jobs.mark_stale(
                job,
                reason="fingerprint changed before submit",
                reenqueue_fingerprint=document.drive_fingerprint,
            )
            await session.commit()
            logger.info(
                "ai job %s stale before submit (new job: %s)",
                job.id,
                new_job.id if new_job else None,
            )
            return

        # open-kknaks env 검증 — 미설정이면 queued 유지 + 지연.
        try:
            client: ClassificationTaskClient = client_factory(settings)
        except OpenKknaksNotConfiguredError:
            await jobs.defer(
                job,
                error_code="OPEN_KKNAKS_NOT_CONFIGURED",
                delay_sec=NOT_CONFIGURED_DEFER_SEC,
            )
            await session.commit()
            logger.warning("open-kknaks not configured — job %s deferred", job.id)
            return
        except OpenKknaksProviderInvalidError as exc:
            await jobs.mark_running(job, provider=None, model=None)
            job = await jobs.mark_failed(job, message=f"unsupported provider: {exc}")
            job.last_error_code = "OPEN_KKNAKS_PROVIDER_INVALID"
            await session.commit()
            logger.warning("open-kknaks provider invalid — job %s failed", job.id)
            return

        await jobs.mark_running(
            job,
            provider=settings.open_kknaks_provider,
            model=settings.open_kknaks_model,
        )

        # Classification Input 조립 (secret/OAuth token 미포함).
        classification = ClassificationService(
            session,
            settings=settings,
            drive_client=drive_client or _default_drive_client(settings),
        )
        analysis_text = await classification.extract_analysis_text(document)
        input_payload = await classification.build_input(
            document, analysis_text=analysis_text
        )
        read_capability = input_payload["read_capability"]
        prompt = classification.build_prompt(input_payload)
        await session.commit()

    # ── phase B: submit + 결과 대기 (세션 밖 — 긴 I/O) ────────────────────
    try:
        task_id = await client.submit(prompt)
    except Exception as exc:
        async with session_factory() as session:
            jobs = AiJobsService(session, settings=settings)
            job = await AiQueueJobRepository(session).get(job_id)
            if job is not None and job.status == "running":
                await jobs.mark_failed(job, message=f"submit failed: {exc}")
                await session.commit()
        await _close_quietly(client)
        return

    async with session_factory() as session:
        job = await AiQueueJobRepository(session).get(job_id)
        if job is not None:
            job.external_task_id = task_id
            await session.commit()

    try:
        outcome = await client.wait(task_id)
    except Exception as exc:
        async with session_factory() as session:
            jobs = AiJobsService(session, settings=settings)
            job = await AiQueueJobRepository(session).get(job_id)
            if job is not None and job.status == "running":
                await jobs.mark_failed(job, message=f"result wait failed: {exc}")
                await session.commit()
        return
    finally:
        await _close_quietly(client)

    # ── phase C: 결과 반영 ────────────────────────────────────────────────
    async with session_factory() as session:
        jobs = AiJobsService(session, settings=settings)
        job = await AiQueueJobRepository(session).get(job_id)
        if job is None or job.status != "running":
            return

        if outcome.status == "failed":
            await jobs.mark_failed(job, message=outcome.error or "task failed")
            await session.commit()
            return
        if outcome.status == "timeout":
            await jobs.mark_timeout(job, message=outcome.error or "task timeout")
            await session.commit()
            return

        await jobs.mark_succeeded(job)
        classification = ClassificationService(session, settings=settings)
        result = await classification.process_result(
            job, outcome.result_text, read_capability=read_capability
        )
        await session.commit()
        logger.info(
            "ai job %s finished: %s (candidate=%s)",
            job.id,
            result.status,
            result.candidate.id if result.candidate else None,
        )


def _default_drive_client(settings: Settings) -> GoogleDriveClient | None:
    client = build_drive_client(settings)
    return client if client.configured else None


async def _close_quietly(client: ClassificationTaskClient) -> None:
    try:
        await client.close()
    except Exception:
        logger.debug("task client close failed", exc_info=True)
