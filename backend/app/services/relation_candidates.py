"""relation 후보 처리 service — WORK-005 (SPEC-005 U-6/S-5, DEC-021).

- resolve: admin target 지정으로만 확정 가능 상태(pending)가 된다.
- hold: unresolved 유지 — 확정 graph 미반영.
- remove: 후보 제거(removed).
- rematch: title/drive_name 재검색 "제안"만 — 확정은 resolve로만 (DEC-021).
- 어떤 경로로도 새 document row를 자동 생성하지 않는다.
확정 graph(document_relations) 반영은 metadata candidate approve 흐름
(services/approval._apply_resolved_relations) 소관이다.
"""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import RelationCandidate
from app.models.document import Document
from app.repos.documents import DocumentRepository
from app.repos.relation_candidates import RelationCandidateRepository
from app.services.approval import (
    ApprovalDocumentUnavailableError,
    CandidateNotFoundError,
    CandidateNotPendingError,
    RelationTargetRequiredError,
)

_WIKILINK = re.compile(r"^\[\[(.+?)\]\]$")

# 처리 가능한 미종결 상태 (approved/removed는 종결)
_OPEN_STATES = ("pending", "unresolved")


class RelationCandidateService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._relations = RelationCandidateRepository(session)
        self._docs = DocumentRepository(session)

    async def _get_open(self, candidate_id: int) -> RelationCandidate:
        row = await self._relations.get(candidate_id)
        if row is None:
            raise CandidateNotFoundError
        if row.state not in _OPEN_STATES:
            raise CandidateNotPendingError
        return row

    async def target_name(self, row: RelationCandidate) -> str | None:
        if row.target_document_id is None:
            return None
        target = await self._docs.get(row.target_document_id)
        return target.drive_name if target else None

    async def resolve(
        self, candidate_id: int, *, target_document_id: int | None, admin_id: int
    ) -> RelationCandidate:
        """target 지정 → pending 전환. target 없이는 확정 불가 (DEC-021)."""
        row = await self._get_open(candidate_id)
        if target_document_id is None:
            raise RelationTargetRequiredError
        target = await self._docs.get(target_document_id)
        if (
            target is None
            or target.drive_state != "active"
            or target.id == row.source_document_id
        ):
            # 삭제/범위 제외 문서·자기 자신은 target 불가
            raise ApprovalDocumentUnavailableError
        updated = await self._relations.set_target(
            candidate_id, target_document_id=target.id, resolved_by=admin_id
        )
        assert updated is not None
        await self._session.commit()
        # onupdate 서버 생성 컬럼(updated_at) 재조회 — 응답 직렬화용
        await self._session.refresh(updated)
        return updated

    async def hold(self, candidate_id: int, *, admin_id: int) -> RelationCandidate:
        """보류 — unresolved 유지 (확정 graph 미반영)."""
        row = await self._get_open(candidate_id)
        updated = await self._relations.set_state(
            row.id, state="unresolved", resolved_by=admin_id
        )
        assert updated is not None
        await self._session.commit()
        await self._session.refresh(updated)
        return updated

    async def remove(self, candidate_id: int, *, admin_id: int) -> RelationCandidate:
        row = await self._get_open(candidate_id)
        updated = await self._relations.set_state(
            row.id, state="removed", resolved_by=admin_id
        )
        assert updated is not None
        await self._session.commit()
        await self._session.refresh(updated)
        return updated

    async def rematch(
        self, candidate_id: int
    ) -> tuple[RelationCandidate, Document | None]:
        """title/drive_name 재검색 제안 — 상태 변경 없음, 확정은 resolve로만."""
        row = await self._get_open(candidate_id)
        name = row.raw_label.strip()
        match = _WIKILINK.match(name)
        if match:
            name = match.group(1).strip()
        suggestion = await self._docs.find_by_drive_name(
            name, exclude_document_id=row.source_document_id
        )
        return row, suggestion
