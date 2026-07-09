"""classification worker/결과 처리 테스트 — WORK-004 Phase 2/3 (SPEC-007).

open-kknaks 실 실행 없이 ClassificationTaskClient fake로 검증한다:
- 제출 전/저장 전 fingerprint 이중 검사 (stale + 재enqueue)
- output schema validation 성공/실패 경로
- 부서/트리 노드 id resolve 성공/실패 표시
- pending 후보 1개 규칙 + candidate_fingerprint 멱등
- relation 후보 unresolved 저장 (문서 자동 생성 금지 — DEC-021)
- payload secret 미포함 / metadata_only / env 미설정 defer
"""

from __future__ import annotations

import json

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.integrations.google_drive import DriveClientConfig, GoogleDriveClient
from app.integrations.open_kknaks import TaskOutcome, build_task_client
from app.models.ai_queue import AiQueueJob
from app.models.candidate import MetadataCandidate, RelationCandidate
from app.models.document import Document
from app.models.organization import DocumentTreeNode, DocumentType, OrganizationNode
from app.services.ai_jobs import AiJobsService, fingerprint_key
from app.services.classification import ClassificationService
from app.tests.conftest import _Session
from app.workers.ai_jobs import run_ai_jobs_once

# ----------------------------------------------------------------------
# fixtures / helpers
# ----------------------------------------------------------------------

SETTINGS = Settings(
    open_kknaks_broker_url="redis://fake:6379/0",
    open_kknaks_provider="claude",
    open_kknaks_model="claude-test",
    ai_jobs_batch_size=5,
)

VALID_OUTPUT = {
    "document_type": "회의록",
    "created_department": "개발팀",
    "owning_department": "개발팀",
    "physical_tree_path": {
        "organization_path": ["Mediness", "개발팀"],
        "tree_path": ["주간회의"],
    },
    "related_departments": ["운영팀"],
    "related_products": ["mediness"],
    "summary": "주간 회의 내용 요약",
    "sensitivity": "normal",
    "policy_preset": None,
    "read_policy": {
        "read_roles": ["member"],
        "read_departments": ["개발팀"],
        "read_positions": [],
        "access_logic": "ANY",
    },
    "relation_candidates": [
        {
            "raw_label": "[[지난주 회의록.pdf]]",
            "relation_type": "references",
            "target_hint": None,
        }
    ],
    "confidence": 0.86,
    "reasons": ["파일명이 회의록 패턴"],
}


class FakeTaskClient:
    """ClassificationTaskClient fake — 결과 시나리오 주입."""

    def __init__(
        self, outcome_factory, *, on_wait=None
    ) -> None:
        self._outcome_factory = outcome_factory
        self._on_wait = on_wait
        self.submitted_prompts: list[str] = []
        self.closed = False

    async def submit(self, prompt: str) -> str:
        self.submitted_prompts.append(prompt)
        return f"task-{len(self.submitted_prompts)}"

    async def wait(self, task_id: str) -> TaskOutcome:
        if self._on_wait is not None:
            await self._on_wait()
        return self._outcome_factory(task_id)

    async def close(self) -> None:
        self.closed = True


def succeeded_client(output: dict | str, *, on_wait=None) -> FakeTaskClient:
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    return FakeTaskClient(
        lambda task_id: TaskOutcome(
            status="succeeded", task_id=task_id, result_text=text
        ),
        on_wait=on_wait,
    )


async def _make_document(
    session: AsyncSession,
    *,
    drive_file_id: str = "f-cls-1",
    name: str = "주간회의록.pdf",
    mime: str = "application/pdf",
    drive_state: str = "active",
) -> Document:
    row = Document(
        source_provider="google_drive",
        drive_file_id=drive_file_id,
        drive_name=name,
        drive_mime_type=mime,
        drive_state=drive_state,
        drive_fingerprint={
            "drive_file_id": drive_file_id,
            "drive_modified_time": "2026-07-08T10:00:00+00:00",
            "drive_name": name,
            "mime_type": mime,
        },
    )
    session.add(row)
    await session.flush()
    return row


