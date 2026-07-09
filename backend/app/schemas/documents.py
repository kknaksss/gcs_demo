"""SPEC-003 문서 record API schema — WORK-003 최소 표면.

- GET /documents/{id} (mirror+state), GET /documents/{id}/drive-mirror (admin),
  GET /admin/documents (상태별 감사 목록).
승인 metadata 표시 완성/RBAC 필터는 WORK-005/006.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel

from app.schemas.document_tree import PhysicalPathOut

DriveState = Literal["active", "trashed", "removed", "out_of_scope"]


class DriveMirrorOut(BaseModel):
    """Drive-derived field 묶음 — mirror는 Drive sync가 우선권을 가진다."""

    source_provider: str
    drive_file_id: str
    drive_name: str
    drive_web_url: str | None = None
    drive_mime_type: str
    drive_state: DriveState
    drive_modified_time: dt.datetime | None = None
    drive_fingerprint: dict


class DocumentOut(BaseModel):
    """문서 상세 최소 표면 — mirror와 approved 필드를 구분해 응답한다 (SPEC-003 AC)."""

    id: int
    mirror: DriveMirrorOut
    # approved metadata (WORK-005 승인 전까지 대부분 null)
    document_type_id: int | None = None
    owning_department_node_id: int | None = None
    organization_path: list[int] | None = None
    tree_path: list[int] | None = None
    access_logic: str
    sensitivity: str
    policy_preset: str | None = None
    summary: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class RelatedDepartmentOut(BaseModel):
    node_id: int
    name: str


class PendingCandidateOut(BaseModel):
    """admin 전용 `승인 대기` badge 원천 (SPEC-003 U-2). member 응답에는 null."""

    id: int
    state: str
    created_at: dt.datetime


class DocumentDetailOut(DocumentOut):
    """문서 상세 확장 — WORK-006 (SPEC-003 U-1/U-2).

    승인 metadata는 명칭 join(문서종류/표시 path/관련 부서)으로 노출하고,
    후보값은 admin 전용 pending badge로만 표시한다 (확정값처럼 노출 금지).
    """

    document_type_name: str | None = None
    physical_tree_path: PhysicalPathOut | None = None
    related_departments: list[RelatedDepartmentOut] = []
    related_products: list[str] = []
    approved: bool = False
    pending_candidate: PendingCandidateOut | None = None


class AdminDocumentListResponse(BaseModel):
    documents: list[DocumentOut]
    total: int
    limit: int
    offset: int


class DocumentDriveMirrorResponse(BaseModel):
    document_id: int
    mirror: DriveMirrorOut
