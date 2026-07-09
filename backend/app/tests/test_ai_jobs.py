"""ai_queue_jobs 원장 service 테스트 — WORK-004 Phase 1 (SPEC-007, ARCH-002).

- 멱등 enqueue (같은 document_id+fingerprint → row 1개)
- WORK-003 훅 배선 (upsert → classification, fingerprint 변경 → stale_reanalysis)
- unavailable 문서 자동 재분석 제외
- state machine 전이 전부 + retry 게이트 (attempt_count/max_attempts)
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_queue import AiQueueJob
from app.models.candidate import MetadataCandidate
from app.models.document import Document
from app.models.drive_sync import DriveSyncEvent
from app.services.ai_jobs import (
    AiJobsService,
    ClassificationRetryExhaustedError,
    DocumentUnavailableError,
    InvalidJobTransitionError,
    fingerprint_key,
)
from app.tests.drive_fakes import FakeDrive, change_for, make_file, make_sync_service


async def _make_document(
    session: AsyncSession,
    *,
    drive_file_id: str = "f-ai-1",
    drive_state: str = "active",
    name: str = "테스트.pdf",
) -> Document:
    row = Document(
        source_provider="google_drive",
        drive_file_id=drive_file_id,
        drive_name=name,
        drive_mime_type="application/pdf",
        drive_state=drive_state,
        drive_fingerprint={
            "drive_file_id": drive_file_id,
            "drive_modified_time": "2026-07-08T10:00:00+00:00",
            "drive_name": name,
            "mime_type": "application/pdf",
        },
    )
    session.add(row)
    await session.flush()
    return row


async def _make_candidate(session: AsyncSession, document: Document) -> int:
    row = MetadataCandidate(
        document_id=document.id,
        state="pending",
        read_capability="content_read",
        candidate_metadata={"document_type": "회의록"},
        candidate_fingerprint=document.drive_fingerprint,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _jobs(session: AsyncSession) -> list[AiQueueJob]:
    return list(
        await session.scalars(sa.select(AiQueueJob).order_by(AiQueueJob.id))
    )


# ----------------------------------------------------------------------
# 멱등 enqueue
# ----------------------------------------------------------------------


async def test_enqueue_classification_is_idempotent(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)

    job1, created1 = await service.enqueue_classification(
        doc.id, doc.drive_fingerprint
    )
    job2, created2 = await service.enqueue_classification(
        doc.id, doc.drive_fingerprint
    )
    await db_session.commit()

    assert created1 is True and created2 is False
    assert job1.id == job2.id  # 같은 doc+fingerprint → row 1개 (ARCH-002 AC)
    rows = await _jobs(db_session)
    assert len(rows) == 1
    assert rows[0].status == "queued"
    assert rows[0].job_type == "classification"
    assert rows[0].drive_file_id == doc.drive_file_id
    assert rows[0].fingerprint == fingerprint_key(doc.drive_fingerprint)


async def test_enqueue_different_fingerprint_creates_new_job(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)
    await service.enqueue_classification(doc.id, doc.drive_fingerprint)
    await service.enqueue_classification(
        doc.id, {**doc.drive_fingerprint, "drive_modified_time": "2026-07-09T09:00:00+00:00"}
    )
    assert len(await _jobs(db_session)) == 2


async def test_enqueue_rejects_unavailable_document(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session, drive_state="trashed")
    service = AiJobsService(db_session)
    with pytest.raises(DocumentUnavailableError):
        await service.enqueue_classification(doc.id, doc.drive_fingerprint)
    with pytest.raises(DocumentUnavailableError):
        await service.enqueue_reanalysis(doc.id)


# ----------------------------------------------------------------------
# WORK-003 훅 배선 (drive sync → enqueue)
# ----------------------------------------------------------------------


async def test_document_upsert_hook_enqueues_classification(
    db_session: AsyncSession,
) -> None:
    fake = FakeDrive()
    file = fake.add_file(make_file("f-hook-new"))
    service = make_sync_service(db_session, fake)

    await service.apply_change(change_for(file))
    await db_session.commit()

    rows = await _jobs(db_session)
    assert len(rows) == 1
    assert rows[0].job_type == "classification" and rows[0].status == "queued"

    # 같은 change 재적용 — 멱등 (job 중복 없음)
    await service.apply_change(change_for(file))
    await db_session.commit()
    assert len(await _jobs(db_session)) == 1


async def test_fingerprint_change_hook_enqueues_stale_reanalysis(
    db_session: AsyncSession,
) -> None:
    fake = FakeDrive()
    file = fake.add_file(make_file("f-hook-stale", version="1"))
    service = make_sync_service(db_session, fake)
    await service.apply_change(change_for(file))

    modified = dict(file, modifiedTime="2026-07-08T12:00:00.000Z", version="2")
    fake.add_file(modified)
    await service.apply_change(change_for(modified))
    await db_session.commit()

    rows = await _jobs(db_session)
    types = {(r.job_type, r.status) for r in rows}
    assert ("classification", "queued") in types
    assert ("stale_reanalysis", "queued") in types

    events = list(
        await db_session.scalars(
            sa.select(DriveSyncEvent).where(
                DriveSyncEvent.event_type == "reanalysis_enqueued"
            )
        )
    )
    assert len(events) == 1 and events[0].result == "success"


async def test_unavailable_transition_does_not_enqueue(
    db_session: AsyncSession,
) -> None:
    fake = FakeDrive()
    file = fake.add_file(make_file("f-hook-trash"))
    service = make_sync_service(db_session, fake)
    await service.apply_change(change_for(file))
    before = len(await _jobs(db_session))

    await service.apply_change(change_for(dict(file, trashed=True)))
    await db_session.commit()

    assert len(await _jobs(db_session)) == before  # trashed → enqueue 없음


# ----------------------------------------------------------------------
# state machine 전이 (ARCH-002 §5)
# ----------------------------------------------------------------------


async def test_full_lifecycle_to_candidate_saved(db_session: AsyncSession) -> None:
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)
    job, _ = await service.enqueue_classification(doc.id, doc.drive_fingerprint)

    await service.mark_running(job, provider="claude", model="m")
    assert job.status == "running" and job.attempt_count == 1
    assert job.started_at is not None

    await service.mark_succeeded(job)
    assert job.status == "succeeded"

    candidate_id = await _make_candidate(db_session, doc)
    await service.mark_candidate_saved(job, candidate_id=candidate_id)
    assert job.status == "candidate_saved"
    assert job.candidate_id == candidate_id
    assert job.finished_at is not None


async def test_invalid_transitions_raise(db_session: AsyncSession) -> None:
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)
    job, _ = await service.enqueue_classification(doc.id, doc.drive_fingerprint)

    candidate_id = await _make_candidate(db_session, doc)

    with pytest.raises(InvalidJobTransitionError):
        await service.mark_succeeded(job)  # queued → succeeded 불가
    with pytest.raises(InvalidJobTransitionError):
        await service.mark_candidate_saved(job, candidate_id=candidate_id)

    await service.mark_running(job, provider="claude", model="m")
    with pytest.raises(InvalidJobTransitionError):
        await service.retry(job)  # running → queued 불가

    await service.mark_succeeded(job)
    await service.mark_candidate_saved(job, candidate_id=candidate_id)
    with pytest.raises(InvalidJobTransitionError):
        await service.mark_running(job, provider="claude", model="m")  # terminal


async def test_failed_and_timeout_manual_retry(db_session: AsyncSession) -> None:
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)
    job, _ = await service.enqueue_classification(doc.id, doc.drive_fingerprint)

    await service.mark_running(job, provider="claude", model="m")
    await service.mark_failed(job, message="boom")
    assert job.status == "failed"
    assert job.last_error_code == "CLASSIFICATION_TASK_FAILED"

    await service.retry(job)  # 수동 재시도 → queued 복귀
    assert job.status == "queued" and job.next_run_at is None

    await service.mark_running(job, provider="claude", model="m")
    await service.mark_timeout(job, message="slow")
    assert job.status == "timeout"
    assert job.last_error_code == "CLASSIFICATION_TIMEOUT"


async def test_retry_gate_blocks_after_max_attempts(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)
    job, _ = await service.enqueue_classification(doc.id, doc.drive_fingerprint)

    for _ in range(job.max_attempts):
        await service.mark_running(job, provider="claude", model="m")
        await service.mark_failed(job, message="boom")
        if job.attempt_count < job.max_attempts:
            await service.retry(job)

    assert job.attempt_count == job.max_attempts
    with pytest.raises(ClassificationRetryExhaustedError):
        await service.retry(job)


async def test_validation_failed_auto_requeues_within_attempts(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)
    job, _ = await service.enqueue_classification(doc.id, doc.drive_fingerprint)

    await service.mark_running(job, provider="claude", model="m")
    await service.mark_succeeded(job)
    await service.mark_validation_failed(job, message="schema error")

    # 자동 재분석 — queued 복귀 + backoff 예약 (SPEC-007 lifecycle)
    assert job.status == "queued"
    assert job.last_error_code == "CLASSIFICATION_RESULT_INVALID"
    assert job.next_run_at is not None


async def test_validation_failed_stays_after_max_attempts(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)
    job, _ = await service.enqueue_classification(doc.id, doc.drive_fingerprint)
    job.max_attempts = 1

    await service.mark_running(job, provider="claude", model="m")
    await service.mark_succeeded(job)
    await service.mark_validation_failed(job, message="schema error")

    assert job.status == "validation_failed"  # attempts 소진 → 자동 복귀 없음


async def test_mark_stale_reenqueues_new_job(db_session: AsyncSession) -> None:
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)
    job, _ = await service.enqueue_classification(doc.id, doc.drive_fingerprint)

    new_fp = {**doc.drive_fingerprint, "drive_modified_time": "2026-07-09T09:30:00+00:00"}
    doc.drive_fingerprint = new_fp
    await db_session.flush()

    _, new_job = await service.mark_stale(
        job, reason="fingerprint changed", reenqueue_fingerprint=new_fp
    )

    assert job.status == "stale"
    assert job.last_error_code == "CLASSIFICATION_FINGERPRINT_STALE"
    assert new_job is not None
    assert new_job.job_type == "stale_reanalysis"
    assert new_job.fingerprint == fingerprint_key(new_fp)


# ----------------------------------------------------------------------
# 수동 재분석 (POST /admin/documents/{id}/classify 위임 표면)
# ----------------------------------------------------------------------


async def test_manual_reanalysis_reuses_active_job(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)
    job, _ = await service.enqueue_classification(doc.id, doc.drive_fingerprint)

    manual = await service.enqueue_reanalysis(doc.id)
    assert manual.id == job.id  # 진행 중 job 재사용 (멱등)


async def test_manual_reanalysis_requeues_failed_job(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)
    job, _ = await service.enqueue_classification(doc.id, doc.drive_fingerprint)
    await service.mark_running(job, provider="claude", model="m")
    await service.mark_failed(job, message="boom")

    manual = await service.enqueue_reanalysis(doc.id)
    assert manual.id == job.id and manual.status == "queued"


async def test_manual_reanalysis_creates_new_job_after_terminal(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)
    job, _ = await service.enqueue_classification(doc.id, doc.drive_fingerprint)
    await service.mark_running(job, provider="claude", model="m")
    await service.mark_succeeded(job)
    candidate_id = await _make_candidate(db_session, doc)
    await service.mark_candidate_saved(job, candidate_id=candidate_id)

    manual = await service.enqueue_reanalysis(doc.id)
    assert manual.id != job.id
    assert manual.status == "queued"
    assert manual.idempotency_key.startswith("manual-")


async def test_manual_reanalysis_after_retry_exhausted_creates_new_job(
    db_session: AsyncSession,
) -> None:
    """재시도 소진(validation_failed 3/3)된 job은 terminal 취급 — 수동 재분석이
    409가 아니라 새 manual job을 만든다 (2026-07-09 실검증 버그 회귀 테스트)."""
    doc = await _make_document(db_session)
    service = AiJobsService(db_session)
    job, _ = await service.enqueue_classification(doc.id, doc.drive_fingerprint)

    while True:
        await service.mark_running(job, provider="claude", model="m")
        await service.mark_succeeded(job)
        await service.mark_validation_failed(job, message="no JSON object in result")
        if job.status == "validation_failed":  # 자동 재큐 한도 소진
            break

    assert job.status == "validation_failed"
    assert job.attempt_count == job.max_attempts

    manual = await service.enqueue_reanalysis(doc.id)
    assert manual.id != job.id
    assert manual.status == "queued"
    assert manual.idempotency_key.startswith("manual-")


def test_fingerprint_key_ignores_drive_version_churn() -> None:
    """Drive 자체 version bump(내용 무변경)는 stale을 유발하지 않는다."""
    base = {
        "drive_file_id": "f1",
        "drive_modified_time": "2026-07-09T00:00:00+00:00",
        "drive_name": "a.md",
        "mime_type": "text/markdown",
        "version": "1",
    }
    bumped = dict(base, version="7")
    assert fingerprint_key(base) == fingerprint_key(bumped)

    changed = dict(base, drive_modified_time="2026-07-09T01:00:00+00:00")
    assert fingerprint_key(base) != fingerprint_key(changed)


def test_fingerprint_changed_ignores_version_only_diff() -> None:
    from app.services.documents import fingerprint_changed

    base = {"drive_file_id": "f1", "drive_name": "a", "mime_type": "m", "version": "1"}
    assert fingerprint_changed(base, dict(base, version="9")) is False
    assert fingerprint_changed(base, dict(base, drive_name="b")) is True