async def _seed_org_tree(session: AsyncSession) -> dict[str, int]:
    """조직/트리/카탈로그 최소 seed — resolve 대상 노드 (WORK-002 데이터)."""
    company = OrganizationNode(type="company", name="Mediness", status="active")
    session.add(company)
    await session.flush()
    dev = OrganizationNode(
        type="department", name="개발팀", parent_id=company.id, status="active"
    )
    ops = OrganizationNode(
        type="department", name="운영팀", parent_id=company.id, status="active"
    )
    session.add_all([dev, ops])
    await session.flush()
    doc_type = DocumentType(name="회의록", normalized_name="회의록")
    session.add(doc_type)
    await session.flush()
    work = DocumentTreeNode(
        organization_node_id=dev.id,
        parent_id=None,
        type="work",
        document_type_id=None,
        name="주간회의",
        status="active",
    )
    session.add(work)
    await session.flush()
    return {
        "company": company.id,
        "dev": dev.id,
        "ops": ops.id,
        "doc_type": doc_type.id,
        "work": work.id,
    }


async def _enqueue(session: AsyncSession, document: Document) -> AiQueueJob:
    job, _ = await AiJobsService(session, settings=SETTINGS).enqueue_classification(
        document.id, document.drive_fingerprint
    )
    await session.commit()
    return job


async def _get_job(session: AsyncSession, job_id: int) -> AiQueueJob:
    # worker는 별도 세션에서 갱신한다 — 테스트 세션 캐시를 무시하고 재조회.
    row = await session.get(AiQueueJob, job_id)
    await session.refresh(row)
    return row


async def _pending_candidates(
    session: AsyncSession, document_id: int
) -> list[MetadataCandidate]:
    return list(
        await session.scalars(
            sa.select(MetadataCandidate)
            .where(
                MetadataCandidate.document_id == document_id,
                MetadataCandidate.state == "pending",
            )
            .execution_options(populate_existing=True)
        )
    )


# ----------------------------------------------------------------------
# happy path — queued → running → succeeded → candidate_saved
# ----------------------------------------------------------------------


async def test_worker_happy_path_saves_pending_candidate(
    db_session: AsyncSession,
) -> None:
    ids = await _seed_org_tree(db_session)
    doc = await _make_document(db_session)
    other = await _make_document(
        db_session, drive_file_id="f-cls-prev", name="지난주 회의록.pdf"
    )
    job = await _enqueue(db_session, doc)

    client = succeeded_client(VALID_OUTPUT)
    processed = await run_ai_jobs_once(
        _Session, SETTINGS, client_factory=lambda s: client
    )
    assert processed >= 1

    refreshed = await _get_job(db_session, job.id)
    assert refreshed.status == "candidate_saved"
    assert refreshed.external_task_id == "task-1"
    assert refreshed.provider == "claude" and refreshed.model == "claude-test"
    assert refreshed.attempt_count == 1
    assert client.closed is True

    pending = await _pending_candidates(db_session, doc.id)
    assert len(pending) == 1
    candidate = pending[0]
    assert refreshed.candidate_id == candidate.id
    assert candidate.read_capability == "metadata_only"  # pdf → 본문 추출 보류
    meta = candidate.candidate_metadata
    assert meta["document_type"] == "회의록"
    resolution = meta["resolution"]
    # 노드 id resolve 성공 (SPEC-007 Validation — id 기반 저장)
    assert resolution["owning_department_node_id"] == ids["dev"]
    assert resolution["document_type_id"] == ids["doc_type"]
    assert resolution["document_type_is_new"] is False
    assert resolution["organization_path_node_ids"] == [ids["company"], ids["dev"]]
    assert resolution["tree_path_node_ids"] == [ids["work"]]
    assert resolution["read_department_node_ids"] == [ids["dev"]]
    assert resolution["unresolved_fields"] == []
    assert resolution["needs_admin_fix"] is False

    # relation 후보 — target 존재 → pending + target id (DEC-021)
    relations = list(
        await db_session.scalars(
            sa.select(RelationCandidate).where(
                RelationCandidate.source_document_id == doc.id
            )
        )
    )
    assert len(relations) == 1
    assert relations[0].state == "pending"
    assert relations[0].target_document_id == other.id


