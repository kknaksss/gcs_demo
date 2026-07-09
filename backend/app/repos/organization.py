"""organization_nodes repository — seed(WORK-001) + 트리 CRUD(WORK-002).

seed가 멱등하게 회사 root/부서 노드를 만들고, WORK-002가 전체 트리 조회/생성/
이름·상태 변경을 얹는다. hard delete 메서드는 두지 않는다 (DEC-013).
stmt는 이 repo 안에서만.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import OrganizationNode


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_company_root(self) -> OrganizationNode | None:
        return await self._session.scalar(
            sa.select(OrganizationNode).where(OrganizationNode.type == "company")
        )

    async def get_or_create_company(self, name: str) -> OrganizationNode:
        row = await self.get_company_root()
        if row is None:
            row = OrganizationNode(type="company", name=name, status="active")
            self._session.add(row)
            await self._session.flush()
        return row

    async def get_department_by_name(
        self, name: str, *, parent_id: int
    ) -> OrganizationNode | None:
        return await self._session.scalar(
            sa.select(OrganizationNode).where(
                OrganizationNode.type == "department",
                OrganizationNode.parent_id == parent_id,
                OrganizationNode.name == name,
            )
        )

    async def get_or_create_department(
        self, name: str, *, parent_id: int
    ) -> OrganizationNode:
        row = await self.get_department_by_name(name, parent_id=parent_id)
        if row is None:
            row = OrganizationNode(
                type="department", name=name, parent_id=parent_id, status="active"
            )
            self._session.add(row)
            await self._session.flush()
        return row

    async def get_node(self, node_id: int) -> OrganizationNode | None:
        return await self._session.get(OrganizationNode, node_id)

    # ------------------------------------------------------------------
    # WORK-002 — 트리 CRUD (SPEC-002)
    # ------------------------------------------------------------------

    async def list_nodes(self) -> list[OrganizationNode]:
        """전체 조직 노드 (inactive 포함 — 기존 문서 조회 유지, DEC-013)."""
        rows = (
            await self._session.scalars(
                sa.select(OrganizationNode).order_by(OrganizationNode.id.asc())
            )
        ).all()
        return list(rows)

    async def get_nodes_by_ids(
        self, node_ids: list[int]
    ) -> dict[int, OrganizationNode]:
        if not node_ids:
            return {}
        rows = (
            await self._session.scalars(
                sa.select(OrganizationNode).where(OrganizationNode.id.in_(node_ids))
            )
        ).all()
        return {row.id: row for row in rows}

    async def create_node(
        self, *, type: str, name: str, parent_id: int | None
    ) -> OrganizationNode:
        row = OrganizationNode(
            type=type, name=name, parent_id=parent_id, status="active"
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def update_node(
        self,
        node_id: int,
        *,
        name: str | None = None,
        status: str | None = None,
    ) -> OrganizationNode | None:
        """이름/상태 변경만. id 유지 rename (SPEC-002 Implementation Rules)."""
        row = await self._session.get(OrganizationNode, node_id)
        if row is None:
            return None
        if name is not None:
            row.name = name
        if status is not None:
            row.status = status
        await self._session.flush()
        return row

    async def list_departments(self) -> list[OrganizationNode]:
        rows = (
            await self._session.scalars(
                sa.select(OrganizationNode)
                .where(OrganizationNode.type == "department")
                .order_by(OrganizationNode.name.asc())
            )
        ).all()
        return list(rows)

    async def descendant_department_ids(self, node_id: int) -> list[int]:
        """department 노드면 자신 + 하위 team 노드 id 목록. team 노드면 자신만.

        RBAC read_departments 매칭에서 'department면 하위 팀 포함'을 위해 쓴다
        (SPEC-001 RBAC Rules). v1 조직도는 company>department>team 3계층이라
        직접 자식 team까지만 내려가면 충분하다.
        """
        node = await self._session.get(OrganizationNode, node_id)
        if node is None:
            return []
        if node.type == "team":
            return [node.id]
        child_ids = (
            await self._session.scalars(
                sa.select(OrganizationNode.id).where(
                    OrganizationNode.parent_id == node_id
                )
            )
        ).all()
        return [node_id, *child_ids]
