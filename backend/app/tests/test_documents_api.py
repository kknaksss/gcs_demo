"""문서 record 조회 API 테스트 — WORK-003 (SPEC-003 최소 표면).

- 일반 사용자 숨김(soft deleted) / admin 감사 목록 / drive-mirror admin 전용.
"""

from __future__ import annotations

from httpx import AsyncClient

from app.models.document import Document
from app.tests.conftest import DEMO_PASSWORD, _Session


def _pick(records: list[dict], *, active: bool, role: str) -> dict:
    """부서 매핑된 계정을 고른다 — WORK-006에서 RBAC read policy가 완성되어
    department_node_id 미매핑 사용자는 일반 문서 탐색이 제한된다 (SPEC-001)."""
    for rec in records:
        if (
            rec["active"] is active
            and rec["role"] == role
            and rec.get("department") is not None
        ):
            return rec
    raise AssertionError("no matching seed record")


async def _bearer(client: AsyncClient, email: str) -> dict[str, str]:
    login = await client.post(
        "/auth/login", json={"email": email, "password": DEMO_PASSWORD}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _make_document(
    *, drive_file_id: str = "f-doc-1", drive_state: str = "active"
) -> int:
    async with _Session() as session:
        row = Document(
            source_provider="google_drive",
            drive_file_id=drive_file_id,
            drive_name="테스트 문서.pdf",
            drive_web_url=f"https://drive.google.com/file/d/{drive_file_id}",
            drive_mime_type="application/pdf",
            drive_state=drive_state,
            drive_fingerprint={
                "drive_file_id": drive_file_id,
                "drive_modified_time": "2026-07-08T10:00:00+00:00",
                "drive_name": "테스트 문서.pdf",
                "mime_type": "application/pdf",
            },
        )
        session.add(row)
        await session.commit()
        return row.id


async def test_get_document_shows_mirror_and_state(
    client: AsyncClient, seeded: list[dict]
) -> None:
    doc_id = await _make_document()
    member = _pick(seeded, active=True, role="member")
    headers = await _bearer(client, member["email"])
    resp = await client.get(f"/documents/{doc_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    # mirror와 approved 필드가 구분되어 응답된다 (SPEC-003 AC)
    assert body["mirror"]["drive_name"] == "테스트 문서.pdf"
    assert body["mirror"]["drive_state"] == "active"
    assert body["mirror"]["drive_mime_type"] == body["mirror"]["drive_fingerprint"]["mime_type"]
    assert body["summary"] is None and body["document_type_id"] is None


async def test_get_document_not_found(
    client: AsyncClient, seeded: list[dict]
) -> None:
    member = _pick(seeded, active=True, role="member")
    headers = await _bearer(client, member["email"])
    resp = await client.get("/documents/9999", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "DOCUMENT_NOT_FOUND"


async def test_soft_deleted_document_hidden_from_member_visible_to_admin(
    client: AsyncClient, seeded: list[dict]
) -> None:
    doc_id = await _make_document(drive_state="trashed")

    member = _pick(seeded, active=True, role="member")
    member_headers = await _bearer(client, member["email"])
    resp = await client.get(f"/documents/{doc_id}", headers=member_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "DOCUMENT_NOT_READABLE"

    admin = _pick(seeded, active=True, role="admin")
    admin_headers = await _bearer(client, admin["email"])
    resp = await client.get(f"/documents/{doc_id}", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["mirror"]["drive_state"] == "trashed"


async def test_drive_mirror_admin_only(
    client: AsyncClient, seeded: list[dict]
) -> None:
    doc_id = await _make_document()

    member = _pick(seeded, active=True, role="member")
    member_headers = await _bearer(client, member["email"])
    resp = await client.get(
        f"/documents/{doc_id}/drive-mirror", headers=member_headers
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "FORBIDDEN_ADMIN_ONLY"

    admin = _pick(seeded, active=True, role="admin")
    admin_headers = await _bearer(client, admin["email"])
    resp = await client.get(
        f"/documents/{doc_id}/drive-mirror", headers=admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["document_id"] == doc_id
    assert body["mirror"]["drive_file_id"] == "f-doc-1"


async def test_admin_documents_filter_by_state(
    client: AsyncClient, seeded: list[dict]
) -> None:
    await _make_document(drive_file_id="f-a", drive_state="active")
    await _make_document(drive_file_id="f-b", drive_state="trashed")
    await _make_document(drive_file_id="f-c", drive_state="out_of_scope")

    admin = _pick(seeded, active=True, role="admin")
    headers = await _bearer(client, admin["email"])

    resp = await client.get("/admin/documents", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3  # admin 감사 목록은 soft deleted 포함

    resp = await client.get(
        "/admin/documents", params={"drive_state": "trashed"}, headers=headers
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["documents"][0]["mirror"]["drive_state"] == "trashed"

    member = _pick(seeded, active=True, role="member")
    member_headers = await _bearer(client, member["email"])
    resp = await client.get("/admin/documents", headers=member_headers)
    assert resp.status_code == 403
