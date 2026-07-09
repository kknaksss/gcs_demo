"""문서 record 조회 라우트 — WORK-003 최소 표면 + WORK-006 상세 확장 (SPEC-003).

- GET /documents/{id}              : 문서 상세 — 승인 metadata 명칭 join +
                                     RBAC read policy 적용 (evaluate_read).
                                     member는 approved만, admin은 `승인 대기`
                                     candidate badge 포함 (SPEC-003 U-1/U-2).
- GET /documents/{id}/drive-mirror : Drive mirror 조회 (admin)
- GET /admin/documents             : 상태별 문서 감사 목록 (admin)

에러봉투 {detail:{error_code,message}} — SPEC-003 Case Matrix.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_admin_only
from app.api.errors import (
    SPEC003_004_ERRORS,
    SPEC006_ERRORS,
    spec003_004_http_error,
    spec006_http_error,
)
from app.dtos.user import UserDTO
from app.models.document import Document
from app.schemas.document_tree import PhysicalPathOut
from app.schemas.documents import (
    AdminDocumentListResponse,
    DocumentDetailOut,
    DocumentDriveMirrorResponse,
    DocumentOut,
    DriveMirrorOut,
    PendingCandidateOut,
    RelatedDepartmentOut,
)
from app.services.documents import DocumentsService
from app.services.explorer import DocumentDetail, ExplorerService

router = APIRouter(tags=["documents"])


def _mirror_out(document: Document) -> DriveMirrorOut:
    return DriveMirrorOut(
        source_provider=document.source_provider,
        drive_file_id=document.drive_file_id,
        drive_name=document.drive_name,
        drive_web_url=document.drive_web_url,
        drive_mime_type=document.drive_mime_type,
        drive_state=document.drive_state,  # type: ignore[arg-type]
        drive_modified_time=document.drive_modified_time,
        drive_fingerprint=document.drive_fingerprint,
    )


def _document_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        mirror=_mirror_out(document),
        document_type_id=document.document_type_id,
        owning_department_node_id=document.owning_department_node_id,
        organization_path=document.organization_path,
        tree_path=document.tree_path,
        access_logic=document.access_logic,
        sensitivity=document.sensitivity,
        policy_preset=document.policy_preset,
        summary=document.summary,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _detail_out(detail: DocumentDetail) -> DocumentDetailOut:
    document = detail.document
    base = _document_out(document)
    return DocumentDetailOut(
        **base.model_dump(),
        document_type_name=detail.document_type_name,
        physical_tree_path=(
            PhysicalPathOut(
                organization_path=detail.path.organization_path,
                tree_path=detail.path.tree_path,
                display_path=detail.path.display_path,
                owning_department=detail.path.owning_department,
            )
            if detail.path
            else None
        ),
        related_departments=[
            RelatedDepartmentOut(node_id=n.id, name=n.name)
            for n in detail.related_departments
        ],
        related_products=list(document.related_products or []),
        approved=bool(document.organization_path),
        pending_candidate=(
            PendingCandidateOut(
                id=detail.pending_candidate.id,
                state=detail.pending_candidate.state,
                created_at=detail.pending_candidate.created_at,
            )
            if detail.pending_candidate
            else None
        ),
    )


@router.get("/documents/{document_id}", response_model=DocumentDetailOut)
async def get_document(
    document_id: int,
    user: UserDTO = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DocumentDetailOut:
    """문서 상세 — RBAC read policy 불만족 시 404 톤(DOCUMENT_NOT_READABLE)."""
    try:
        detail = await ExplorerService(session).document_detail(user, document_id)
    except SPEC006_ERRORS as exc:
        raise spec006_http_error(exc)
    return _detail_out(detail)


@router.get(
    "/documents/{document_id}/drive-mirror",
    response_model=DocumentDriveMirrorResponse,
)
async def get_document_drive_mirror(
    document_id: int,
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> DocumentDriveMirrorResponse:
    try:
        document = await DocumentsService(session).get_drive_mirror(document_id)
    except SPEC003_004_ERRORS as exc:
        raise spec003_004_http_error(exc)
    return DocumentDriveMirrorResponse(
        document_id=document.id, mirror=_mirror_out(document)
    )


@router.get("/admin/documents", response_model=AdminDocumentListResponse)
async def list_admin_documents(
    drive_state: Literal["active", "trashed", "removed", "out_of_scope"] | None = Query(
        default=None
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> AdminDocumentListResponse:
    documents, total = await DocumentsService(session).list_admin_documents(
        drive_state=drive_state, limit=limit, offset=offset
    )
    return AdminDocumentListResponse(
        documents=[_document_out(d) for d in documents],
        total=total,
        limit=limit,
        offset=offset,
    )
