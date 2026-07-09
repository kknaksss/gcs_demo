"""문서 탐색/관계 라우트 — WORK-006 (SPEC-002/006 API Contract).

- GET /tree-documents                          : path(노드) 기준 물리 귀속 목록 (authenticated)
- GET /related-documents                       : 조직/문서 기준 관련 문서 (SPEC-002)
- GET /departments/{id}/related-documents      : 부서 기준 관련 문서 (SPEC-006)
- GET /documents/{id}/related                  : 문서 기준 관련 문서
- GET /documents/{id}/relations                : approved relation 조회
- GET /relation-types                          : v1 enum 4종

모든 표면에서 권한 없는 문서는 BE에서 제거된다 — FE 필터가 아니다 (Pre-deploy Check).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.errors import SPEC006_ERRORS, spec006_http_error
from app.dtos.user import UserDTO
from app.schemas.document_tree import PhysicalPathOut
from app.schemas.explorer import (
    DocumentRelationOut,
    DocumentRelationsResponse,
    RelatedDocumentOut,
    RelatedDocumentsResponse,
    RelationTypeOut,
    RelationTypesResponse,
    TreeDocumentOut,
    TreeDocumentsResponse,
    TreeNodeContextOut,
)
from app.services.explorer import (
    ExplorerService,
    RelatedDocumentItem,
    RelationItem,
    TreeDocumentItem,
)

router = APIRouter(tags=["explorer"])


def _path_out(item) -> PhysicalPathOut:
    return PhysicalPathOut(
        organization_path=item.organization_path,
        tree_path=item.tree_path,
        display_path=item.display_path,
        owning_department=item.owning_department,
    )


def _tree_document_out(item: TreeDocumentItem) -> TreeDocumentOut:
    doc = item.document
    return TreeDocumentOut(
        document_id=doc.id,
        drive_name=doc.drive_name,
        drive_state=doc.drive_state,  # type: ignore[arg-type]
        drive_modified_time=doc.drive_modified_time,
        document_type_name=item.document_type_name,
        physical_tree_path=_path_out(item.path),
        approved=bool(doc.organization_path),
    )


def _related_out(item: RelatedDocumentItem) -> RelatedDocumentOut:
    return RelatedDocumentOut(
        document_id=item.document.id,
        drive_name=item.document.drive_name,
        physical_tree_path=_path_out(item.path),
        source=item.source,  # type: ignore[arg-type]
        relation_type=item.relation_type,  # type: ignore[arg-type]
        match_reason=item.match_reason,
    )


def _relation_out(item: RelationItem) -> DocumentRelationOut:
    rel = item.relation
    return DocumentRelationOut(
        id=rel.id,
        source_document_id=rel.source_document_id,
        target_document_id=rel.target_document_id,
        relation_type=rel.relation_type,  # type: ignore[arg-type]
        source_label=rel.source_label,
        approved_by=rel.approved_by,
        approved_at=rel.approved_at,
        target_state=item.target_state,  # type: ignore[arg-type]
        source_drive_name=item.source_drive_name,
        target_drive_name=item.target_drive_name,
    )


@router.get("/tree-documents", response_model=TreeDocumentsResponse)
async def get_tree_documents(
    org_node_id: int = Query(description="선택 조직 노드 id"),
    tree_node_id: int | None = Query(default=None, description="선택 문서 트리 노드 id"),
    user: UserDTO = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> TreeDocumentsResponse:
    try:
        org, items = await ExplorerService(session).tree_documents(
            user, org_node_id=org_node_id, tree_node_id=tree_node_id
        )
    except SPEC006_ERRORS as exc:
        raise spec006_http_error(exc)
    return TreeDocumentsResponse(
        organization_node=TreeNodeContextOut(
            id=org.id, name=org.name, type=org.type, status=org.status
        ),
        documents=[_tree_document_out(i) for i in items],
        total=len(items),
    )


@router.get("/related-documents", response_model=RelatedDocumentsResponse)
async def get_related_documents(
    department_node_id: int | None = Query(default=None),
    document_id: int | None = Query(default=None),
    relation_type: str | None = Query(default=None),
    user: UserDTO = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RelatedDocumentsResponse:
    """SPEC-002 `GET /related-documents` — 조직 또는 문서 기준 관련 문서."""
    if department_node_id is None and document_id is None:
        # Case Matrix에 전용 코드 없음 — 쿼리 계약 위반 (spec 환류 후보).
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "RELATED_CONTEXT_REQUIRED",
                "message": "department_node_id or document_id is required",
            },
        )
    service = ExplorerService(session)
    try:
        if department_node_id is not None:
            items = await service.related_documents_for_department(
                user, department_node_id, relation_type=relation_type
            )
        else:
            items = await service.related_documents_for_document(
                user, document_id, relation_type=relation_type  # type: ignore[arg-type]
            )
    except SPEC006_ERRORS as exc:
        raise spec006_http_error(exc)
    return RelatedDocumentsResponse(
        documents=[_related_out(i) for i in items], total=len(items)
    )


@router.get(
    "/departments/{department_node_id}/related-documents",
    response_model=RelatedDocumentsResponse,
)
async def get_department_related_documents(
    department_node_id: int,
    relation_type: str | None = Query(default=None),
    user: UserDTO = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RelatedDocumentsResponse:
    try:
        items = await ExplorerService(session).related_documents_for_department(
            user, department_node_id, relation_type=relation_type
        )
    except SPEC006_ERRORS as exc:
        raise spec006_http_error(exc)
    return RelatedDocumentsResponse(
        documents=[_related_out(i) for i in items], total=len(items)
    )


@router.get("/documents/{document_id}/related", response_model=RelatedDocumentsResponse)
async def get_document_related(
    document_id: int,
    relation_type: str | None = Query(default=None),
    user: UserDTO = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> RelatedDocumentsResponse:
    try:
        items = await ExplorerService(session).related_documents_for_document(
            user, document_id, relation_type=relation_type
        )
    except SPEC006_ERRORS as exc:
        raise spec006_http_error(exc)
    return RelatedDocumentsResponse(
        documents=[_related_out(i) for i in items], total=len(items)
    )


@router.get(
    "/documents/{document_id}/relations", response_model=DocumentRelationsResponse
)
async def get_document_relations(
    document_id: int,
    user: UserDTO = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DocumentRelationsResponse:
    try:
        items = await ExplorerService(session).document_relations(user, document_id)
    except SPEC006_ERRORS as exc:
        raise spec006_http_error(exc)
    return DocumentRelationsResponse(
        document_id=document_id,
        relations=[_relation_out(i) for i in items],
        total=len(items),
    )


@router.get("/relation-types", response_model=RelationTypesResponse)
async def get_relation_types(
    _user: UserDTO = Depends(get_current_user),
) -> RelationTypesResponse:
    return RelationTypesResponse(
        relation_types=[
            RelationTypeOut(value=value, label=label)  # type: ignore[arg-type]
            for value, label in ExplorerService.relation_types()
        ]
    )
