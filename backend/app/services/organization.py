"""조직도 service — WORK-002 Phase 1 (SPEC-002 U-4, Validation 표).

계층 규칙: company root 1개, department는 company 하위, team은 department 하위.
inactive 전환만 허용하고 hard delete 경로는 만들지 않는다 (DEC-013).
transaction boundary는 service (ARCH-001 §4).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import OrganizationNode
from app.repos.organization import OrganizationRepository


class OrgNodeNotFoundError(Exception):
    """ORG_NODE_NOT_FOUND — 대상/부모 조직 노드 없음."""


class OrgNodeInactiveError(Exception):
    """ORG_NODE_INACTIVE — 비활성 조직/트리 노드는 새 귀속 대상 선택 불가."""


class InvalidTreeDepthError(Exception):
    """INVALID_TREE_DEPTH — 허용되지 않는 조직/트리 계층."""


# 자식 type -> 요구되는 부모 type (SPEC-002 Validation 표)
_REQUIRED_PARENT_TYPE = {"department": "company", "team": "department"}


class OrganizationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._orgs = OrganizationRepository(session)

    async def get_tree(self) -> list[OrganizationNode]:
        """inactive 포함 전체 노드 — 기존 문서 조회는 유지된다 (DEC-013)."""
        return await self._orgs.list_nodes()

    async def create_node(
        self, *, type: str, name: str, parent_id: int | None
    ) -> OrganizationNode:
        if type == "company":
            # company는 root 1개만 (SPEC-002 Validation).
            if parent_id is not None or await self._orgs.get_company_root() is not None:
                raise InvalidTreeDepthError
        else:
            if parent_id is None:
                raise InvalidTreeDepthError
            parent = await self._orgs.get_node(parent_id)
            if parent is None:
                raise OrgNodeNotFoundError
            if parent.type != _REQUIRED_PARENT_TYPE[type]:
                raise InvalidTreeDepthError
            if parent.status != "active":
                raise OrgNodeInactiveError
        node = await self._orgs.create_node(type=type, name=name, parent_id=parent_id)
        await self._session.commit()
        return node

    async def update_node(
        self,
        node_id: int,
        *,
        name: str | None = None,
        status: str | None = None,
    ) -> OrganizationNode:
        """이름 변경(id 유지) / active·inactive 전환. hard delete 없음."""
        node = await self._orgs.update_node(node_id, name=name, status=status)
        if node is None:
            raise OrgNodeNotFoundError
        await self._session.commit()
        return node
