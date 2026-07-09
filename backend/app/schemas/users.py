"""admin user 매핑 보정 API schema — WORK-001 Phase 3 (v1 admin tool)."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.auth import UserProfile


class UnmappedUsersResponse(BaseModel):
    users: list[UserProfile]


class AssignDepartmentRequest(BaseModel):
    department_node_id: int
    team_node_id: int | None = None
