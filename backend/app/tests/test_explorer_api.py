"""문서 탐색/관계 API 테스트 — WORK-006 (SPEC-002/003/006).

- 물리 귀속 목록에 논리 연결 문서 미혼입 (DEC-014)
- 관련 문서 3원천(related_departments/related_products/document_relations) 병합
- 검색 source_badge/filter, RBAC 숨김 5개 표면(목록/관련/검색/상세 relation/상세)
- unresolved relation candidate 미노출 (DEC-021), unavailable target 숨김
- relation type v1 enum 4종 제한

seed 계정(admin / fe member / hr member / 미매핑 member) + fixture 문서로
권한 매트릭스를 교차 검증한다 (work-006 Phase 4).
"""

from __future__ import annotations

import sqlalchemy as sa
from httpx import AsyncClient

from app.models.candidate import (
    DocumentRelation,
    MetadataCandidate,
    RelationCandidate,
)
from app.models.document import Document, DocumentRelatedDepartment
from app.models.organization import DocumentTreeNode, DocumentType, OrganizationNode
from app.models.user import User
from app.tests.conftest import DEMO_PASSWORD, _Session

# ── 계정/조직 헬퍼 ────────────────────────────────────────────────────────────


def _pick_user(
    records: list[dict],
    *,
    role: str | None = None,
    department: str | None | object = "__any__",
    admin: bool | None = None,
) -> dict:
    for rec in records:
        if not rec["active"] or rec.get("resigned_at"):
            continue
        if admin is True and rec["role"] != "admin":
            continue
        if admin is False and rec["role"] == "admin":
            continue
        if role is not None and rec["role"] != role:
            continue
        if department != "__any__" and rec.get("department") != department:
            continue
        return rec
    raise AssertionError("no matching seed record")


