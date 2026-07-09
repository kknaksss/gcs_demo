"""classification job admin API 테스트 — WORK-004 Phase 5 (SPEC-007 API Contract).

- POST /admin/documents/{id}/classify (수동 분류 — 멱등)
- GET /admin/classification-jobs/{id}
- GET /admin/documents/{id}/classification-jobs
- admin guard (FORBIDDEN_ADMIN_ONLY) + Case Matrix 에러봉투
"""

from __future__ import annotations

from httpx import AsyncClient

from app.models.document import Document
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


async def _make_document(
    *, drive_file_id: str = "f-capi-1", drive_state: str = "active"
) -> int:
    async with _Session() as session:
        row = Document(
            source_provider="google_drive",
            drive_file_id=drive_file_id,
            drive_name="분류 대상.pdf",
            drive_mime_type="application/pdf",
            drive_state=drive_state,
            drive_fingerprint={
                "drive_file_id": drive_file_id,
                "drive_modified_time": "2026-07-08T10:00:00+00:00",
                "drive_name": "분류 대상.pdf",
                "mime_type": "application/pdf",
            },
        )
        session.add(row)
        await session.commit()
        return row.id


async def test_classify_creates_job_and_is_idempotent(
    client: AsyncClient, seeded: list[dict]
) -> None:
    doc_id = await _make_document()
    admin = _pick(seeded, active=True, role="admin")
    headers = await _bearer(client, admin["email"])

    first = await client.post(f"/admin/documents/{doc_id}/classify", headers=headers)
    assert first.status_code == 202
    body = first.json()
    assert body["job_type"] == "classification"
    assert body["status"] == "queued"
    assert body["document_id"] == doc_id
    assert body["attempt_count"] == 0

    # 진행 중 job 재요청 → 같은 job (멱등)
    second = await client.post(
        f"/admin/documents/{doc_id}/classify", headers=headers
    )
    assert second.status_code == 202
    assert second.json()["id"] == body["id"]


async def test_get_classification_job_and_document_history(
    client: AsyncClient, seeded: list[dict]
) -> None:
    doc_id = await _make_document(drive_file_id="f-capi-2")
    admin = _pick(seeded, active=True, role="admin")
    headers = await _bearer(client, admin["email"])
    created = (
        await client.post(f"/admin/documents/{doc_id}/classify", headers=headers)
    ).json()

    # 단일 job 조회 — FE 폴링 표면 (ARCH-002 §6 최소 필드)
    got = await client.get(
        f"/admin/classification-jobs/{created['id']}", headers=headers
    )
    assert got.status_code == 200
    body = got.json()
    for field in (
        "id",
        "job_type",
        "status",
        "document_id",
        "candidate_id",
        "fingerprint",
        "attempt_count",
        "last_error_code",
        "created_at",
        "updated_at",
    ):
        assert field in body

    # 문서별 이력
    listed = await client.get(
        f"/admin/documents/{doc_id}/classification-jobs", headers=headers
    )
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] == 1
    assert payload["jobs"][0]["id"] == created["id"]


async def test_classification_api_error_envelopes(
    client: AsyncClient, seeded: list[dict]
) -> None:
    admin = _pick(seeded, active=True, role="admin")
    headers = await _bearer(client, admin["email"])

    # 없는 job → CLASSIFICATION_JOB_NOT_FOUND
    missing_job = await client.get(
        "/admin/classification-jobs/99999", headers=headers
    )
    assert missing_job.status_code == 404
    assert missing_job.json()["detail"]["error_code"] == "CLASSIFICATION_JOB_NOT_FOUND"

    # 없는 문서 classify → DOCUMENT_NOT_FOUND
    missing_doc = await client.post(
        "/admin/documents/99999/classify", headers=headers
    )
    assert missing_doc.status_code == 404
    assert missing_doc.json()["detail"]["error_code"] == "DOCUMENT_NOT_FOUND"

    # unavailable 문서 classify → DOCUMENT_UNAVAILABLE (분석 제외)
    trashed_id = await _make_document(
        drive_file_id="f-capi-trashed", drive_state="trashed"
    )
    conflict = await client.post(
        f"/admin/documents/{trashed_id}/classify", headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error_code"] == "DOCUMENT_UNAVAILABLE"


async def test_classification_api_requires_admin(
    client: AsyncClient, seeded: list[dict]
) -> None:
    doc_id = await _make_document(drive_file_id="f-capi-member")
    member = _pick(seeded, active=True, role="member")
    headers = await _bearer(client, member["email"])

    for method, path in (
        ("POST", f"/admin/documents/{doc_id}/classify"),
        ("GET", "/admin/classification-jobs/1"),
        ("GET", f"/admin/documents/{doc_id}/classification-jobs"),
    ):
        resp = await client.request(method, path, headers=headers)
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "FORBIDDEN_ADMIN_ONLY"
