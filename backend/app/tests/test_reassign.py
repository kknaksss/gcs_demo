"""문서 이관 + path history 테스트 — WORK-002 Phase 2 (SPEC-002 U-5, DEC-015).

Document row는 fixture로 만든다 — Drive sync는 WORK-003, 실데이터 재검증은
WORK-005 이후 항목 (work-002 Open Issues).
"""

from __future__ import annotations

import sqlalchemy as sa
from httpx import AsyncClient

from app.models.document import Document, DocumentPathHistory
from app.models.organization import OrganizationNode
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


async def _make_document(drive_file_id: str = "fixture-file-1") -> int:
    async with _Session() as session:
        row = Document(
            source_provider="google_drive",
            drive_file_id=drive_file_id,
            drive_name="fixture.pdf",
            drive_mime_type="application/pdf",
            drive_state="active",
            drive_fingerprint={"md5": "fixture"},
        )
        session.add(row)
        await session.commit()
        return row.id


async def _org_path() -> tuple[list[int], int]:
    """(company>department path, department id)."""
    async with _Session() as session:
        company = await session.scalar(
            sa.select(OrganizationNode).where(OrganizationNode.type == "company")
        )
        dept = await session.scalar(
            sa.select(OrganizationNode).where(OrganizationNode.type == "department")
        )
        assert company is not None and dept is not None
        return [company.id, dept.id], dept.id


async def test_reassign_requires_reason(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    doc_id = await _make_document()
    org_path, _ = await _org_path()
    resp = await client.post(
        f"/documents/{doc_id}/reassign",
        json={"organization_path": org_path, "tree_path": [], "reason": "   "},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "REASSIGN_REASON_REQUIRED"

    # 사유 없이 실패하면 history도 남지 않는다.
    async with _Session() as session:
        count = await session.scalar(
            sa.select(sa.func.count()).select_from(DocumentPathHistory)
        )
    assert count == 0


async def test_reassign_to_inactive_org_rejected(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    doc_id = await _make_document()
    org_path, dept_id = await _org_path()
    await client.patch(
        f"/organization-nodes/{dept_id}",
        json={"status": "inactive"},
        headers=headers,
    )
    resp = await client.post(
        f"/documents/{doc_id}/reassign",
        json={"organization_path": org_path, "tree_path": [], "reason": "부서 이동"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "ORG_NODE_INACTIVE"


async def test_reassign_invalid_hierarchy_rejected(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    doc_id = await _make_document()
    _, dept_id = await _org_path()
    # company root부터 시작하지 않는 path는 거부.
    resp = await client.post(
        f"/documents/{doc_id}/reassign",
        json={"organization_path": [dept_id], "tree_path": [], "reason": "이동"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "INVALID_TREE_DEPTH"


async def test_reassign_missing_document(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    org_path, _ = await _org_path()
    resp = await client.post(
        "/documents/999999/reassign",
        json={"organization_path": org_path, "tree_path": [], "reason": "이동"},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "DOCUMENT_NOT_READABLE"


async def test_reassign_forbidden_for_member(
    client: AsyncClient, seeded: list[dict]
) -> None:
    member = _pick(seeded, active=True, role="member")
    headers = await _bearer(client, member["email"])
    doc_id = await _make_document()
    org_path, _ = await _org_path()
    resp = await client.post(
        f"/documents/{doc_id}/reassign",
        json={"organization_path": org_path, "tree_path": [], "reason": "이동"},
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "FORBIDDEN_ADMIN_ONLY"

    history = await client.get(f"/documents/{doc_id}/path-history", headers=headers)
    assert history.status_code == 403


async def test_reassign_updates_path_and_appends_history(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    doc_id = await _make_document()
    org_path, dept_id = await _org_path()

    # work 노드까지 포함한 full path로 이관.
    work = (
        await client.post(
            "/document-tree-nodes",
            json={"organization_node_id": dept_id, "type": "work", "name": "제품 운영"},
            headers=headers,
        )
    ).json()

    resp = await client.post(
        f"/documents/{doc_id}/reassign",
        json={
            "organization_path": org_path,
            "tree_path": [work["id"]],
            "reason": "부서 개편",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    path = resp.json()["path"]
    assert path["organization_path"] == org_path
    assert path["tree_path"] == [work["id"]]
    assert path["display_path"].endswith("제품 운영")

    # 문서 row 갱신 + owning department 반영.
    async with _Session() as session:
        doc = await session.get(Document, doc_id)
        assert doc is not None
        assert doc.organization_path == org_path
        assert doc.tree_path == [work["id"]]
        assert doc.owning_department_node_id == dept_id

    # 두 번째 이관 → history가 append-only로 누적.
    resp = await client.post(
        f"/documents/{doc_id}/reassign",
        json={"organization_path": org_path, "tree_path": [], "reason": "재이관"},
        headers=headers,
    )
    assert resp.status_code == 200

    history = (
        await client.get(f"/documents/{doc_id}/path-history", headers=headers)
    ).json()
    assert len(history["entries"]) == 2
    latest, first = history["entries"]
    assert first["previous_path"] == {"organization_path": [], "tree_path": []}
    assert first["new_path"] == {
        "organization_path": org_path,
        "tree_path": [work["id"]],
    }
    assert latest["previous_path"] == first["new_path"]
    assert latest["reason"] == "재이관"
    assert latest["changed_by"] is not None
    assert latest["changed_at"] is not None


async def test_rename_after_reassign_shows_latest_display_name(
    client: AsyncClient, seeded: list[dict]
) -> None:
    """path는 노드 id 저장 — rename 후 별도 갱신 없이 최신 표시명 반영 (SPEC-002)."""
    headers = await _admin_headers(client, seeded)
    doc_id = await _make_document()
    org_path, dept_id = await _org_path()

    await client.post(
        f"/documents/{doc_id}/reassign",
        json={"organization_path": org_path, "tree_path": [], "reason": "최초 귀속"},
        headers=headers,
    )
    await client.patch(
        f"/organization-nodes/{dept_id}",
        json={"name": "개편된부서"},
        headers=headers,
    )

    history = (
        await client.get(f"/documents/{doc_id}/path-history", headers=headers)
    ).json()
    current = history["current_path"]
    assert current is not None
    assert current["organization_path"] == org_path
    assert "개편된부서" in current["display_path"]
    assert current["owning_department"] == "개편된부서"
