"""SPEC-005 승인 게이트 API schema — WORK-005.

- 후보 원장 state는 5개 enum (SPEC-003과 동일). `reanalysis_status`는 표시용
  파생값으로 응답에만 실리고 원장에 저장하지 않는다 (DEC-022).
- Approval payload의 부서/트리 값은 텍스트가 아니라 노드/카탈로그 stable id다
  (ARCH-003 — SPEC-005 표기와의 정합은 구현 후 spec 환류, work-005 Open Issue).
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

CandidateState = Literal["pending", "stale", "approved", "rejected", "blocked"]
ReanalysisStatus = Literal["reanalyzing", "new_candidate_ready", "reanalysis_failed"]
ReadCapability = Literal["content_read", "metadata_only"]
RelationCandidateState = Literal["pending", "unresolved", "approved", "removed"]
RelationType = Literal["related", "references", "supersedes", "duplicate_candidate"]


# ── Approval candidate ───────────────────────────────────────────────────────


class ApprovalCandidateSummary(BaseModel):
    id: int
    document_id: int
    drive_name: str
    drive_state: str
    state: CandidateState
    reanalysis_status: ReanalysisStatus | None = None
    read_capability: ReadCapability
    stale_reason: str | None = None
    blocked_reason: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ApprovalCandidateListResponse(BaseModel):
    candidates: list[ApprovalCandidateSummary]
    total: int
    limit: int
    offset: int


class RelationCandidateOut(BaseModel):
    id: int
    source_document_id: int
    raw_label: str
    suggested_relation_type: RelationType
    target_document_id: int | None = None
    target_drive_name: str | None = None
    state: RelationCandidateState
    created_at: dt.datetime
    updated_at: dt.datetime


class ApprovalCandidateDetail(ApprovalCandidateSummary):
    drive_web_url: str | None = None
    candidate_metadata: dict
    candidate_fingerprint: dict
    current_fingerprint: dict
    fingerprint_match: bool
    approved_by: int | None = None
    approved_at: dt.datetime | None = None
    relation_candidates: list[RelationCandidateOut] = Field(default_factory=list)


# ── Approval payload (SPEC-005 표 — id 기반) ─────────────────────────────────


class PhysicalTreePathIn(BaseModel):
    organization_path: list[int]
    tree_path: list[int] = Field(default_factory=list)


class ApprovalPayload(BaseModel):
    document_type_id: int
    created_department_node_id: int | None = None
    owning_department_node_id: int
    physical_tree_path: PhysicalTreePathIn
    related_department_node_ids: list[int] = Field(default_factory=list)
    related_products: list[str] = Field(default_factory=list)
    summary: str | None = None
    read_roles: list[str] = Field(default_factory=list)
    read_departments: list[int] = Field(default_factory=list)
    read_positions: list[str] = Field(default_factory=list)
    access_logic: Literal["ANY", "ALL", "PRESET"]
    sensitivity: Literal["normal", "sensitive"]
    policy_preset: str | None = None


class ApprovedMetadataOut(BaseModel):
    """승인 후 documents에 반영된 approved 필드 (mirror 제외)."""

    document_id: int
    document_type_id: int | None
    created_department_node_id: int | None
    owning_department_node_id: int | None
    organization_path: list[int] | None
    tree_path: list[int] | None
    related_department_node_ids: list[int]
    related_products: list[str] | None
    read_roles: list[str] | None
    read_departments: list[int] | None
    read_positions: list[str] | None
    access_logic: str
    sensitivity: str
    policy_preset: str | None
    summary: str | None


class ApprovalResultResponse(BaseModel):
    candidate: ApprovalCandidateSummary
    document: ApprovedMetadataOut
    # 같은 요청 재시도 멱등 성공 여부 (SPEC-005 Implementation Rules)
    idempotent: bool = False


class RejectPayload(BaseModel):
    reason: str | None = None


# ── Document type catalog (SPEC-005 U-4) ─────────────────────────────────────


class DocumentTypeCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class DocumentTypeItemOut(BaseModel):
    id: int
    name: str
    normalized_name: str


class DocumentTypeListResponse(BaseModel):
    document_types: list[DocumentTypeItemOut]


# ── Relation candidate 처리 (SPEC-005 U-6) ───────────────────────────────────


class RelationResolvePayload(BaseModel):
    # target 없이 확정 불가 — null이면 RELATION_TARGET_REQUIRED (DEC-021)
    target_document_id: int | None = None


class RelationRematchResponse(BaseModel):
    candidate: RelationCandidateOut
    suggested_target_document_id: int | None = None
    suggested_target_drive_name: str | None = None
