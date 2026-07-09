"""SPEC-002/006 문서 탐색/관계 API schema — WORK-006.

- GET /tree-documents · /related-documents · /departments/{id}/related-documents
- GET /documents/{id}/relations · /documents/{id}/related · /relation-types
- GET /search/documents
응답에는 read policy 원본/PII를 싣지 않는다 (Pre-deploy Check).
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel

from app.schemas.document_tree import PhysicalPathOut
from app.schemas.documents import DriveState

RelationType = Literal["related", "references", "supersedes", "duplicate_candidate"]
RelatedSource = Literal[
    "physical", "related_department", "related_product", "document_relation"
]
SearchBadge = Literal["physical", "related"]


class TreeDocumentOut(BaseModel):
    """물리 귀속 목록 항목 (SPEC-006 U-1) — 논리 연결 문서는 여기 섞이지 않는다."""

    document_id: int
    drive_name: str
    drive_state: DriveState
    drive_modified_time: dt.datetime | None = None
    document_type_name: str | None = None
    physical_tree_path: PhysicalPathOut
    approved: bool


class TreeNodeContextOut(BaseModel):
    """선택 조직 노드 컨텍스트 — inactive면 FE가 `비활성 조직` badge 표시 (DEC-013)."""

    id: int
    name: str
    type: str
    status: str


class TreeDocumentsResponse(BaseModel):
    organization_node: TreeNodeContextOut
    documents: list[TreeDocumentOut]
    total: int


class RelatedDocumentOut(BaseModel):
    """관련 문서 항목 (SPEC-006 Related document item 계약)."""

    document_id: int
    drive_name: str
    physical_tree_path: PhysicalPathOut
    source: RelatedSource
    relation_type: RelationType | None = None
    match_reason: str


class RelatedDocumentsResponse(BaseModel):
    documents: list[RelatedDocumentOut]
    total: int


class DocumentRelationOut(BaseModel):
    """문서 상세 relation (SPEC-006 Document relation 계약).

    `target_state`는 target 문서 drive_state 파생값 — 저장하지 않는다.
    """

    id: int
    source_document_id: int
    target_document_id: int
    relation_type: RelationType
    source_label: str | None = None
    approved_by: int
    approved_at: dt.datetime
    target_state: DriveState
    # FE 표시 편의 필드 — 이름은 노출 가능 범위(권한 필터 통과분)만 온다
    source_drive_name: str | None = None
    target_drive_name: str | None = None


class DocumentRelationsResponse(BaseModel):
    document_id: int
    relations: list[DocumentRelationOut]
    total: int


class RelationTypeOut(BaseModel):
    value: RelationType
    label: str


class RelationTypesResponse(BaseModel):
    relation_types: list[RelationTypeOut]


class SearchResultOut(BaseModel):
    """검색 결과 항목 (SPEC-006 Search result item 계약) — 출처 badge 필수."""

    document_id: int
    drive_name: str
    physical_tree_path: PhysicalPathOut
    source_badge: SearchBadge
    relation_type: RelationType | None = None


class SearchDocumentsResponse(BaseModel):
    query: str
    results: list[SearchResultOut]
    total: int