async def test_worker_prompt_contains_no_secrets(db_session: AsyncSession) -> None:
    await _seed_org_tree(db_session)
    doc = await _make_document(db_session, drive_file_id="f-cls-sec")
    await _enqueue(db_session, doc)

    settings = Settings(
        open_kknaks_broker_url="redis://fake:6379/0",
        open_kknaks_model="claude-test",
        google_drive_client_id="secret-client-id",
        google_drive_client_secret="secret-client-secret",
        google_drive_refresh_token="secret-refresh-token",
        google_drive_selected_folder_id="folder-1",
        jwt_secret="secret-jwt",
        database_url="postgresql+asyncpg://user:dbpass@host/db",
    )
    client = succeeded_client(VALID_OUTPUT)
    # drive_client 미주입 + pdf mime → 본문 추출 시도 없이 metadata_only.
    await run_ai_jobs_once(_Session, settings, client_factory=lambda s: client)

    assert len(client.submitted_prompts) == 1
    prompt = client.submitted_prompts[0]
    for secret in (
        "secret-client-id",
        "secret-client-secret",
        "secret-refresh-token",
        "secret-jwt",
        "dbpass",
    ):
        assert secret not in prompt  # SPEC-007 AC — payload에 secret 미포함
    assert '"read_capability": "metadata_only"' in prompt
    assert '"analysis_text": null' in prompt


# ----------------------------------------------------------------------
# fingerprint 이중 검사 (제출 전 / 저장 전)
# ----------------------------------------------------------------------


async def test_worker_stale_before_submit_skips_and_reenqueues(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session, drive_file_id="f-cls-stale1")
    job = await _enqueue(db_session, doc)

    doc.drive_fingerprint = {**doc.drive_fingerprint, "drive_modified_time": "2026-07-09T09:00:00+00:00"}
    await db_session.commit()

    client = succeeded_client(VALID_OUTPUT)
    await run_ai_jobs_once(_Session, SETTINGS, client_factory=lambda s: client)

    refreshed = await _get_job(db_session, job.id)
    assert refreshed.status == "stale"
    assert refreshed.last_error_code == "CLASSIFICATION_FINGERPRINT_STALE"
    assert client.submitted_prompts == []  # 제출 자체를 하지 않는다

    new_jobs = list(
        await db_session.scalars(
            sa.select(AiQueueJob).where(
                AiQueueJob.document_id == doc.id,
                AiQueueJob.job_type == "stale_reanalysis",
                AiQueueJob.status == "queued",
            )
        )
    )
    assert len(new_jobs) == 1  # 최신 fingerprint 기준 새 job (DEC-022)
    assert new_jobs[0].fingerprint == fingerprint_key(doc.drive_fingerprint)


async def test_worker_stale_at_save_discards_candidate(
    db_session: AsyncSession,
) -> None:
    await _seed_org_tree(db_session)
    doc = await _make_document(db_session, drive_file_id="f-cls-stale2")
    job = await _enqueue(db_session, doc)

    async def mutate_fingerprint() -> None:
        # task 실행 중 Drive 변경 시뮬레이션 — 저장 직전 fingerprint 상이.
        async with _Session() as session:
            row = await session.get(Document, doc.id)
            row.drive_fingerprint = {**row.drive_fingerprint, "drive_modified_time": "2026-07-09T09:30:00+00:00"}
            await session.commit()

    client = succeeded_client(VALID_OUTPUT, on_wait=mutate_fingerprint)
    await run_ai_jobs_once(_Session, SETTINGS, client_factory=lambda s: client)

    refreshed = await _get_job(db_session, job.id)
    assert refreshed.status == "stale"
    assert await _pending_candidates(db_session, doc.id) == []  # 미저장 (AC)

    new_jobs = list(
        await db_session.scalars(
            sa.select(AiQueueJob).where(
                AiQueueJob.document_id == doc.id,
                AiQueueJob.status == "queued",
            )
        )
    )
    assert len(new_jobs) == 1


# ----------------------------------------------------------------------
# schema validation 실패 / task 실패 / timeout
# ----------------------------------------------------------------------


