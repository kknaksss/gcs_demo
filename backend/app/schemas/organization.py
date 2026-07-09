"""SPEC-002 조직도 API schema — WORK-002 Phase 1.

- GET /organization-tree, POST /organization-nodes, PATCH /organization-nodes/{id}.
- 노드 참조는 name이 아니라 stable id (DEC-004/012).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OrganizationNodeOut(BaseModel):
    """SPEC-002 §4 Organization node 계약."""

    id: int
    parent_id: int | None = None
    type: Literal["company", "department", "team"]
    name: str
    status: Literal["active", "inactive"]


class OrganizationTreeResponse(BaseModel):
    """평탄한 노드 목록 — 계층은 parent_id로 FE가 조립한다."""

    nodes: list[OrganizationNodeOut]


class CreateOrganizationNodeRequest(BaseModel):
    parent_id: int | None = None
    type: Literal["company", "department", "team"]
    name: str = Field(min_length=1)


class UpdateOrganizationNodeRequest(BaseModel):
    """이름/상태 변경만 허용. type/parent 이동과 hard delete는 계약에 없다."""

    name: str | None = Field(default=None, min_length=1)
    status: Literal["active", "inactive"] | None = None
