"""document_path_histories repository — WORK-002 (SPEC-002, DEC-015).

append-only: INSERT와 SELECT만 제공한다. UPDATE/DELETE 메서드를 만들지 않는다.
stmt는 이 repo 안에서만.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentPathHistory


class PathHistoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        document_id: int,
        previous_path: dict,
        new_path: dict,
        changed_by: int,
        reason: str,
    ) -> DocumentPathHistory:
        row = DocumentPathHistory(
            document_id=document_id,
            previous_path=previous_path,
            new_path=new_path,
            changed_by=changed_by,
            reason=reason,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_by_document(self, document_id: int) -> list[DocumentPathHistory]:
        """최근 변경이 먼저 오도록 반환한다."""
        rows = (
            await self._session.scalars(
                sa.select(DocumentPathHistory)
                .where(DocumentPathHistory.document_id == document_id)
                .order_by(DocumentPathHistory.id.desc())
            )
        ).all()
        return list(rows)
