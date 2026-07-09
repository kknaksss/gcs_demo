"""조직도 라우트 — WORK-002 Phase 1 (SPEC-002 API Contract).

- GET   /organization-tree          : 조직도 조회 (authenticated)
- POST  /organization-nodes         : 조직 노드 생성 (admin)
- PATCH /organization-nodes/{id}    : 이름/상태 변경 (admin, hard delete 없음)

에러봉투 {detail:{error_code,message}} — SPEC-002 Case Matrix.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_admin_only
from app.api.errors import SPEC002_ERRORS, spec002_http_error
from app.dtos.user import UserDTO
from app.models.organization import OrganizationNode
from app.schemas.organization import (
    CreateOrganizationNodeRequest,
    OrganizationNodeOut,
    OrganizationTreeResponse,
    UpdateOrganizationNodeRequest,
)
from app.services.organization import OrganizationService

router = APIRouter(tags=["organization"])


def _node_out(node: OrganizationNode) -> OrganizationNodeOut:
    return OrganizationNodeOut(
        id=node.id,
        parent_id=node.parent_id,
        type=node.type,
        name=node.name,
        status=node.status,
    )


@router.get("/organization-tree", response_model=OrganizationTreeResponse)
async def get_organization_tree(
    _user: UserDTO = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> OrganizationTreeResponse:
    nodes = await OrganizationService(session).get_tree()
    return OrganizationTreeResponse(nodes=[_node_out(n) for n in nodes])


@router.post(
    "/organization-nodes",
    response_model=OrganizationNodeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization_node(
    body: CreateOrganizationNodeRequest,
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> OrganizationNodeOut:
    try:
        node = await OrganizationService(session).create_node(
            type=body.type, name=body.name, parent_id=body.parent_id
        )
    except SPEC002_ERRORS as exc:
        raise spec002_http_error(exc)
    return _node_out(node)


@router.patch("/organization-nodes/{node_id}", response_model=OrganizationNodeOut)
async def update_organization_node(
    node_id: int,
    body: UpdateOrganizationNodeRequest,
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> OrganizationNodeOut:
    try:
        node = await OrganizationService(session).update_node(
            node_id, name=body.name, status=body.status
        )
    except SPEC002_ERRORS as exc:
        raise spec002_http_error(exc)
    return _node_out(node)
