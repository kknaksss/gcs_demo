"""SPEC-002 문서 트리/카탈로그/이관 API schema — WORK-002 Phase 1/2.

- GET /document-tree-config, GET /document-types
- POST/PATCH /document-tree-nodes
- POST /documents/{id}/reassign, GET /documents/{id}/path-history

path array는 노드 name이 아니라 노드 id를 담는다 (SPEC-002 Implementation Rules).
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DocumentTreeNodeOut(BaseModel):
    """SPEC-002 §4 Document tree node 계약 (+ 카탈로그 참조 id)."""

    id: int
    organization_node_id: int
    parent_id: int | None = None
    type: Literal["work", "document_type"]
    document_type_id: int | None = None
    name: str
    status: Literal["active", "inactive"]


class DocumentTreeConfigResponse(BaseModel):
    nodes: list[DocumentTreeNodeOut]


class CreateDocumentTreeNodeRequest(BaseModel):
    organization_node_id: int
    parent_id: int | None = None
    type: Literal["work", "document_type"]
    document_type_id: int | None = None
    name: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_catalog_ref(self) -> CreateDocumentTreeNodeRequest:
        # document_type 노드만 카탈로그 stable id를 참조한다 (DEC-007).
        if self.type == "document_type" and self.document_type_id is None:
            raise ValueError("document_type node requires document_type_id")
        if self.type == "work" and self.document_type_id is not None:
            raise ValueError("work node cannot reference document_type_id")
        return self


class UpdateDocumentTreeNodeRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    status: Literal["active", "inactive"] | None = None


class DocumentTypeOut(BaseModel):
    id: int
    name: str
    normalized_name: str


class DocumentTypesResponse(BaseModel):
    document_types: list[DocumentTypeOut]


class PhysicalPathOut(BaseModel):
    """SPEC-002 §4 Physical tree path 계약 — 표시명은 노드 join으로 계산."""

    organization_path: list[int]
    tree_path: list[int]
    display_path: str
    owning_department: str | None = None


class ReassignRequest(BaseModel):
    organization_path: list[int]
    tree_path: list[int] = Field(default_factory=list)
    # 사유 필수 검증은 service에서 REASSIGN_REASON_REQUIRED 로 처리한다 (Case Matrix).
    reason: str = ""


class ReassignResponse(BaseModel):
    document_id: int
    path: PhysicalPathOut


class PathHistoryEntryOut(BaseModel):
    id: int
    previous_path: dict
    new_path: dict
    changed_by: int
    reason: str
    changed_at: dt.datetime


class PathHistoryResponse(BaseModel):
    document_id: int
    current_path: PhysicalPathOut | None = None
    entries: list[PathHistoryEntryOut]
