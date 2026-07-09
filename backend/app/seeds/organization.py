"""기본 조직도 seed — WORK-001 Phase 3 (회사 root + department 노드).

DEC-004 기본 트리. seed `department` 분포(ax/be/design/fe/hr/plan/qa/rnd)를 부서 노드로
만든다. 멱등: 이미 있으면 다시 만들지 않는다. team 노드/CRUD는 WORK-002.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repos.organization import OrganizationRepository

COMPANY_NAME = "메디솔브"


async def seed_organization(
    session: AsyncSession, department_names: list[str]
) -> dict[str, int]:
    """회사 root + 부서 노드를 멱등 생성하고 {부서명: node_id} 매핑을 돌려준다."""
    repo = OrganizationRepository(session)
    company = await repo.get_or_create_company(COMPANY_NAME)
    mapping: dict[str, int] = {}
    for name in sorted({n for n in department_names if n}):
        node = await repo.get_or_create_department(name, parent_id=company.id)
        mapping[name] = node.id
    return mapping
