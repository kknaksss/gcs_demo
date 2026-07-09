"""승인 게이트 API 테스트 — WORK-005 Phase 1/2 (SPEC-005).

- 후보 큐/상세 (state 필터, reanalysis_status 파생, current_fingerprint 동봉)
- approve 재검사(state/fingerprint/document state/path/policy) + 반영 + 멱등
- reject(pending/stale), reanalyze(WORK-004 위임)
- 문서종류 추가(정규화 unique), 민감 preset 풀어 저장 (DEC-018)
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from httpx import AsyncClient

from app.dtos.rbac import ReadPolicy
from app.dtos.user import UserDTO
from app.models.ai_queue import AiQueueJob
from app.models.candidate import (
    DocumentRelation,
    MetadataCandidate,
    RelationCandidate,
)
from app.models.document import Document, DocumentRelatedDepartment
from app.models.organization import DocumentTreeNode, DocumentType, OrganizationNode
from app.services.rbac import evaluate_read
from app.tests.conftest import DEMO_PASSWORD, _Session


def _pick(records: list[dict], *, active: bool, role: str) -> dict:
    for rec in records:
        if rec["active"] is active and rec["role"] == role:
            return rec
    raise AssertionError("no matching seed record")


async def _bearer(client: AsyncClient, email: str) -> dict[str, str]:
    login = await client.post(
        "/auth/login", json={"email": email, "password": DEMO_PASSWORD}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _admin_headers(client: AsyncClient, seeded: list[dict]) -> dict[str, str]:
    admin = _pick(seeded, active=True, role="admin")
    return await _bearer(client, admin["email"])


_FP = {
    "drive_file_id": "f-appr-1",
    "drive_modified_time": "2026-07-08T10:00:00+00:00",
    "drive_name": "신규 계약 검토표.xlsx",
    "mime_type": "application/vnd.ms-excel",
}


async def _make_document(
    *,
    drive_file_id: str = "f-appr-1",
    drive_state: str = "active",
    drive_name: str = "신규 계약 검토표.xlsx",
) -> int:
    async with _Session() as session:
        fingerprint = {**_FP, "drive_file_id": drive_file_id, "drive_name": drive_name}
        row = Document(
            source_provider="google_drive",
            drive_file_id=drive_file_id,
            drive_name=drive_name,
            drive_mime_type=fingerprint["mime_type"],
            drive_state=drive_state,
            drive_fingerprint=fingerprint,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _make_candidate(
    document_id: int,
    *,
    state: str = "pending",
    read_capability: str = "content_read",
    fingerprint: dict | None = None,
    reason: str | None = None,
) -> int:
    async with _Session() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        row = MetadataCandidate(
            document_id=document_id,
            state=state,
            read_capability=read_capability,
            candidate_metadata={"document_type": "계약서", "summary": "AI 후보"},
            candidate_fingerprint=fingerprint or dict(document.drive_fingerprint),
            reason=reason,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _org_ids() -> tuple[int, int]:
    """(company_id, department_id) — seed 조직도에서."""
    async with _Session() as session:
        company = await session.scalar(
            sa.select(OrganizationNode).where(OrganizationNode.type == "company")
        )
        dept = await session.scalar(
            sa.select(OrganizationNode)
            .where(OrganizationNode.type == "department")
            .order_by(OrganizationNode.id.asc())
        )
        assert company is not None and dept is not None
        return company.id, dept.id


async def _make_catalog_type(name: str = "계약서") -> int:
    async with _Session() as session:
        row = DocumentType(name=name, normalized_name=name.casefold())
        session.add(row)
        await session.commit()
        return row.id


async def _make_tree_node(
    *, organization_node_id: int, type_: str = "work", name: str = "계약",
    parent_id: int | None = None, document_type_id: int | None = None,
    status: str = "active",
) -> int:
    async with _Session() as session:
        row = DocumentTreeNode(
            organization_node_id=organization_node_id,
            parent_id=parent_id,
            type=type_,
            document_type_id=document_type_id,
            name=name,
            status=status,
        )
        session.add(row)
        await session.commit()
        return row.id


def _payload(
    *,
    document_type_id: int,
    company_id: int,
    dept_id: int,
    tree_path: list[int] | None = None,
    **overrides,
) -> dict:
    payload = {
        "document_type_id": document_type_id,
        "owning_department_node_id": dept_id,
        "physical_tree_path": {
            "organization_path": [company_id, dept_id],
            "tree_path": tree_path or [],
        },
        "related_department_node_ids": [],
        "related_products": [],
        "summary": "승인 요약",
        "read_roles": ["member"],
        "read_departments": [dept_id],
        "read_positions": [],
        "access_logic": "ANY",
        "sensitivity": "normal",
        "policy_preset": None,
    }
    payload.update(overrides)
    return payload


async def _setup(client: AsyncClient, seeded: list[dict]) -> dict:
    headers = await _admin_headers(client, seeded)
    doc_id = await _make_document()
    candidate_id = await _make_candidate(doc_id)
    company_id, dept_id = await _org_ids()
    type_id = await _make_catalog_type()
    return {
        "headers": headers,
        "doc_id": doc_id,
        "candidate_id": candidate_id,
        "company_id": company_id,
        "dept_id": dept_id,
        "type_id": type_id,
    }


# ── 후보 큐/상세 (U-1/U-2) ───────────────────────────────────────────────────


async def test_queue_requires_admin(client: AsyncClient, seeded: list[dict]) -> None:
    member = _pick(seeded, active=True, role="member")
    headers = await _bearer(client, member["email"])
    resp = await client.get("/admin/approval-candidates", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "FORBIDDEN_ADMIN_ONLY"


async def test_queue_filters_state_and_read_capability(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    doc1 = await _make_document(drive_file_id="f-q-1")
    doc2 = await _make_document(drive_file_id="f-q-2")
    doc3 = await _make_document(drive_file_id="f-q-3")
    pending_id = await _make_candidate(doc1)
    await _make_candidate(doc2, state="stale", reason="fingerprint changed")
    metadata_only_id = await _make_candidate(doc3, read_capability="metadata_only")

    all_resp = await client.get("/admin/approval-candidates", headers=headers)
    assert all_resp.status_code == 200
    assert all_resp.json()["total"] == 3

    pending = await client.get(
        "/admin/approval-candidates?state=pending", headers=headers
    )
    assert pending.json()["total"] == 2
    assert {c["state"] for c in pending.json()["candidates"]} == {"pending"}

    stale = await client.get(
        "/admin/approval-candidates?state=stale", headers=headers
    )
    body = stale.json()
    assert body["total"] == 1
    assert body["candidates"][0]["stale_reason"] == "fingerprint changed"

    metadata_only = await client.get(
        "/admin/approval-candidates?read_capability=metadata_only", headers=headers
    )
    assert [c["id"] for c in metadata_only.json()["candidates"]] == [
        metadata_only_id
    ]
    assert pending_id in {c["id"] for c in pending.json()["candidates"]}


async def test_detail_includes_fingerprints(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    resp = await client.get(
        f"/admin/approval-candidates/{ctx['candidate_id']}", headers=ctx["headers"]
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate_fingerprint"] == body["current_fingerprint"]
    assert body["fingerprint_match"] is True
    assert body["state"] == "pending"
    assert body["reanalysis_status"] is None
    assert body["candidate_metadata"]["document_type"] == "계약서"

    missing = await client.get(
        "/admin/approval-candidates/99999", headers=ctx["headers"]
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["error_code"] == "CANDIDATE_NOT_FOUND"


# ── reanalysis_status 파생 (DEC-022) ─────────────────────────────────────────


async def _make_job(document_id: int, *, status: str, fingerprint: str = "fp") -> None:
    async with _Session() as session:
        session.add(
            AiQueueJob(
                job_type="stale_reanalysis",
                status=status,
                document_id=document_id,
                drive_file_id=f"f-job-{uuid.uuid4().hex[:6]}",
                fingerprint=fingerprint,
                idempotency_key=uuid.uuid4().hex[:8],
                attempt_count=1,
                max_attempts=3,
            )
        )
        await session.commit()


async def test_reanalysis_status_derivation(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)

    # reanalyzing — stale 후보 + 진행 중 job
    doc1 = await _make_document(drive_file_id="f-rs-1")
    stale1 = await _make_candidate(doc1, state="stale")
    await _make_job(doc1, status="running")
    body = (
        await client.get(f"/admin/approval-candidates/{stale1}", headers=headers)
    ).json()
    assert body["reanalysis_status"] == "reanalyzing"

    # reanalysis_failed — 최신 job 실패
    doc2 = await _make_document(drive_file_id="f-rs-2")
    stale2 = await _make_candidate(doc2, state="stale")
    await _make_job(doc2, status="failed")
    body = (
        await client.get(f"/admin/approval-candidates/{stale2}", headers=headers)
    ).json()
    assert body["reanalysis_status"] == "reanalysis_failed"

    # new_candidate_ready — 재분석 성공으로 새 pending 후보 존재
    doc3 = await _make_document(drive_file_id="f-rs-3")
    stale3 = await _make_candidate(doc3, state="stale")
    await _make_candidate(doc3, state="pending")
    await _make_job(doc3, status="candidate_saved")
    body = (
        await client.get(f"/admin/approval-candidates/{stale3}", headers=headers)
    ).json()
    assert body["reanalysis_status"] == "new_candidate_ready"

    # 원장 state는 그대로 stale — 표시용 상태는 저장되지 않는다
    async with _Session() as session:
        row = await session.get(MetadataCandidate, stale3)
        assert row is not None and row.state == "stale"


# ── approve (S-1) ────────────────────────────────────────────────────────────


async def test_approve_applies_metadata_and_closes_candidate(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    work_id = await _make_tree_node(organization_node_id=ctx["dept_id"])
    payload = _payload(
        document_type_id=ctx["type_id"],
        company_id=ctx["company_id"],
        dept_id=ctx["dept_id"],
        tree_path=[work_id],
        related_department_node_ids=[ctx["dept_id"]],
        related_products=["cloud-file-organizer"],
    )
    resp = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload,
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["idempotent"] is False
    assert body["candidate"]["state"] == "approved"
    assert body["document"]["document_type_id"] == ctx["type_id"]
    assert body["document"]["tree_path"] == [work_id]
    assert body["document"]["related_department_node_ids"] == [ctx["dept_id"]]

    async with _Session() as session:
        document = await session.get(Document, ctx["doc_id"])
        candidate = await session.get(MetadataCandidate, ctx["candidate_id"])
        assert document is not None and candidate is not None
        # approved 필드 반영
        assert document.document_type_id == ctx["type_id"]
        assert document.owning_department_node_id == ctx["dept_id"]
        assert document.organization_path == [ctx["company_id"], ctx["dept_id"]]
        assert document.read_departments == [ctx["dept_id"]]
        assert document.summary == "승인 요약"
        # mirror 불가침
        assert document.drive_name == "신규 계약 검토표.xlsx"
        assert document.drive_fingerprint["drive_file_id"] == "f-appr-1"
        # 후보 종결 (approved_by/at)
        assert candidate.state == "approved"
        assert candidate.approved_by is not None
        assert candidate.approved_at is not None
        related = (
            await session.scalars(
                sa.select(DocumentRelatedDepartment.organization_node_id).where(
                    DocumentRelatedDepartment.document_id == ctx["doc_id"]
                )
            )
        ).all()
        assert list(related) == [ctx["dept_id"]]


async def test_approve_is_idempotent_on_retry(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    payload = _payload(
        document_type_id=ctx["type_id"],
        company_id=ctx["company_id"],
        dept_id=ctx["dept_id"],
    )
    first = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload,
        headers=ctx["headers"],
    )
    assert first.status_code == 200

    # 같은 요청 재시도 → 멱등 성공 (Implementation Rules)
    retry = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload,
        headers=ctx["headers"],
    )
    assert retry.status_code == 200
    assert retry.json()["idempotent"] is True

    # 다른 payload로 재승인 시도는 거부
    changed = {**payload, "summary": "다른 요약"}
    conflict = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=changed,
        headers=ctx["headers"],
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error_code"] == "CANDIDATE_NOT_PENDING"


async def test_approve_rejected_candidate_not_pending(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    async with _Session() as session:
        row = await session.get(MetadataCandidate, ctx["candidate_id"])
        assert row is not None
        row.state = "rejected"
        await session.commit()
    payload = _payload(
        document_type_id=ctx["type_id"],
        company_id=ctx["company_id"],
        dept_id=ctx["dept_id"],
    )
    resp = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload,
        headers=ctx["headers"],
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "CANDIDATE_NOT_PENDING"


async def test_approve_fingerprint_mismatch_marks_stale(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    # Drive mirror가 후보 생성 이후 변경된 상황
    async with _Session() as session:
        document = await session.get(Document, ctx["doc_id"])
        assert document is not None
        document.drive_fingerprint = {
            **document.drive_fingerprint,
            "drive_modified_time": "2026-07-08T12:00:00+00:00",
        }
        await session.commit()
    payload = _payload(
        document_type_id=ctx["type_id"],
        company_id=ctx["company_id"],
        dept_id=ctx["dept_id"],
    )
    resp = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload,
        headers=ctx["headers"],
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "CANDIDATE_STALE"
    # 원장 전이: pending → stale
    async with _Session() as session:
        row = await session.get(MetadataCandidate, ctx["candidate_id"])
        assert row is not None and row.state == "stale"

    # stale 후보 재승인 시도도 CANDIDATE_STALE
    again = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload,
        headers=ctx["headers"],
    )
    assert again.status_code == 409
    assert again.json()["detail"]["error_code"] == "CANDIDATE_STALE"


async def test_approve_unavailable_document_blocked(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    async with _Session() as session:
        document = await session.get(Document, ctx["doc_id"])
        assert document is not None
        document.drive_state = "trashed"
        await session.commit()
    payload = _payload(
        document_type_id=ctx["type_id"],
        company_id=ctx["company_id"],
        dept_id=ctx["dept_id"],
    )
    resp = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload,
        headers=ctx["headers"],
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "DOCUMENT_UNAVAILABLE"
    async with _Session() as session:
        row = await session.get(MetadataCandidate, ctx["candidate_id"])
        assert row is not None and row.state == "blocked"


async def test_approve_inactive_path_rejected(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    inactive_work = await _make_tree_node(
        organization_node_id=ctx["dept_id"], status="inactive", name="Legacy Ops"
    )
    payload = _payload(
        document_type_id=ctx["type_id"],
        company_id=ctx["company_id"],
        dept_id=ctx["dept_id"],
        tree_path=[inactive_work],
    )
    resp = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload,
        headers=ctx["headers"],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "INVALID_TREE_PATH"
    # 부분 반영 금지 — 후보는 pending 유지
    async with _Session() as session:
        row = await session.get(MetadataCandidate, ctx["candidate_id"])
        assert row is not None and row.state == "pending"


async def test_approve_unknown_document_type_rejected(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    payload = _payload(
        document_type_id=99999,
        company_id=ctx["company_id"],
        dept_id=ctx["dept_id"],
    )
    resp = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload,
        headers=ctx["headers"],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "DOCUMENT_TYPE_NOT_FOUND"


async def test_approve_invalid_access_policy(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    # PRESET인데 preset 이름 없음
    payload = _payload(
        document_type_id=ctx["type_id"],
        company_id=ctx["company_id"],
        dept_id=ctx["dept_id"],
        access_logic="PRESET",
        policy_preset=None,
    )
    resp = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload,
        headers=ctx["headers"],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "INVALID_ACCESS_POLICY"

    # ANY인데 preset 이름이 옴
    payload2 = _payload(
        document_type_id=ctx["type_id"],
        company_id=ctx["company_id"],
        dept_id=ctx["dept_id"],
        access_logic="ANY",
        policy_preset="HR_RESTRICTED",
    )
    resp2 = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload2,
        headers=ctx["headers"],
    )
    assert resp2.status_code == 422
    assert resp2.json()["detail"]["error_code"] == "INVALID_ACCESS_POLICY"


# ── 민감 preset 풀어 저장 (U-5, DEC-018) ─────────────────────────────────────


async def test_approve_preset_expands_read_policy(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    payload = _payload(
        document_type_id=ctx["type_id"],
        company_id=ctx["company_id"],
        dept_id=ctx["dept_id"],
        access_logic="PRESET",
        policy_preset="HR_RESTRICTED",
        sensitivity="sensitive",
        # preset이 payload read 필드를 덮는다 (풀어 저장 SoT는 preset 정의)
        read_roles=["member"],
        read_departments=[],
    )
    resp = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload,
        headers=ctx["headers"],
    )
    assert resp.status_code == 200
    body = resp.json()["document"]
    assert body["access_logic"] == "PRESET"
    assert body["policy_preset"] == "HR_RESTRICTED"
    # preset이 read policy 필드로 풀어 저장됨
    assert body["read_roles"] == ["admin"]

    async with _Session() as session:
        document = await session.get(Document, ctx["doc_id"])
        hr = await session.scalar(
            sa.select(OrganizationNode).where(
                sa.func.lower(OrganizationNode.name) == "hr",
                OrganizationNode.type == "department",
            )
        )
        assert document is not None
        if hr is not None:
            assert hr.id in (document.read_departments or [])

        # RBAC core가 풀어 저장된 필드로 판정 (WORK-001 evaluate_read)
        policy = ReadPolicy.from_mapping(
            {
                "read_roles": document.read_roles,
                "read_departments": document.read_departments,
                "read_positions": document.read_positions,
                "access_logic": document.access_logic,
                "sensitivity": document.sensitivity,
                "policy_preset": document.policy_preset,
            }
        )

    def _member(department_node_id: int | None) -> UserDTO:
        return UserDTO(
            id=999,
            source_user_id=uuid.uuid4(),
            email="m@example.com",
            name="member",
            role="member",
            position="주니어",
            department="etc",
            department_node_id=department_node_id,
            team_node_id=None,
            active=True,
            employment_type=None,
            resigned_at=None,
        )

    if hr is not None:
        assert evaluate_read(_member(hr.id), policy).final_readable is True
    # preset 부서가 아닌 일반 member는 차단
    assert evaluate_read(_member(ctx["dept_id"] + 100000), policy).final_readable is False


# ── reject / reanalyze (U-2) ─────────────────────────────────────────────────


async def test_reject_pending_and_stale(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    doc1 = await _make_document(drive_file_id="f-rj-1")
    pending_id = await _make_candidate(doc1)
    resp = await client.post(
        f"/admin/approval-candidates/{pending_id}/reject",
        json={"reason": "잘못된 분류"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "rejected"

    # stale 후보도 거절 가능 (SPEC-005 AC)
    doc2 = await _make_document(drive_file_id="f-rj-2")
    stale_id = await _make_candidate(doc2, state="stale")
    resp2 = await client.post(
        f"/admin/approval-candidates/{stale_id}/reject", headers=headers
    )
    assert resp2.status_code == 200
    assert resp2.json()["state"] == "rejected"

    # approved 후보는 거절 불가
    doc3 = await _make_document(drive_file_id="f-rj-3")
    approved_id = await _make_candidate(doc3, state="approved")
    resp3 = await client.post(
        f"/admin/approval-candidates/{approved_id}/reject", headers=headers
    )
    assert resp3.status_code == 409
    assert resp3.json()["detail"]["error_code"] == "CANDIDATE_NOT_PENDING"


async def test_reanalyze_delegates_to_work004_enqueue(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    doc_id = await _make_document(drive_file_id="f-re-1")
    stale_id = await _make_candidate(doc_id, state="stale")

    resp = await client.post(
        f"/admin/approval-candidates/{stale_id}/reanalyze", headers=headers
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["document_id"] == doc_id
    assert body["status"] == "queued"

    # unavailable 문서는 재분석 불가
    async with _Session() as session:
        document = await session.get(Document, doc_id)
        assert document is not None
        document.drive_state = "removed"
        await session.commit()
    blocked = await client.post(
        f"/admin/approval-candidates/{stale_id}/reanalyze", headers=headers
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error_code"] == "DOCUMENT_UNAVAILABLE"


# ── 문서종류 추가 (U-4/S-3) ──────────────────────────────────────────────────


async def test_document_type_add_and_duplicate(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    created = await client.post(
        "/admin/document-types", json={"name": "회의록"}, headers=headers
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "회의록"
    assert body["normalized_name"] == "회의록"

    # 정규화 기준 중복 (공백/대소문자 무시)
    dup = await client.post(
        "/admin/document-types", json={"name": " 회의록 "}, headers=headers
    )
    assert dup.status_code == 409
    assert dup.json()["detail"]["error_code"] == "DOCUMENT_TYPE_DUPLICATE"

    latin = await client.post(
        "/admin/document-types", json={"name": "NDA Draft"}, headers=headers
    )
    assert latin.status_code == 201
    dup2 = await client.post(
        "/admin/document-types", json={"name": "nda  draft"}, headers=headers
    )
    assert dup2.status_code == 409

    # 추가분이 admin 카탈로그 조회에 반영
    listed = await client.get("/admin/document-types", headers=headers)
    names = {t["name"] for t in listed.json()["document_types"]}
    assert {"회의록", "NDA Draft"} <= names

    # member는 추가 불가
    member = _pick(seeded, active=True, role="member")
    member_headers = await _bearer(client, member["email"])
    forbidden = await client.post(
        "/admin/document-types", json={"name": "멤버 추가"}, headers=member_headers
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["error_code"] == "FORBIDDEN_ADMIN_ONLY"


# ── relation 반영 (approve 흐름) ─────────────────────────────────────────────


async def test_approve_reflects_resolved_relations_only(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    target_id = await _make_document(
        drive_file_id="f-rel-target", drive_name="표준 계약 템플릿.gdoc"
    )
    async with _Session() as session:
        resolved = RelationCandidate(
            source_document_id=ctx["doc_id"],
            raw_label="[[표준 계약 템플릿]]",
            suggested_relation_type="references",
            target_document_id=target_id,
            state="pending",
        )
        unresolved = RelationCandidate(
            source_document_id=ctx["doc_id"],
            raw_label="[[구매 품의 규정]]",
            suggested_relation_type="related",
            target_document_id=None,
            state="unresolved",
        )
        session.add_all([resolved, unresolved])
        await session.commit()
        resolved_id, unresolved_id = resolved.id, unresolved.id

    payload = _payload(
        document_type_id=ctx["type_id"],
        company_id=ctx["company_id"],
        dept_id=ctx["dept_id"],
    )
    resp = await client.post(
        f"/admin/approval-candidates/{ctx['candidate_id']}/approve",
        json=payload,
        headers=ctx["headers"],
    )
    assert resp.status_code == 200

    async with _Session() as session:
        relations = (
            await session.scalars(
                sa.select(DocumentRelation).where(
                    DocumentRelation.source_document_id == ctx["doc_id"]
                )
            )
        ).all()
        # resolved(pending+target)만 확정 graph 반영 (DEC-021)
        assert len(relations) == 1
        assert relations[0].target_document_id == target_id
        assert relations[0].relation_type == "references"

        resolved_row = await session.get(RelationCandidate, resolved_id)
        unresolved_row = await session.get(RelationCandidate, unresolved_id)
        assert resolved_row is not None and resolved_row.state == "approved"
        # unresolved는 그대로 보류 — document row도 자동 생성되지 않음
        assert unresolved_row is not None and unresolved_row.state == "unresolved"
        doc_count = await session.scalar(
            sa.select(sa.func.count()).select_from(Document)
        )
        assert doc_count == 2  # source + target 그대로
