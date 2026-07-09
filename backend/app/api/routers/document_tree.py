"""문서 트리/카탈로그/이관 라우트 — WORK-002 Phase 1/2 (SPEC-002 API Contract).

- GET   /document-tree-config          : 업무/문서종류 트리 조회 (authenticated)
- GET   /document-types                : 전사 공통 카탈로그 조회 (authenticated)
- POST  /document-tree-nodes           : 트리 노드 생성 (admin)
- PATCH /document-tree-nodes/{id}      : 트리 노드 수정 (admin)
- POST  /documents/{id}/reassign       : physical path 이관 (admin)
- GET   /documents/{id}/path-history   : path 변경 이력 (admin)

`GET /tree-documents`·`/related-documents`는 WORK-006 소관 — 여기 만들지 않는다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_admin_only
from app.api.errors import SPEC002_ERRORS, spec002_http_error
from app.dtos.path import ValidatedPath
from app.dtos.user import UserDTO
from app.models.organization import DocumentTreeNode
from app.schemas.document_tree import (
    CreateDocumentTreeNodeRequest,
    DocumentTreeConfigResponse,
    DocumentTreeNodeOut,
    DocumentTypeOut,
    DocumentTypesResponse,
    PathHistoryEntryOut,
    PathHistoryResponse,
    PhysicalPathOut,
    ReassignRequest,
    ReassignResponse,
    UpdateDocumentTreeNodeRequest,
)
from app.services.document_tree import DocumentTreeService

router = APIRouter(tags=["document-tree"])


def _node_out(node: DocumentTreeNode) -> DocumentTreeNodeOut:
    return DocumentTreeNodeOut(
        id=node.id,
        organization_node_id=node.organization_node_id,
        parent_id=node.parent_id,
        type=node.type,
        document_type_id=node.document_type_id,
        name=node.name,
        status=node.status,
    )


def _path_out(path: ValidatedPath) -> PhysicalPathOut:
    return PhysicalPathOut(
        organization_path=path.organization_path,
        tree_path=path.tree_path,
        display_path=path.display_path,
        owning_department=path.owning_department,
    )


@router.get("/document-tree-config", response_model=DocumentTreeConfigResponse)
async def get_document_tree_config(
    _user: UserDTO = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DocumentTreeConfigResponse:
    nodes = await DocumentTreeService(session).get_config()
    return DocumentTreeConfigResponse(nodes=[_node_out(n) for n in nodes])


@router.get("/document-types", response_model=DocumentTypesResponse)
async def get_document_types(
    _user: UserDTO = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DocumentTypesResponse:
    types = await DocumentTreeService(session).list_document_types()
    return DocumentTypesResponse(
        document_types=[
            DocumentTypeOut(id=t.id, name=t.name, normalized_name=t.normalized_name)
            for t in types
        ]
    )


@router.post(
    "/document-tree-nodes",
    response_model=DocumentTreeNodeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_tree_node(
    body: CreateDocumentTreeNodeRequest,
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> DocumentTreeNodeOut:
    try:
        node = await DocumentTreeService(session).create_node(
            organization_node_id=body.organization_node_id,
            parent_id=body.parent_id,
            type=body.type,
            document_type_id=body.document_type_id,
            name=body.name,
        )
    except SPEC002_ERRORS as exc:
        raise spec002_http_error(exc)
    return _node_out(node)


@router.patch("/document-tree-nodes/{node_id}", response_model=DocumentTreeNodeOut)
async def update_document_tree_node(
    node_id: int,
    body: UpdateDocumentTreeNodeRequest,
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> DocumentTreeNodeOut:
    try:
        node = await DocumentTreeService(session).update_node(
            node_id, name=body.name, status=body.status
        )
    except SPEC002_ERRORS as exc:
        raise spec002_http_error(exc)
    return _node_out(node)


@router.post("/documents/{document_id}/reassign", response_model=ReassignResponse)
async def reassign_document(
    document_id: int,
    body: ReassignRequest,
    admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> ReassignResponse:
    try:
        validated = await DocumentTreeService(session).reassign(
            document_id,
            organization_path=body.organization_path,
            tree_path=body.tree_path,
            reason=body.reason,
            changed_by=admin.id,
        )
    except SPEC002_ERRORS as exc:
        raise spec002_http_error(exc)
    return ReassignResponse(document_id=document_id, path=_path_out(validated))


@router.get(
    "/documents/{document_id}/path-history", response_model=PathHistoryResponse
)
async def get_document_path_history(
    document_id: int,
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> PathHistoryResponse:
    try:
        _document, current, entries = await DocumentTreeService(
            session
        ).path_history(document_id)
    except SPEC002_ERRORS as exc:
        raise spec002_http_error(exc)
    return PathHistoryResponse(
        document_id=document_id,
        current_path=_path_out(current) if current else None,
        entries=[
            PathHistoryEntryOut(
                id=e.id,
                previous_path=e.previous_path,
                new_path=e.new_path,
                changed_by=e.changed_by,
                reason=e.reason,
                changed_at=e.changed_at,
            )
            for e in entries
        ],
    )