async def _bearer(client: AsyncClient, email: str) -> dict[str, str]:
    login = await client.post(
        "/auth/login", json={"email": email, "password": DEMO_PASSWORD}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _org_ids() -> dict[str, int]:
    """{name: node_id} — company root는 'company' key."""
    async with _Session() as session:
        rows = (await session.scalars(sa.select(OrganizationNode))).all()
    ids = {row.name: row.id for row in rows}
    company = next(r for r in rows if r.type == "company")
    ids["company"] = company.id
    return ids


async def _user_id(email: str) -> int:
    async with _Session() as session:
        row = await session.scalar(sa.select(User).where(User.email == email))
        assert row is not None
        return row.id


# ── fixture 문서/relation 헬퍼 ────────────────────────────────────────────────


async def _make_doc(
    name: str,
    *,
    file_id: str,
    org_path: list[int] | None,
    dept_id: int | None = None,
    tree_path: list[int] | None = None,
    read_departments: list[int] | None = None,
    access_logic: str = "ANY",
    sensitivity: str = "normal",
    policy_preset: str | None = None,
    related_products: list[str] | None = None,
    summary: str | None = None,
    drive_state: str = "active",
    document_type_id: int | None = None,
) -> int:
    async with _Session() as session:
        row = Document(
            source_provider="google_drive",
            drive_file_id=file_id,
            drive_name=name,
            drive_web_url=f"https://drive.example/{file_id}",
            drive_mime_type="application/pdf",
            drive_state=drive_state,
            drive_fingerprint={"md5": file_id},
            document_type_id=document_type_id,
            owning_department_node_id=dept_id,
            organization_path=org_path,
            tree_path=tree_path or ([] if org_path else None),
            related_products=related_products,
            read_departments=read_departments,
            access_logic=access_logic,
            sensitivity=sensitivity,
            policy_preset=policy_preset,
            summary=summary,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _make_relation(
    source_id: int,
    target_id: int,
    *,
    relation_type: str,
    approved_by: int,
    source_label: str | None = None,
) -> int:
    async with _Session() as session:
        row = DocumentRelation(
            source_document_id=source_id,
            target_document_id=target_id,
            relation_type=relation_type,
            source_label=source_label,
            approved_by=approved_by,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _mark_related_department(document_id: int, org_node_id: int) -> None:
    async with _Session() as session:
        session.add(
            DocumentRelatedDepartment(
                document_id=document_id, organization_node_id=org_node_id
            )
        )
        await session.commit()


async def _make_unresolved_candidate(source_id: int, raw_label: str) -> int:
    async with _Session() as session:
        row = RelationCandidate(
            source_document_id=source_id,
            raw_label=raw_label,
            suggested_relation_type="references",
            target_document_id=None,
            state="unresolved",
        )
        session.add(row)
        await session.commit()
        return row.id


# ── 공용 시나리오 셋업 ────────────────────────────────────────────────────────


async def _setup(client: AsyncClient, seeded: list[dict]) -> dict:
    """fe/hr/qa 부서에 걸친 fixture 문서 셋 + 계정 헤더."""
    admin_rec = _pick_user(seeded, admin=True)
    fe_rec = _pick_user(seeded, admin=False, department="fe")
    hr_rec = _pick_user(seeded, admin=False, department="hr")
    unmapped_rec = _pick_user(seeded, admin=False, department=None)

    ctx: dict = {
        "admin": await _bearer(client, admin_rec["email"]),
        "fe": await _bearer(client, fe_rec["email"]),
        "hr": await _bearer(client, hr_rec["email"]),
        "unmapped": await _bearer(client, unmapped_rec["email"]),
        "admin_id": await _user_id(admin_rec["email"]),
    }
    orgs = await _org_ids()
    ctx["company"] = orgs["company"]
    ctx["fe_id"] = orgs["fe"]
    ctx["hr_id"] = orgs["hr"]
    ctx["qa_id"] = orgs["qa"]

    # 물리 귀속: fe 부서 문서 (공개)
    ctx["doc_fe"] = await _make_doc(
        "FE 온보딩 체크리스트.gdoc",
        file_id="fe-onboarding",
        org_path=[ctx["company"], ctx["fe_id"]],
        dept_id=ctx["fe_id"],
        related_products=["MediNess"],
        summary="FE 신규 입사자 온보딩 체크리스트",
    )
    # 물리 귀속: hr 부서 문서 (공개) — fe 부서를 관련 부서로 지정 (역방향 index)
    ctx["doc_hr"] = await _make_doc(
        "HR 온보딩 정책.gdoc",
        file_id="hr-onboarding",
        org_path=[ctx["company"], ctx["hr_id"]],
        dept_id=ctx["hr_id"],
        summary="전사 온보딩 정책",
    )
    await _mark_related_department(ctx["doc_hr"], ctx["fe_id"])
    # 물리 귀속: qa 부서 문서 (공개) — 제품 태그 공유
    ctx["doc_qa"] = await _make_doc(
        "QA 릴리즈 검수 기준.gdoc",
        file_id="qa-release",
        org_path=[ctx["company"], ctx["qa_id"]],
        dept_id=ctx["qa_id"],
        related_products=["MediNess"],
    )
    # 승인 relation: fe 문서 → hr 부서의 다른 문서
    ctx["doc_ref"] = await _make_doc(
        "HR 취업규칙.gdoc",
        file_id="hr-rules",
        org_path=[ctx["company"], ctx["hr_id"]],
        dept_id=ctx["hr_id"],
    )
    ctx["rel_ref"] = await _make_relation(
        ctx["doc_fe"],
        ctx["doc_ref"],
        relation_type="references",
        approved_by=ctx["admin_id"],
        source_label="[[HR 취업규칙]]",
    )
    # 민감 문서 (hr 전용 PRESET) — hr 물리 귀속 + fe 관련 부서 지정 + fe 문서와 relation
    ctx["doc_secret"] = await _make_doc(
        "HR 기밀 온보딩 평가.gdoc",
        file_id="hr-secret",
        org_path=[ctx["company"], ctx["hr_id"]],
        dept_id=ctx["hr_id"],
        read_departments=[ctx["hr_id"]],
        access_logic="PRESET",
        sensitivity="sensitive",
        policy_preset="HR_RESTRICTED",
    )
    await _mark_related_department(ctx["doc_secret"], ctx["fe_id"])
    ctx["rel_secret"] = await _make_relation(
        ctx["doc_fe"],
        ctx["doc_secret"],
        relation_type="related",
        approved_by=ctx["admin_id"],
    )
    # unresolved relation candidate — 어떤 표면에도 나오면 안 된다
    await _make_unresolved_candidate(ctx["doc_fe"], "[[없는 문서]]")
    return ctx


def _doc_ids(payload: dict, key: str = "documents") -> set[int]:
    return {item["document_id"] for item in payload[key]}


# ── relation types ────────────────────────────────────────────────────────────


async def test_relation_types_exactly_four(
    client: AsyncClient, seeded: list[dict]
) -> None:
    rec = _pick_user(seeded, admin=False, department="fe")
    headers = await _bearer(client, rec["email"])
    resp = await client.get("/relation-types", headers=headers)
    assert resp.status_code == 200
    types = resp.json()["relation_types"]
    assert [t["value"] for t in types] == [
        "related",
        "references",
        "supersedes",
        "duplicate_candidate",
    ]
    assert [t["label"] for t in types] == ["관련", "참조", "대체", "중복 후보"]


# ── 물리 귀속 목록 ────────────────────────────────────────────────────────────


async def test_tree_documents_physical_only_no_logical_mixin(
    client: AsyncClient, seeded: list[dict]
) -> None:
    """관련 부서/relation으로 연결된 문서는 물리 귀속 목록에 섞이지 않는다."""
    ctx = await _setup(client, seeded)
    resp = await client.get(
        f"/tree-documents?org_node_id={ctx['fe_id']}", headers=ctx["fe"]
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _doc_ids(body) == {ctx["doc_fe"]}
    item = body["documents"][0]
    assert item["approved"] is True
    assert "fe" in item["physical_tree_path"]["display_path"]
    assert body["organization_node"]["status"] == "active"


async def test_tree_documents_tree_node_filter(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    async with _Session() as session:
        work = DocumentTreeNode(
            organization_node_id=ctx["fe_id"], type="work", name="제품 운영"
        )
        session.add(work)
        await session.commit()
        work_id = work.id
    doc_in_work = await _make_doc(
        "제품 운영 회의록.gdoc",
        file_id="fe-work-doc",
        org_path=[ctx["company"], ctx["fe_id"]],
        dept_id=ctx["fe_id"],
        tree_path=[work_id],
    )
    resp = await client.get(
        f"/tree-documents?org_node_id={ctx['fe_id']}&tree_node_id={work_id}",
        headers=ctx["fe"],
    )
    assert resp.status_code == 200
    assert _doc_ids(resp.json()) == {doc_in_work}


async def test_tree_documents_unknown_node(
    client: AsyncClient, seeded: list[dict]
) -> None:
    rec = _pick_user(seeded, admin=False, department="fe")
    headers = await _bearer(client, rec["email"])
    resp = await client.get("/tree-documents?org_node_id=999999", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "ORG_NODE_NOT_FOUND"

    resp = await client.get(
        "/tree-documents?org_node_id=1&tree_node_id=999999", headers=headers
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error_code"] == "TREE_NODE_NOT_FOUND"


async def test_tree_documents_rbac_and_state_filter(
    client: AsyncClient, seeded: list[dict]
) -> None:
    """민감 문서는 policy 부서만, trashed 문서는 explorer에서 모두 숨김."""
    ctx = await _setup(client, seeded)
    await _make_doc(
        "HR 휴지통 문서.gdoc",
        file_id="hr-trashed",
        org_path=[ctx["company"], ctx["hr_id"]],
        dept_id=ctx["hr_id"],
        drive_state="trashed",
    )
    url = f"/tree-documents?org_node_id={ctx['hr_id']}"

    fe_view = _doc_ids((await client.get(url, headers=ctx["fe"])).json())
    assert ctx["doc_secret"] not in fe_view  # 잠금 표시 없이 제거
    assert fe_view == {ctx["doc_hr"], ctx["doc_ref"]}

    hr_view = _doc_ids((await client.get(url, headers=ctx["hr"])).json())
    assert ctx["doc_secret"] in hr_view

    admin_view = _doc_ids((await client.get(url, headers=ctx["admin"])).json())
    assert ctx["doc_secret"] in admin_view  # admin은 민감 문서 포함 전체 열람
    assert not any("trashed" in str(v) for v in admin_view)  # id set — 아래 재확인
    trashed_ids = {
        d["document_id"]
        for d in (await client.get(url, headers=ctx["admin"])).json()["documents"]
        if d["drive_state"] != "active"
    }
    assert trashed_ids == set()


async def test_tree_documents_unmapped_member_sees_nothing(
    client: AsyncClient, seeded: list[dict]
) -> None:
    """department_node_id 미매핑 사용자는 일반 문서 탐색이 제한된다 (SPEC-001)."""
    ctx = await _setup(client, seeded)
    resp = await client.get(
        f"/tree-documents?org_node_id={ctx['fe_id']}", headers=ctx["unmapped"]
    )
    assert resp.status_code == 200
    assert resp.json()["documents"] == []


# ── 관련 문서 (3원천) ─────────────────────────────────────────────────────────


async def test_department_related_three_sources(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    resp = await client.get(
        f"/departments/{ctx['fe_id']}/related-documents", headers=ctx["fe"]
    )
    assert resp.status_code == 200
    body = resp.json()
    by_id = {d["document_id"]: d for d in body["documents"]}

    # 물리 귀속 문서는 관련 영역에 섞이지 않는다
    assert ctx["doc_fe"] not in by_id
    # 1) document_relation — fe 문서가 참조하는 hr 문서
    assert by_id[ctx["doc_ref"]]["source"] == "document_relation"
    assert by_id[ctx["doc_ref"]]["relation_type"] == "references"
    assert by_id[ctx["doc_ref"]]["match_reason"] == "[[HR 취업규칙]]"
    # 2) related_department — fe를 관련 부서로 지정한 hr 문서
    assert by_id[ctx["doc_hr"]]["source"] == "related_department"
    assert by_id[ctx["doc_hr"]]["relation_type"] is None
    assert "관련 부서" in by_id[ctx["doc_hr"]]["match_reason"]
    # 3) related_product — 제품 태그 공유 qa 문서
    assert by_id[ctx["doc_qa"]]["source"] == "related_product"
    assert "MediNess" in by_id[ctx["doc_qa"]]["match_reason"]
    # 민감 문서는 fe member에게 숨김 (relation/관련 부서 지정 있어도)
    assert ctx["doc_secret"] not in by_id

    # SPEC-002 flavor 동등성
    alias = await client.get(
        f"/related-documents?department_node_id={ctx['fe_id']}", headers=ctx["fe"]
    )
    assert _doc_ids(alias.json()) == set(by_id)


async def test_related_relation_type_filter_and_invalid(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    resp = await client.get(
        f"/departments/{ctx['fe_id']}/related-documents?relation_type=references",
        headers=ctx["fe"],
    )
    assert _doc_ids(resp.json()) == {ctx["doc_ref"]}

    resp = await client.get(
        f"/departments/{ctx['fe_id']}/related-documents?relation_type=bogus",
        headers=ctx["fe"],
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "INVALID_RELATION_TYPE"


async def test_related_hides_unavailable_target(
    client: AsyncClient, seeded: list[dict]
) -> None:
    """trashed/removed/out_of_scope 문서는 관련 영역에서 제거된다."""
    ctx = await _setup(client, seeded)
    trashed = await _make_doc(
        "폐기된 정책.gdoc",
        file_id="hr-gone",
        org_path=[ctx["company"], ctx["hr_id"]],
        dept_id=ctx["hr_id"],
        drive_state="removed",
    )
    await _mark_related_department(trashed, ctx["fe_id"])
    await _make_relation(
        ctx["doc_fe"], trashed, relation_type="related", approved_by=ctx["admin_id"]
    )
    resp = await client.get(
        f"/departments/{ctx['fe_id']}/related-documents", headers=ctx["fe"]
    )
    assert trashed not in _doc_ids(resp.json())


async def test_document_related_three_sources(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    # doc_fe가 qa 부서를 관련 부서로 지정 (승인 metadata)
    await _mark_related_department(ctx["doc_fe"], ctx["qa_id"])
    resp = await client.get(
        f"/documents/{ctx['doc_fe']}/related", headers=ctx["fe"]
    )
    assert resp.status_code == 200
    by_id = {d["document_id"]: d for d in resp.json()["documents"]}

    assert by_id[ctx["doc_ref"]]["source"] == "document_relation"
    assert by_id[ctx["doc_qa"]]["source"] in {"related_department", "related_product"}
    assert ctx["doc_fe"] not in by_id  # 자기 자신 제외
    assert ctx["doc_secret"] not in by_id  # 권한 없는 relation 대상 숨김

    # hr member가 보면 민감 문서 relation이 노출된다 (hr policy 만족)
    hr_view = await client.get(
        f"/documents/{ctx['doc_fe']}/related", headers=ctx["hr"]
    )
    assert ctx["doc_secret"] in _doc_ids(hr_view.json())


# ── 문서 상세 relation ────────────────────────────────────────────────────────


async def test_document_relations_approved_only_unresolved_excluded(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    resp = await client.get(
        f"/documents/{ctx['doc_fe']}/relations", headers=ctx["fe"]
    )
    assert resp.status_code == 200
    body = resp.json()
    # fe member: 승인 relation 중 readable target만 (doc_secret relation 숨김)
    assert [r["id"] for r in body["relations"]] == [ctx["rel_ref"]]
    rel = body["relations"][0]
    assert rel["relation_type"] == "references"
    assert rel["target_state"] == "active"
    assert rel["target_drive_name"] == "HR 취업규칙.gdoc"
    # unresolved candidate label은 어떤 응답에도 없다
    assert "없는 문서" not in resp.text

    # admin은 민감 문서 relation 포함 전체
    admin_resp = await client.get(
        f"/documents/{ctx['doc_fe']}/relations", headers=ctx["admin"]
    )
    assert {r["id"] for r in admin_resp.json()["relations"]} == {
        ctx["rel_ref"],
        ctx["rel_secret"],
    }
    assert "없는 문서" not in admin_resp.text


async def test_document_relations_unavailable_target(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    gone = await _make_doc(
        "지워진 참조 문서.gdoc",
        file_id="fe-gone-target",
        org_path=[ctx["company"], ctx["hr_id"]],
        dept_id=ctx["hr_id"],
        drive_state="trashed",
    )
    rel_id = await _make_relation(
        ctx["doc_fe"], gone, relation_type="supersedes", approved_by=ctx["admin_id"]
    )
    member_view = await client.get(
        f"/documents/{ctx['doc_fe']}/relations", headers=ctx["fe"]
    )
    assert rel_id not in {r["id"] for r in member_view.json()["relations"]}

    admin_view = await client.get(
        f"/documents/{ctx['doc_fe']}/relations", headers=ctx["admin"]
    )
    broken = next(
        r for r in admin_view.json()["relations"] if r["id"] == rel_id
    )
    assert broken["target_state"] == "trashed"  # 파생 상태 — 저장 없음


# ── 통합 검색 ─────────────────────────────────────────────────────────────────


async def test_search_badges_and_source_filter(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    url = f"/search/documents?q=온보딩&org_node_id={ctx['fe_id']}"
    resp = await client.get(url, headers=ctx["fe"])
    assert resp.status_code == 200
    body = resp.json()
    by_id = {r["document_id"]: r for r in body["results"]}
    assert by_id[ctx["doc_fe"]]["source_badge"] == "physical"
    assert by_id[ctx["doc_hr"]]["source_badge"] == "related"
    # 실제 physical_tree_path 표시
    assert "hr" in by_id[ctx["doc_hr"]]["physical_tree_path"]["display_path"]
    # 민감 문서(이름에 '온보딩' 포함)는 fe member 결과에서 제거
    assert ctx["doc_secret"] not in by_id

    physical_only = await client.get(url + "&source=physical", headers=ctx["fe"])
    assert {r["document_id"] for r in physical_only.json()["results"]} == {
        ctx["doc_fe"]
    }
    related_only = await client.get(url + "&source=related", headers=ctx["fe"])
    assert ctx["doc_fe"] not in {
        r["document_id"] for r in related_only.json()["results"]
    }

    # org_node_id 미지정 시 사용자 부서가 badge context
    default_ctx = await client.get("/search/documents?q=온보딩", headers=ctx["fe"])
    default_map = {
        r["document_id"]: r["source_badge"]
        for r in default_ctx.json()["results"]
    }
    assert default_map[ctx["doc_fe"]] == "physical"
    assert default_map[ctx["doc_hr"]] == "related"


async def test_search_rbac_and_empty(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    # admin은 민감 문서 검색 가능
    admin_resp = await client.get(
        "/search/documents?q=기밀", headers=ctx["admin"]
    )
    assert ctx["doc_secret"] in {
        r["document_id"] for r in admin_resp.json()["results"]
    }
    # hr member도 가능 (policy 만족), fe member는 불가
    hr_resp = await client.get("/search/documents?q=기밀", headers=ctx["hr"])
    assert ctx["doc_secret"] in {r["document_id"] for r in hr_resp.json()["results"]}
    fe_resp = await client.get("/search/documents?q=기밀", headers=ctx["fe"])
    assert fe_resp.json()["results"] == []

    # SEARCH_EMPTY — 200 + 빈 배열 (FE가 '검색 결과가 없습니다.' 표시)
    empty = await client.get(
        "/search/documents?q=존재하지않는검색어", headers=ctx["fe"]
    )
    assert empty.status_code == 200
    assert empty.json() == {"query": "존재하지않는검색어", "results": [], "total": 0}
    blank = await client.get("/search/documents?q=%20", headers=ctx["fe"])
    assert blank.json()["results"] == []

    # summary 검색도 지원
    summary_hit = await client.get(
        "/search/documents?q=신규 입사자", headers=ctx["fe"]
    )
    assert ctx["doc_fe"] in {
        r["document_id"] for r in summary_hit.json()["results"]
    }


# ── 문서 상세 확장 ────────────────────────────────────────────────────────────


async def test_document_detail_metadata_and_pending_badge(
    client: AsyncClient, seeded: list[dict]
) -> None:
    ctx = await _setup(client, seeded)
    async with _Session() as session:
        doc_type = DocumentType(name="요구사항 정의서", normalized_name="요구사항정의서")
        session.add(doc_type)
        await session.flush()
        await session.execute(
            sa.update(Document)
            .where(Document.id == ctx["doc_fe"])
            .values(document_type_id=doc_type.id)
        )
        session.add(
            MetadataCandidate(
                document_id=ctx["doc_fe"],
                state="pending",
                read_capability="content_read",
                candidate_metadata={"document_type": "회의록"},
                candidate_fingerprint={"md5": "candidate"},
            )
        )
        await session.commit()
    await _mark_related_department(ctx["doc_fe"], ctx["qa_id"])

    member = await client.get(f"/documents/{ctx['doc_fe']}", headers=ctx["fe"])
    assert member.status_code == 200
    body = member.json()
    assert body["mirror"]["drive_name"] == "FE 온보딩 체크리스트.gdoc"
    assert body["document_type_name"] == "요구사항 정의서"
    assert body["approved"] is True
    assert "fe" in body["physical_tree_path"]["display_path"]
    assert [d["name"] for d in body["related_departments"]] == ["qa"]
    assert body["related_products"] == ["MediNess"]
    assert body["summary"] == "FE 신규 입사자 온보딩 체크리스트"
    # member에게 후보는 확정값처럼도, badge로도 노출되지 않는다
    assert body["pending_candidate"] is None
    assert "회의록" not in member.text

    admin = await client.get(f"/documents/{ctx['doc_fe']}", headers=ctx["admin"])
    assert admin.json()["pending_candidate"] is not None
    assert admin.json()["pending_candidate"]["state"] == "pending"


async def test_document_detail_rbac_direct_access(
    client: AsyncClient, seeded: list[dict]
) -> None:
    """권한 없는 문서 직접 URL 접근 — 404 톤 DOCUMENT_NOT_READABLE."""
    ctx = await _setup(client, seeded)
    fe_view = await client.get(
        f"/documents/{ctx['doc_secret']}", headers=ctx["fe"]
    )
    assert fe_view.status_code == 404
    assert fe_view.json()["detail"]["error_code"] == "DOCUMENT_NOT_READABLE"

    hr_view = await client.get(
        f"/documents/{ctx['doc_secret']}", headers=ctx["hr"]
    )
    assert hr_view.status_code == 200

    admin_view = await client.get(
        f"/documents/{ctx['doc_secret']}", headers=ctx["admin"]
    )
    assert admin_view.status_code == 200

    missing = await client.get("/documents/999999", headers=ctx["fe"])
    assert missing.status_code == 404
    assert missing.json()["detail"]["error_code"] == "DOCUMENT_NOT_FOUND"


# ── RBAC 숨김 5개 표면 교차 검증 (Phase 4 e2e) ───────────────────────────────


async def test_rbac_hidden_across_five_surfaces(
    client: AsyncClient, seeded: list[dict]
) -> None:
    """민감(PRESET) 문서가 목록/관련/검색/상세 relation/상세 전부에서 숨겨진다."""
    ctx = await _setup(client, seeded)
    secret = ctx["doc_secret"]

    # 1) 물리 귀속 목록 (hr 트리)
    tree = await client.get(
        f"/tree-documents?org_node_id={ctx['hr_id']}", headers=ctx["fe"]
    )
    assert secret not in _doc_ids(tree.json())
    # 2) 관련 문서 (fe 부서 기준 — 관련 부서 지정 + relation 둘 다 있어도)
    related = await client.get(
        f"/departments/{ctx['fe_id']}/related-documents", headers=ctx["fe"]
    )
    assert secret not in _doc_ids(related.json())
    # 3) 검색
    search = await client.get("/search/documents?q=기밀", headers=ctx["fe"])
    assert search.json()["results"] == []
    # 4) 상세 relation (fe 문서의 relation 대상)
    relations = await client.get(
        f"/documents/{ctx['doc_fe']}/relations", headers=ctx["fe"]
    )
    assert secret not in {
        r["target_document_id"] for r in relations.json()["relations"]
    }
    # 5) 상세 직접 접근
    detail = await client.get(f"/documents/{secret}", headers=ctx["fe"])
    assert detail.status_code == 404
    assert detail.json()["detail"]["error_code"] == "DOCUMENT_NOT_READABLE"

    # 같은 표면에서 hr member/admin은 열람 가능 (SPEC-001 AC)
    for headers in (ctx["hr"], ctx["admin"]):
        tree_ok = await client.get(
            f"/tree-documents?org_node_id={ctx['hr_id']}", headers=headers
        )
        assert secret in _doc_ids(tree_ok.json())
        detail_ok = await client.get(f"/documents/{secret}", headers=headers)
        assert detail_ok.status_code == 200
