"""admin user 매핑 보정 라우트 — WORK-001 Phase 3 (SPEC-001 Admin Behavior).

- GET  /admin/users/unmapped        : 조직 매핑 실패 user 목록
- POST /admin/users/{id}/department : 조직도 노드 지정 보정

admin 전용(require_admin). 에러봉투 {detail:{error_code,message}}.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin
from app.api.routers.auth import _profile
from app.dtos.user import UserDTO
from app.schemas.auth import UserProfile
from app.schemas.users import AssignDepartmentRequest, UnmappedUsersResponse
from app.services.users import (
    InvalidOrganizationNodeError,
    OrganizationNodeNotFoundError,
    UserAdminService,
    UserNotFoundError,
)

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


@router.get("/unmapped", response_model=UnmappedUsersResponse)
async def list_unmapped(
    _admin: UserDTO = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> UnmappedUsersResponse:
    users = await UserAdminService(session).list_unmapped()
    return UnmappedUsersResponse(users=[_profile(u) for u in users])


@router.post("/{user_id}/department", response_model=UserProfile)
async def assign_department(
    user_id: int,
    body: AssignDepartmentRequest,
    _admin: UserDTO = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> UserProfile:
    service = UserAdminService(session)
    try:
        user = await service.assign_department(
            user_id,
            department_node_id=body.department_node_id,
            team_node_id=body.team_node_id,
        )
    except UserNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "USER_NOT_FOUND", "message": "User not found."},
        )
    except OrganizationNodeNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "ORG_NODE_NOT_FOUND",
                "message": "Organization node not found.",
            },
        )
    except InvalidOrganizationNodeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "INVALID_ORG_NODE",
                "message": "Only department or team nodes can be assigned.",
            },
        )
    await session.commit()
    return _profile(user)
