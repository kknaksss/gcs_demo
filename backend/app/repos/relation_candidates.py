"""relation_candidates / document_relations repository — WORK-004 저장 +
WORK-005 처리(resolve/hold/remove/승인 반영) (SPEC-005/006/007, DEC-021).

AI output의 wikilink 후보 저장 전용. target을 찾지 못하면 `unresolved`로
남기고 새 document row는 절대 만들지 않는다 (DEC-021). 전이 규칙은
services/relation_candidates가 담당한다. stmt는 이 repo 안에서만 (ARCH-001 §4).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import DocumentRelation, RelationCandidate


class RelationCandidateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_open(
        self,
        *,
        source_document_id: int,
        raw_label: str,
        suggested_relation_type: str,
    ) -> RelationCandidate | None:
        """중복 저장 방지 — 같은 (source, label, type)의 미처리 후보."""
        return await self._session.scalar(
            sa.select(RelationCandidate).where(
                RelationCandidate.source_document_id == source_document_id,
                RelationCandidate.raw_label == raw_label,
                RelationCandidate.suggested_relation_type == suggested_relation_type,
                RelationCandidate.state.in_(("pending", "unresolved")),
            )
        )

    async def create(
        self,
        *,
        source_document_id: int,
        raw_label: str,
        suggested_relation_type: str,
        target_document_id: int | None,
        state: str,
    ) -> RelationCandidate:
        row = RelationCandidate(
            source_document_id=source_document_id,
            raw_label=raw_label,
            suggested_relation_type=suggested_relation_type,
            target_document_id=target_document_id,
            state=state,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_by_source(
        self, source_document_id: int
    ) -> list[RelationCandidate]:
        rows = await self._session.scalars(
            sa.select(RelationCandidate)
            .where(RelationCandidate.source_document_id == source_document_id)
            .order_by(RelationCandidate.id.asc())
        )
        return list(rows)

    # ------------------------------------------------------------------
    # WORK-005 — 승인 게이트 처리 표면
    # ------------------------------------------------------------------

    async def get(self, candidate_id: int) -> RelationCandidate | None:
        return await self._session.get(RelationCandidate, candidate_id)

    async def set_target(
        self, candidate_id: int, *, target_document_id: int, resolved_by: int
    ) -> RelationCandidate | None:
        """admin target 지정 — pending 전환 (SPEC-005 U-6 `대상 선택`)."""
        row = await self._session.get(RelationCandidate, candidate_id)
        if row is None:
            return None
        row.target_document_id = target_document_id
        row.state = "pending"
        row.resolved_by = resolved_by
        await self._session.flush()
        return row

    async def set_state(
        self,
        candidate_id: int,
        *,
        state: str,
        resolved_by: int | None = None,
    ) -> RelationCandidate | None:
        row = await self._session.get(RelationCandidate, candidate_id)
        if row is None:
            return None
        row.state = state
        if resolved_by is not None:
            row.resolved_by = resolved_by
        await self._session.flush()
        return row

    async def list_pending_with_target(
        self, source_document_id: int
    ) -> list[RelationCandidate]:
        """metadata candidate 승인 시 확정 graph에 반영할 후보 (target 있는 pending)."""
        rows = await self._session.scalars(
            sa.select(RelationCandidate)
            .where(
                RelationCandidate.source_document_id == source_document_id,
                RelationCandidate.state == "pending",
                RelationCandidate.target_document_id.is_not(None),
            )
            .order_by(RelationCandidate.id.asc())
        )
        return list(rows)


class DocumentRelationRepository:
    """확정 relation graph — (source, target, type) unique (SPEC-003/006)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_key(
        self,
        *,
        source_document_id: int,
        target_document_id: int,
        relation_type: str,
    ) -> DocumentRelation | None:
        return await self._session.scalar(
            sa.select(DocumentRelation).where(
                DocumentRelation.source_document_id == source_document_id,
                DocumentRelation.target_document_id == target_document_id,
                DocumentRelation.relation_type == relation_type,
            )
        )

    # ------------------------------------------------------------------
    # WORK-006 — explorer 조회 (approved relation만 저장되는 테이블이다)
    # ------------------------------------------------------------------

    async def list_by_document(self, document_id: int) -> list[DocumentRelation]:
        """문서가 source 또는 target인 approved relation (SPEC-006 U-4)."""
        rows = await self._session.scalars(
            sa.select(DocumentRelation)
            .where(
                sa.or_(
                    DocumentRelation.source_document_id == document_id,
                    DocumentRelation.target_document_id == document_id,
                )
            )
            .order_by(DocumentRelation.id.asc())
        )
        return list(rows)

    async def list_touching(self, document_ids: list[int]) -> list[DocumentRelation]:
        """document_ids 중 하나가 endpoint인 approved relation (부서 기준 관련 문서)."""
        if not document_ids:
            return []
        rows = await self._session.scalars(
            sa.select(DocumentRelation)
            .where(
                sa.or_(
                    DocumentRelation.source_document_id.in_(document_ids),
                    DocumentRelation.target_document_id.in_(document_ids),
                )
            )
            .order_by(DocumentRelation.id.asc())
        )
        return list(rows)

    async def create(
        self,
        *,
        source_document_id: int,
        target_document_id: int,
        relation_type: str,
        source_label: str | None,
        approved_by: int,
    ) -> DocumentRelation:
        row = DocumentRelation(
            source_document_id=source_document_id,
            target_document_id=target_document_id,
            relation_type=relation_type,
            source_label=source_label,
            approved_by=approved_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row