async def test_worker_invalid_output_marks_validation_failed_and_requeues(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session, drive_file_id="f-cls-invalid")
    job = await _enqueue(db_session, doc)

    client = succeeded_client("this is not json at all")
    await run_ai_jobs_once(_Session, SETTINGS, client_factory=lambda s: client)

    refreshed = await _get_job(db_session, job.id)
    # invalid → validation_failed 기록 후 자동 재분석 queued 복귀 (attempts 내)
    assert refreshed.status == "queued"
    assert refreshed.last_error_code == "CLASSIFICATION_RESULT_INVALID"
    assert refreshed.next_run_at is not None
    assert await _pending_candidates(db_session, doc.id) == []  # candidate 미저장


async def test_worker_schema_violation_is_validation_failed(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session, drive_file_id="f-cls-schema")
    job = await _enqueue(db_session, doc)

    bad = {**VALID_OUTPUT, "sensitivity": "top-secret"}  # enum 위반
    client = succeeded_client(bad)
    await run_ai_jobs_once(_Session, SETTINGS, client_factory=lambda s: client)

    refreshed = await _get_job(db_session, job.id)
    assert refreshed.last_error_code == "CLASSIFICATION_RESULT_INVALID"
    assert await _pending_candidates(db_session, doc.id) == []


async def test_worker_task_failed_and_timeout(db_session: AsyncSession) -> None:
    doc1 = await _make_document(db_session, drive_file_id="f-cls-fail")
    job1 = await _enqueue(db_session, doc1)
    failed_client = FakeTaskClient(
        lambda task_id: TaskOutcome(status="failed", task_id=task_id, error="boom")
    )
    await run_ai_jobs_once(_Session, SETTINGS, client_factory=lambda s: failed_client)
    refreshed1 = await _get_job(db_session, job1.id)
    assert refreshed1.status == "failed"
    assert refreshed1.last_error_code == "CLASSIFICATION_TASK_FAILED"

    doc2 = await _make_document(db_session, drive_file_id="f-cls-timeout")
    job2 = await _enqueue(db_session, doc2)
    timeout_client = FakeTaskClient(
        lambda task_id: TaskOutcome(status="timeout", task_id=task_id, error="slow")
    )
    await run_ai_jobs_once(
        _Session, SETTINGS, client_factory=lambda s: timeout_client
    )
    refreshed2 = await _get_job(db_session, job2.id)
    assert refreshed2.status == "timeout"
    assert refreshed2.last_error_code == "CLASSIFICATION_TIMEOUT"


async def test_worker_not_configured_defers_job(db_session: AsyncSession) -> None:
    doc = await _make_document(db_session, drive_file_id="f-cls-noenv")
    job = await _enqueue(db_session, doc)

    unconfigured = Settings(open_kknaks_broker_url="", open_kknaks_model="")
    await run_ai_jobs_once(_Session, unconfigured, client_factory=build_task_client)

    refreshed = await _get_job(db_session, job.id)
    # OPEN_KKNAKS_NOT_CONFIGURED — queued 유지 + 지연, attempt 미소모
    assert refreshed.status == "queued"
    assert refreshed.last_error_code == "OPEN_KKNAKS_NOT_CONFIGURED"
    assert refreshed.next_run_at is not None
    assert refreshed.attempt_count == 0


