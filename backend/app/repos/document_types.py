"""document_types repository — WORK-002 (조회) + WORK-005 (admin 추가, DEC-007).

전사 공통 카탈로그. 추가는 정규화 이름(normalized_name) unique — 중복 판정은
services/approval이 이 repo의 get_by_normalized로 수행한다.
stmt는 이 repo 안에서만.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import DocumentType


class DocumentTypeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, type_id: int) -> DocumentType | None:
        return await self._session.get(DocumentType, type_id)

    async def list_all(self) -> list[DocumentType]:
        rows = (
            await self._session.scalars(
                sa.select(DocumentType).order_by(DocumentType.name.asc())
            )
        ).all()
        return list(rows)

    async def get_by_normalized(self, normalized_name: str) -> DocumentType | None:
        return await self._session.scalar(
            sa.select(DocumentType).where(
                DocumentType.normalized_name == normalized_name
            )
        )

    async def create(
        self, *, name: str, normalized_name: str, created_by: int | None
    ) -> DocumentType:
        row = DocumentType(
            name=name, normalized_name=normalized_name, created_by=created_by
        )
        self._session.add(row)
        await self._session.flush()
        return row
