"""document_tree_nodes repository — WORK-002 (SPEC-002 업무/문서종류 트리).

계층 validation은 service(document_tree) 책임, 여기는 persistence만.
hard delete 메서드는 두지 않는다 (inactive 전환만). stmt는 이 repo 안에서만.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import DocumentTreeNode


class DocumentTreeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_node(self, node_id: int) -> DocumentTreeNode | None:
        return await self._session.get(DocumentTreeNode, node_id)

    async def list_nodes(self) -> list[DocumentTreeNode]:
        rows = (
            await self._session.scalars(
                sa.select(DocumentTreeNode).order_by(DocumentTreeNode.id.asc())
            )
        ).all()
        return list(rows)

    async def get_nodes_by_ids(
        self, node_ids: list[int]
    ) -> dict[int, DocumentTreeNode]:
        if not node_ids:
            return {}
        rows = (
            await self._session.scalars(
                sa.select(DocumentTreeNode).where(DocumentTreeNode.id.in_(node_ids))
            )
        ).all()
        return {row.id: row for row in rows}

    async def create_node(
        self,
        *,
        organization_node_id: int,
        parent_id: int | None,
        type: str,
        document_type_id: int | None,
        name: str,
    ) -> DocumentTreeNode:
        row = DocumentTreeNode(
            organization_node_id=organization_node_id,
            parent_id=parent_id,
            type=type,
            document_type_id=document_type_id,
            name=name,
            status="active",
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
    ) -> DocumentTreeNode | None:
        row = await self._session.get(DocumentTreeNode, node_id)
        if row is None:
            return None
        if name is not None:
            row.name = name
        if status is not None:
            row.status = status
        await self._session.flush()
        return row
