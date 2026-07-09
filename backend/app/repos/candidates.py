"""metadata_candidates repository — WORK-003(stale/blocked 전환) + WORK-004(pending 생성)
+ WORK-005(승인 게이트 조회/승인/거절 전이).

WORK-004는 검증 통과한 AI 결과를 pending으로 저장한다 — 문서당 pending 1개
(부분 unique), (document_id, candidate_fingerprint) 멱등 (SPEC-007
Implementation Rules). 전이 규칙(승인=pending만, 거절=pending/stale)은
services/approval이 담당하고 여기는 조회/필드 갱신만 제공한다.
stmt는 이 repo 안에서만 (ARCH-001 §4).
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import MetadataCandidate


class MetadataCandidateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, candidate_id: int) -> MetadataCandidate | None:
        return await self._session.get(MetadataCandidate, candidate_id)

    async def list_for_approval(
        self,
        *,
        state: str | None = None,
        read_capability: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MetadataCandidate], int]:
        """승인 게이트 후보 큐 — 최신 후보 우선 (SPEC-005 U-1).

        state 필터는 원장 5개 enum, read_capability는 metadata_only 필터용.
        재분석 표시 상태(reanalyzing 등)는 파생값이라 여기서 필터하지 않는다.
        """
        where = []
        if state is not None:
            where.append(MetadataCandidate.state == state)
        if read_capability is not None:
            where.append(MetadataCandidate.read_capability == read_capability)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(MetadataCandidate).where(*where)
        )
        rows = await self._session.scalars(
            sa.select(MetadataCandidate)
            .where(*where)
            .order_by(MetadataCandidate.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total or 0)

    async def mark_approved(
        self, candidate_id: int, *, approved_by: int
    ) -> MetadataCandidate | None:
        """pending → approved 종결 (SPEC-005 S-1). 전제 검사는 service 소관."""
        row = await self._session.get(MetadataCandidate, candidate_id)
        if row is None or row.state != "pending":
            return None
        row.state = "approved"
        row.approved_by = approved_by
        row.approved_at = dt.datetime.now(dt.timezone.utc)
        await self._session.flush()
        return row

    async def mark_rejected(
        self, candidate_id: int, *, reason: str | None = None
    ) -> MetadataCandidate | None:
        """pending/stale → rejected (SPEC-005 State Lifecycle)."""
        row = await self._session.get(MetadataCandidate, candidate_id)
        if row is None or row.state not in ("pending", "stale"):
            return None
        row.state = "rejected"
        if reason is not None:
            row.reason = reason
        await self._session.flush()
        return row

    async def list_pending_by_document(
        self, document_id: int
    ) -> list[MetadataCandidate]:
        rows = await self._session.scalars(
            sa.select(MetadataCandidate).where(
                MetadataCandidate.document_id == document_id,
                MetadataCandidate.state == "pending",
            )
        )
        return list(rows)

    async def mark_stale(
        self, candidate_id: int, *, reason: str
    ) -> MetadataCandidate | None:
        row = await self._session.get(MetadataCandidate, candidate_id)
        if row is None or row.state != "pending":
            return None
        row.state = "stale"
        row.reason = reason
        await self._session.flush()
        return row

    async def mark_blocked(
        self, candidate_id: int, *, reason: str
    ) -> MetadataCandidate | None:
        """문서 unavailable(trashed/removed/out_of_scope) 시 승인 진행 차단."""
        row = await self._session.get(MetadataCandidate, candidate_id)
        if row is None or row.state != "pending":
            return None
        row.state = "blocked"
        row.reason = reason
        await self._session.flush()
        return row

    # ------------------------------------------------------------------
    # WORK-004 — pending 후보 생성/갱신 (SPEC-007 결과 저장)
    # ------------------------------------------------------------------

    async def get_pending_by_document(
        self, document_id: int
    ) -> MetadataCandidate | None:
        """문서당 pending 후보는 1개 (부분 unique index)."""
        return await self._session.scalar(
            sa.select(MetadataCandidate).where(
                MetadataCandidate.document_id == document_id,
                MetadataCandidate.state == "pending",
            )
        )

    async def create_pending(
        self,
        *,
        document_id: int,
        read_capability: str,
        candidate_metadata: dict,
        candidate_fingerprint: dict,
        reason: str | None = None,
    ) -> MetadataCandidate:
        row = MetadataCandidate(
            document_id=document_id,
            state="pending",
            read_capability=read_capability,
            candidate_metadata=candidate_metadata,
            candidate_fingerprint=candidate_fingerprint,
            reason=reason,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def replace_pending_payload(
        self,
        candidate_id: int,
        *,
        read_capability: str,
        candidate_metadata: dict,
        reason: str | None = None,
    ) -> MetadataCandidate | None:
        """같은 candidate_fingerprint 재결과 멱등 갱신 — row는 유지한다."""
        row = await self._session.get(MetadataCandidate, candidate_id)
        if row is None or row.state != "pending":
            return None
        row.read_capability = read_capability
        row.candidate_metadata = candidate_metadata
        row.reason = reason
        await self._session.flush()
        return row
