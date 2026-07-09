"""조직도/문서 트리/카탈로그 API 테스트 — WORK-002 Phase 1 (SPEC-002 Validation/Case Matrix)."""

from __future__ import annotations

import sqlalchemy as sa
from httpx import AsyncClient

from app.models.organization import DocumentType, OrganizationNode
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


async def _node_by_type(node_type: str) -> OrganizationNode:
    async with _Session() as session:
        node = await session.scalar(
            sa.select(OrganizationNode).where(OrganizationNode.type == node_type)
        )
        assert node is not None
        return node


async def _make_document_type(name: str = "요구사항 정의서") -> int:
    async with _Session() as session:
        row = DocumentType(name=name, normalized_name=name.replace(" ", "").lower())
        session.add(row)
        await session.commit()
        return row.id


# ----------------------------------------------------------------------
# GET /organization-tree
# ----------------------------------------------------------------------


async def test_org_tree_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/organization-tree")
    assert resp.status_code == 401


async def test_org_tree_lists_seeded_hierarchy(
    client: AsyncClient, seeded: list[dict]
) -> None:
    member = _pick(seeded, active=True, role="member")
    headers = await _bearer(client, member["email"])
    resp = await client.get("/organization-tree", headers=headers)
    assert resp.status_code == 200
    nodes = resp.json()["nodes"]
    companies = [n for n in nodes if n["type"] == "company"]
    departments = [n for n in nodes if n["type"] == "department"]
    assert len(companies) == 1
    assert companies[0]["parent_id"] is None
    assert departments and all(
        d["parent_id"] == companies[0]["id"] for d in departments
    )


# ----------------------------------------------------------------------
# POST/PATCH /organization-nodes — 계층 validation + admin guard
# ----------------------------------------------------------------------


async def test_non_admin_cannot_create_org_node(
    client: AsyncClient, seeded: list[dict]
) -> None:
    member = _pick(seeded, active=True, role="member")
    headers = await _bearer(client, member["email"])
    resp = await client.post(
        "/organization-nodes",
        json={"type": "team", "name": "FE Team", "parent_id": 1},
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "FORBIDDEN_ADMIN_ONLY"


async def test_create_team_under_department(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    dept = await _node_by_type("department")
    resp = await client.post(
        "/organization-nodes",
        json={"type": "team", "name": "FE Team", "parent_id": dept.id},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["type"] == "team"
    assert body["parent_id"] == dept.id
    assert body["status"] == "active"


async def test_second_company_root_rejected(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    resp = await client.post(
        "/organization-nodes",
        json={"type": "company", "name": "다른회사"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "INVALID_TREE_DEPTH"


async def test_team_under_company_rejected(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    company = await _node_by_type("company")
    resp = await client.post(
        "/organization-nodes",
        json={"type": "team", "name": "FE Team", "parent_id": company.id},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "INVALID_TREE_DEPTH"


async def test_department_under_missing_parent_rejected(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    resp = await client.post(
        "/organization-nodes",
        json={"type": "department", "name": "신규부서", "parent_id": 999_999},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "ORG_NODE_NOT_FOUND"


async def test_rename_keeps_id_and_tree_shows_latest_name(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    dept = await _node_by_type("department")
    resp = await client.patch(
        f"/organization-nodes/{dept.id}",
        json={"name": "새이름팀"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "id": dept.id,
        "parent_id": dept.parent_id,
        "type": "department",
        "name": "새이름팀",
        "status": "active",
    }
    tree = (await client.get("/organization-tree", headers=headers)).json()
    renamed = next(n for n in tree["nodes"] if n["id"] == dept.id)
    assert renamed["name"] == "새이름팀"


async def test_deactivate_is_soft_and_blocks_new_children(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    dept = await _node_by_type("department")
    resp = await client.patch(
        f"/organization-nodes/{dept.id}",
        json={"status": "inactive"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "inactive"

    # hard delete 아님 — 트리 조회에 남는다 (DEC-013).
    tree = (await client.get("/organization-tree", headers=headers)).json()
    assert any(n["id"] == dept.id for n in tree["nodes"])

    # inactive 조직 아래 새 노드 생성 불가.
    resp = await client.post(
        "/organization-nodes",
        json={"type": "team", "name": "늦은팀", "parent_id": dept.id},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "ORG_NODE_INACTIVE"


# ----------------------------------------------------------------------
# document tree nodes + catalog
# ----------------------------------------------------------------------


async def test_create_work_node_and_config_reflects(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    dept = await _node_by_type("department")
    resp = await client.post(
        "/document-tree-nodes",
        json={"organization_node_id": dept.id, "type": "work", "name": "제품 운영"},
        headers=headers,
    )
    assert resp.status_code == 201
    node = resp.json()
    assert node["type"] == "work"
    assert node["document_type_id"] is None

    config = (await client.get("/document-tree-config", headers=headers)).json()
    assert any(n["id"] == node["id"] for n in config["nodes"])


async def test_work_node_under_inactive_org_rejected(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    dept = await _node_by_type("department")
    await client.patch(
        f"/organization-nodes/{dept.id}",
        json={"status": "inactive"},
        headers=headers,
    )
    resp = await client.post(
        "/document-tree-nodes",
        json={"organization_node_id": dept.id, "type": "work", "name": "제품 운영"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "ORG_NODE_INACTIVE"


async def test_document_type_node_references_catalog(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    dept = await _node_by_type("department")
    catalog_id = await _make_document_type()

    work = (
        await client.post(
            "/document-tree-nodes",
            json={"organization_node_id": dept.id, "type": "work", "name": "제품 운영"},
            headers=headers,
        )
    ).json()

    resp = await client.post(
        "/document-tree-nodes",
        json={
            "organization_node_id": dept.id,
            "parent_id": work["id"],
            "type": "document_type",
            "document_type_id": catalog_id,
            "name": "요구사항 정의서",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["document_type_id"] == catalog_id

    # 카탈로그에 없는 stable id 참조는 거부.
    resp = await client.post(
        "/document-tree-nodes",
        json={
            "organization_node_id": dept.id,
            "type": "document_type",
            "document_type_id": 999_999,
            "name": "없는종류",
        },
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "TREE_NODE_NOT_FOUND"

    # document_type 노드는 카탈로그 id 없이 만들 수 없다 (schema validation).
    resp = await client.post(
        "/document-tree-nodes",
        json={
            "organization_node_id": dept.id,
            "type": "document_type",
            "name": "카탈로그 없음",
        },
        headers=headers,
    )
    assert resp.status_code == 422


async def test_document_types_endpoint_lists_catalog(
    client: AsyncClient, seeded: list[dict]
) -> None:
    member = _pick(seeded, active=True, role="member")
    headers = await _bearer(client, member["email"])
    catalog_id = await _make_document_type("회의록")
    resp = await client.get("/document-types", headers=headers)
    assert resp.status_code == 200
    types = resp.json()["document_types"]
    assert any(t["id"] == catalog_id and t["name"] == "회의록" for t in types)
