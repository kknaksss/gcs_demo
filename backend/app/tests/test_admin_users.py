"""admin 조직 매핑 보정 API 테스트 — WORK-001 Phase 3 (SPEC-001 Admin Behavior)."""

from __future__ import annotations

import sqlalchemy as sa
from httpx import AsyncClient

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


async def _a_department_node_id() -> int:
    async with _Session() as session:
        node = await session.scalar(
            sa.select(OrganizationNode).where(OrganizationNode.type == "department")
        )
        assert node is not None
        return node.id


async def test_admin_lists_unmapped_users(
    client: AsyncClient, seeded: list[dict]
) -> None:
    admin = _pick(seeded, active=True, role="admin")
    headers = await _bearer(client, admin["email"])
    resp = await client.get("/admin/users/unmapped", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["users"]) == 8


async def test_non_admin_forbidden(
    client: AsyncClient, seeded: list[dict]
) -> None:
    member = _pick(seeded, active=True, role="member")
    headers = await _bearer(client, member["email"])
    resp = await client.get("/admin/users/unmapped", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "FORBIDDEN"


async def test_admin_assigns_department_to_unmapped_user(
    client: AsyncClient, seeded: list[dict]
) -> None:
    admin = _pick(seeded, active=True, role="admin")
    headers = await _bearer(client, admin["email"])
    unmapped = (await client.get("/admin/users/unmapped", headers=headers)).json()
    target_id = unmapped["users"][0]["id"]
    node_id = await _a_department_node_id()

    resp = await client.post(
        f"/admin/users/{target_id}/department",
        json={"department_node_id": node_id},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["department_node_id"] == node_id

    # 보정 후 미매핑 목록에서 빠진다.
    after = (await client.get("/admin/users/unmapped", headers=headers)).json()
    assert len(after["users"]) == 7


async def test_assign_invalid_node_422(
    client: AsyncClient, seeded: list[dict]
) -> None:
    admin = _pick(seeded, active=True, role="admin")
    headers = await _bearer(client, admin["email"])
    unmapped = (await client.get("/admin/users/unmapped", headers=headers)).json()
    target_id = unmapped["users"][0]["id"]

    # 회사(company) 노드는 department/team이 아니므로 거부.
    async with _Session() as session:
        company = await session.scalar(
            sa.select(OrganizationNode).where(OrganizationNode.type == "company")
        )
    resp = await client.post(
        f"/admin/users/{target_id}/department",
        json={"department_node_id": company.id},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "INVALID_ORG_NODE"
