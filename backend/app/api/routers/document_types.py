"""문서종류 카탈로그 admin 라우트 — WORK-005 (SPEC-005 U-4/S-3, DEC-007).

- GET  /admin/document-types : 전사 공통 카탈로그 조회 (admin)
- POST /admin/document-types : 문서종류 추가 (admin, 정규화 이름 unique)

추가해도 기존 문서의 문서종류는 자동 변경되지 않는다. 조회 전용 공개 표면은
WORK-002의 GET /document-types (document_tree 라우터).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin_only
from app.api.errors import SPEC005_ERRORS, spec005_http_error
from app.dtos.user import UserDTO
from app.schemas.approval import (
    DocumentTypeCreatePayload,
    DocumentTypeItemOut,
    DocumentTypeListResponse,
)
from app.services.approval import ApprovalService

router = APIRouter(tags=["document-types"])


@router.get("/admin/document-types", response_model=DocumentTypeListResponse)
async def list_document_types(
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> DocumentTypeListResponse:
    types = await ApprovalService(session).list_document_types()
    return DocumentTypeListResponse(
        document_types=[
            DocumentTypeItemOut(
                id=t.id, name=t.name, normalized_name=t.normalized_name
            )
            for t in types
        ]
    )


@router.post(
    "/admin/document-types", response_model=DocumentTypeItemOut, status_code=201
)
async def create_document_type(
    payload: DocumentTypeCreatePayload,
    admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> DocumentTypeItemOut:
    try:
        row = await ApprovalService(session).create_document_type(
            name=payload.name, admin=admin
        )
    except SPEC005_ERRORS as exc:
        raise spec005_http_error(exc)
    return DocumentTypeItemOut(
        id=row.id, name=row.name, normalized_name=row.normalized_name
    )