async def test_worker_provider_invalid_marks_failed(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(db_session, drive_file_id="f-cls-badprov")
    job = await _enqueue(db_session, doc)

    bad_provider = Settings(
        open_kknaks_broker_url="redis://fake:6379/0",
        open_kknaks_model="m",
        open_kknaks_provider="gpt",
    )
    await run_ai_jobs_once(_Session, bad_provider, client_factory=build_task_client)

    refreshed = await _get_job(db_session, job.id)
    assert refreshed.status == "failed"
    assert refreshed.last_error_code == "OPEN_KKNAKS_PROVIDER_INVALID"


async def test_worker_skips_unavailable_document(db_session: AsyncSession) -> None:
    doc = await _make_document(db_session, drive_file_id="f-cls-gone")
    job = await _enqueue(db_session, doc)
    doc.drive_state = "removed"
    await db_session.commit()

    client = succeeded_client(VALID_OUTPUT)
    await run_ai_jobs_once(_Session, SETTINGS, client_factory=lambda s: client)

    refreshed = await _get_job(db_session, job.id)
    assert refreshed.status == "stale"  # 재enqueue 없이 종료
    assert client.submitted_prompts == []
    remaining = list(
        await db_session.scalars(
            sa.select(AiQueueJob).where(
                AiQueueJob.document_id == doc.id, AiQueueJob.status == "queued"
            )
        )
    )
    assert remaining == []  # unavailable 문서 자동 재분석 제외


# ----------------------------------------------------------------------
# resolve 실패 표시 / pending 1개 규칙 / relation unresolved
# ----------------------------------------------------------------------


async def test_resolve_failure_flags_admin_fix(db_session: AsyncSession) -> None:
    await _seed_org_tree(db_session)
    doc = await _make_document(db_session, drive_file_id="f-cls-unres")
    job = await _enqueue(db_session, doc)

    output = {
        **VALID_OUTPUT,
        "owning_department": "없는팀",
        "read_policy": {**VALID_OUTPUT["read_policy"], "read_departments": ["없는팀"]},
        "relation_candidates": [],
    }
    client = succeeded_client(output)
    await run_ai_jobs_once(_Session, SETTINGS, client_factory=lambda s: client)

    refreshed = await _get_job(db_session, job.id)
    assert refreshed.status == "candidate_saved"  # 저장은 되고 보정 대상 표시
    pending = await _pending_candidates(db_session, doc.id)
    resolution = pending[0].candidate_metadata["resolution"]
    assert resolution["owning_department_node_id"] is None
    assert "owning_department" in resolution["unresolved_fields"]
    assert "read_policy.read_departments" in resolution["unresolved_fields"]
    assert resolution["needs_admin_fix"] is True


async def test_pending_candidate_replacement_rule(db_session: AsyncSession) -> None:
    """기존 pending(다른 fingerprint)은 stale로 밀고 새 pending 1개만 유지."""
    await _seed_org_tree(db_session)
    doc = await _make_document(db_session, drive_file_id="f-cls-repl")
    old = MetadataCandidate(
        document_id=doc.id,
        state="pending",
        read_capability="content_read",
        candidate_metadata={"document_type": "옛후보"},
        candidate_fingerprint={"old": True},
    )
    db_session.add(old)
    await db_session.commit()

    job = await _enqueue(db_session, doc)
    client = succeeded_client(VALID_OUTPUT)
    await run_ai_jobs_once(_Session, SETTINGS, client_factory=lambda s: client)

    pending = await _pending_candidates(db_session, doc.id)
    assert len(pending) == 1  # 문서당 pending 1개
    assert pending[0].candidate_metadata["document_type"] == "회의록"
    replaced = await db_session.get(MetadataCandidate, old.id)
    await db_session.refresh(replaced)
    assert replaced.state == "stale"
    assert "superseded" in (replaced.reason or "")
    refreshed = await _get_job(db_session, job.id)
    assert refreshed.candidate_id == pending[0].id


async def test_same_fingerprint_result_is_idempotent_update(
    db_session: AsyncSession,
) -> None:
    """같은 candidate_fingerprint 재결과 → row 유지, payload 갱신."""
    await _seed_org_tree(db_session)
    doc = await _make_document(db_session, drive_file_id="f-cls-idem")

    service = ClassificationService(db_session, settings=SETTINGS)
    jobs = AiJobsService(db_session, settings=SETTINGS)

    job1, _ = await jobs.enqueue_classification(doc.id, doc.drive_fingerprint)
    await jobs.mark_running(job1, provider="claude", model="m")
    await jobs.mark_succeeded(job1)
    r1 = await service.process_result(job1, json.dumps(VALID_OUTPUT))
    assert r1.status == "candidate_saved"

    job2 = await jobs.enqueue_reanalysis(doc.id)
    await jobs.mark_running(job2, provider="claude", model="m")
    await jobs.mark_succeeded(job2)
    updated_output = {**VALID_OUTPUT, "summary": "갱신된 요약"}
    r2 = await service.process_result(job2, json.dumps(updated_output))
    assert r2.status == "candidate_saved"
    await db_session.commit()

    pending = await _pending_candidates(db_session, doc.id)
    assert len(pending) == 1
    assert pending[0].id == r1.candidate.id  # row 유지 (멱등 갱신)
    assert pending[0].candidate_metadata["summary"] == "갱신된 요약"


async def test_relation_candidate_unresolved_without_target(
    db_session: AsyncSession,
) -> None:
    await _seed_org_tree(db_session)
    doc = await _make_document(db_session, drive_file_id="f-cls-rel")
    job = await _enqueue(db_session, doc)

    output = {
        **VALID_OUTPUT,
        "relation_candidates": [
            {
                "raw_label": "[[존재하지 않는 문서]]",
                "relation_type": "related",
                "target_hint": None,
            }
        ],
    }
    client = succeeded_client(output)
    doc_count_before = await db_session.scalar(
        sa.select(sa.func.count()).select_from(Document)
    )
    await run_ai_jobs_once(_Session, SETTINGS, client_factory=lambda s: client)

    relations = list(
        await db_session.scalars(
            sa.select(RelationCandidate).where(
                RelationCandidate.source_document_id == doc.id
            )
        )
    )
    assert len(relations) == 1
    assert relations[0].state == "unresolved"
    assert relations[0].target_document_id is None
    # 새 document row 미생성 (DEC-021)
    doc_count_after = await db_session.scalar(
        sa.select(sa.func.count()).select_from(Document)
    )
    assert doc_count_after == doc_count_before
    refreshed = await _get_job(db_session, job.id)
    assert refreshed.status == "candidate_saved"


# ----------------------------------------------------------------------
# analysis_text 추출 (v1 텍스트 계열 최소) — MockTransport
# ----------------------------------------------------------------------


def _text_drive_client(handler) -> GoogleDriveClient:
    return GoogleDriveClient(
        DriveClientConfig(
            client_id="cid", client_secret="cs", refresh_token="rt"
        ),
        transport=httpx.MockTransport(handler),
    )


async def test_extract_analysis_text_google_doc_export(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(
        db_session,
        drive_file_id="f-gdoc",
        name="기획서",
        mime="application/vnd.google-apps.document",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.url.path.endswith("/files/f-gdoc/export"):
            assert request.url.params["mimeType"] == "text/plain"
            return httpx.Response(200, text="기획서 본문 내용")
        return httpx.Response(404)

    service = ClassificationService(
        db_session, settings=SETTINGS, drive_client=_text_drive_client(handler)
    )
    text = await service.extract_analysis_text(doc)
    assert text == "기획서 본문 내용"

    payload = await service.build_input(doc, analysis_text=text)
    assert payload["read_capability"] == "content_read"
    assert payload["analysis_text"] == "기획서 본문 내용"


async def test_extract_analysis_text_plain_text_download(
    db_session: AsyncSession,
) -> None:
    doc = await _make_document(
        db_session, drive_file_id="f-txt", name="메모.txt", mime="text/plain"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.url.path.endswith("/files/f-txt"):
            assert request.url.params["alt"] == "media"
            return httpx.Response(200, text="메모 내용")
        return httpx.Response(404)

    service = ClassificationService(
        db_session, settings=SETTINGS, drive_client=_text_drive_client(handler)
    )
    assert await service.extract_analysis_text(doc) == "메모 내용"


async def test_extract_analysis_text_unsupported_or_failed_is_metadata_only(
    db_session: AsyncSession,
) -> None:
    pdf = await _make_document(
        db_session, drive_file_id="f-pdf", mime="application/pdf"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(500, json={"error": "boom"})

    service = ClassificationService(
        db_session, settings=SETTINGS, drive_client=_text_drive_client(handler)
    )
    assert await service.extract_analysis_text(pdf) is None  # PDF/OCR 보류

    gdoc = await _make_document(
        db_session,
        drive_file_id="f-gdoc-fail",
        mime="application/vnd.google-apps.document",
    )
    assert await service.extract_analysis_text(gdoc) is None  # export 실패 → 제한 입력

    payload = await service.build_input(gdoc, analysis_text=None)
    assert payload["read_capability"] == "metadata_only"
    # metadata_only 입력은 파일명/MIME/수정시각만 전달 (SPEC-007 S-2)
    assert payload["analysis_text"] is None
    assert set(payload["drive_mirror"]) == {
        "drive_name",
        "mime_type",
        "drive_modified_time",
    }
