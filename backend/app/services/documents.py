"""문서 record service — WORK-003 (SPEC-003).

- composite fingerprint 조립/비교 유틸 (DEC-023): 필수 4요소
  (drive_file_id/drive_modified_time/drive_name/mime_type) + version /
  content_fingerprint 옵션.
- 문서 상세/감사 조회 최소 표면 (승인 metadata 표시·RBAC 필터 완성은 WORK-005/006).
- mark_candidates_stale: fingerprint 변경 시 pending 후보 stale 전환 훅 —
  WORK-004가 이 훅 뒤에 재분석 enqueue를 연결한다 (Internal Interface Contract).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import MetadataCandidate
from app.models.document import Document
from app.repos.candidates import MetadataCandidateRepository
from app.repos.documents import DocumentRepository

HIDDEN_DRIVE_STATES = ("trashed", "removed", "out_of_scope")

# SPEC-003 AC — fingerprint 필수 4요소. `mime_type`은 mirror `drive_mime_type`과 동일 값.
FINGERPRINT_REQUIRED_KEYS = (
    "drive_file_id",
    "drive_modified_time",
    "drive_name",
    "mime_type",
)


class DocumentNotFoundError(Exception):
    """DOCUMENT_NOT_FOUND — 문서 없음."""


class DocumentHiddenError(Exception):
    """DOCUMENT_NOT_READABLE — 일반 사용자 숨김 상태(soft deleted) 문서."""


class FingerprintComponentMissingError(Exception):
    """fingerprint 필수 구성요소 누락 — 저장 거부 (SPEC-003 AC)."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"fingerprint components missing: {', '.join(missing)}")
        self.missing = missing


def build_fingerprint(
    *,
    drive_file_id: str,
    drive_modified_time: dt.datetime | str | None,
    drive_name: str,
    mime_type: str,
    version: str | None = None,
    content_fingerprint: str | None = None,
) -> dict:
    """composite fingerprint 조립 (DEC-023). 필수 4요소 누락 시 저장 거부."""
    if isinstance(drive_modified_time, dt.datetime):
        drive_modified_time = drive_modified_time.isoformat()
    fingerprint = {
        "drive_file_id": drive_file_id,
        "drive_modified_time": drive_modified_time,
        "drive_name": drive_name,
        "mime_type": mime_type,
    }
    missing = [k for k in FINGERPRINT_REQUIRED_KEYS if not fingerprint[k]]
    if missing:
        raise FingerprintComponentMissingError(missing)
    if version is not None:
        fingerprint["version"] = str(version)
    if content_fingerprint is not None:
        # 본문을 읽은 경우에만 포함 — 원문 자체가 아니라 hash만 (DEC-019/023).
        fingerprint["content_fingerprint"] = content_fingerprint
    return fingerprint


def fingerprint_changed(current: dict | None, new: dict) -> bool:
    """stale 판정 비교 — 본문을 읽은 경우 content_fingerprint도 포함된 dict 동등 비교."""
    return current != new


class DocumentsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._docs = DocumentRepository(session)
        self._candidates = MetadataCandidateRepository(session)

    # ------------------------------------------------------------------
    # 조회 최소 표면 (SPEC-003 API Contract 일부 — WORK-003 범위)
    # ------------------------------------------------------------------

    async def get_document(self, document_id: int, *, is_admin: bool) -> Document:
        """문서 상세. 일반 사용자에게 soft deleted 문서는 숨긴다 (U-1).

        RBAC read policy 평가 완성은 WORK-006 — 여기서는 상태 숨김만 적용한다.
        """
        document = await self._docs.get(document_id)
        if document is None:
            raise DocumentNotFoundError
        if not is_admin and document.drive_state in HIDDEN_DRIVE_STATES:
            # member에게는 존재 자체를 숨긴다 (DOCUMENT_NOT_READABLE 표면).
            raise DocumentHiddenError
        return document

    async def get_drive_mirror(self, document_id: int) -> Document:
        """admin 전용 Drive mirror 조회 — soft deleted 상태도 그대로 노출."""
        document = await self._docs.get(document_id)
        if document is None:
            raise DocumentNotFoundError
        return document

    async def list_admin_documents(
        self, *, drive_state: str | None, limit: int, offset: int
    ) -> tuple[list[Document], int]:
        return await self._docs.list_admin(
            drive_state=drive_state, limit=limit, offset=offset
        )

    # ------------------------------------------------------------------
    # WORK-004 연결 지점 — stale/blocked 훅
    # ------------------------------------------------------------------

    async def mark_candidates_stale(
        self, document_id: int, new_fingerprint: dict, *, reason: str | None = None
    ) -> list[MetadataCandidate]:
        """fingerprint 변경 시 pending 후보 stale 전환 (SPEC-003 S-4).

        재분석 enqueue는 이 함수의 소관이 아니다 — WORK-004가 이 훅 뒤에
        `reanalysis_enqueued`를 배선한다.
        """
        staled: list[MetadataCandidate] = []
        message = reason or "Drive fingerprint changed"
        for candidate in await self._candidates.list_pending_by_document(document_id):
            row = await self._candidates.mark_stale(candidate.id, reason=message)
            if row is not None:
                staled.append(row)
        return staled

    async def mark_candidates_blocked(
        self, document_id: int, *, reason: str
    ) -> list[MetadataCandidate]:
        """문서 unavailable 전환 시 pending 후보 승인 차단 (SPEC-004 S-4)."""
        blocked: list[MetadataCandidate] = []
        for candidate in await self._candidates.list_pending_by_document(document_id):
            row = await self._candidates.mark_blocked(candidate.id, reason=reason)
            if row is not None:
                blocked.append(row)
        return blocked
