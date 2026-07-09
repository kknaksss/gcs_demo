"""승인 게이트 admin 라우트 — WORK-005 (SPEC-005 API Contract).

- GET  /admin/approval-candidates          : 후보 큐 (state/read_capability 필터)
- GET  /admin/approval-candidates/{id}     : 후보 상세 (current fingerprint 동봉)
- POST /admin/approval-candidates/{id}/approve   : 승인 (재검사 → 반영, 멱등)
- POST /admin/approval-candidates/{id}/reject    : 거절 (pending/stale)
- POST /admin/approval-candidates/{id}/reanalyze : 수동 재분석 (WORK-004 위임)

에러봉투 {detail:{error_code,message}} — SPEC-005 Case Matrix.
reanalysis_status는 표시용 파생값으로 응답에만 실린다 (DEC-022).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin_only
from app.api.errors import SPEC005_ERRORS, spec005_http_error
from app.api.routers.classification import _job_out
from app.dtos.user import UserDTO
from app.models.candidate import MetadataCandidate, RelationCandidate
from app.models.document import Document
from app.schemas.approval import (
    ApprovalCandidateDetail,
    ApprovalCandidateListResponse,
    ApprovalCandidateSummary,
    ApprovalPayload,
    ApprovalResultResponse,
    ApprovedMetadataOut,
    RejectPayload,
    RelationCandidateOut,
)
from app.schemas.classification import ClassificationJobOut
from app.services.ai_jobs import fingerprint_key
from app.services.approval import ApprovalService

router = APIRouter(tags=["approvals"])


def _summary_out(
    candidate: MetadataCandidate,
    document: Document,
    reanalysis_status: str | None,
) -> ApprovalCandidateSummary:
    return ApprovalCandidateSummary(
        id=candidate.id,
        document_id=candidate.document_id,
        drive_name=document.drive_name,
        drive_state=document.drive_state,
        state=candidate.state,  # type: ignore[arg-type]
        reanalysis_status=reanalysis_status,  # type: ignore[arg-type]
        read_capability=candidate.read_capability,  # type: ignore[arg-type]
        stale_reason=candidate.reason if candidate.state == "stale" else None,
        blocked_reason=candidate.reason if candidate.state == "blocked" else None,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )


def _relation_out(
    rel: RelationCandidate, target_drive_name: str | None
) -> RelationCandidateOut:
    return RelationCandidateOut(
        id=rel.id,
        source_document_id=rel.source_document_id,
        raw_label=rel.raw_label,
        suggested_relation_type=rel.suggested_relation_type,  # type: ignore[arg-type]
        target_document_id=rel.target_document_id,
        target_drive_name=target_drive_name,
        state=rel.state,  # type: ignore[arg-type]
        created_at=rel.created_at,
        updated_at=rel.updated_at,
    )


def _approved_out(document: Document, related_ids: list[int]) -> ApprovedMetadataOut:
    return ApprovedMetadataOut(
        document_id=document.id,
        document_type_id=document.document_type_id,
        created_department_node_id=document.created_department_node_id,
        owning_department_node_id=document.owning_department_node_id,
        organization_path=document.organization_path,
        tree_path=document.tree_path,
        related_department_node_ids=related_ids,
        related_products=document.related_products,
        read_roles=document.read_roles,
        read_departments=document.read_departments,
        read_positions=document.read_positions,
        access_logic=document.access_logic,
        sensitivity=document.sensitivity,
        policy_preset=document.policy_preset,
        summary=document.summary,
    )


@router.get(
    "/admin/approval-candidates", response_model=ApprovalCandidateListResponse
)
async def list_approval_candidates(
    state: Literal["pending", "stale", "approved", "rejected", "blocked"] | None = Query(
        default=None
    ),
    read_capability: Literal["content_read", "metadata_only"] | None = Query(
        default=None
    ),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> ApprovalCandidateListResponse:
    rows, total = await ApprovalService(session).list_candidates(
        state=state, read_capability=read_capability, limit=limit, offset=offset
    )
    return ApprovalCandidateListResponse(
        candidates=[
            _summary_out(candidate, document, status)
            for candidate, document, status in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/admin/approval-candidates/{candidate_id}",
    response_model=ApprovalCandidateDetail,
)
async def get_approval_candidate(
    candidate_id: int,
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> ApprovalCandidateDetail:
    try:
        candidate, document, status, relations = await ApprovalService(
            session
        ).get_candidate(candidate_id)
    except SPEC005_ERRORS as exc:
        raise spec005_http_error(exc)
    summary = _summary_out(candidate, document, status)
    return ApprovalCandidateDetail(
        **summary.model_dump(),
        drive_web_url=document.drive_web_url,
        candidate_metadata=candidate.candidate_metadata,
        candidate_fingerprint=candidate.candidate_fingerprint,
        current_fingerprint=document.drive_fingerprint,
        fingerprint_match=fingerprint_key(candidate.candidate_fingerprint)
        == fingerprint_key(document.drive_fingerprint),
        approved_by=candidate.approved_by,
        approved_at=candidate.approved_at,
        relation_candidates=[
            _relation_out(rel, target_name) for rel, target_name in relations
        ],
    )


@router.post(
    "/admin/approval-candidates/{candidate_id}/approve",
    response_model=ApprovalResultResponse,
)
async def approve_candidate(
    candidate_id: int,
    payload: ApprovalPayload,
    admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> ApprovalResultResponse:
    service = ApprovalService(session)
    try:
        candidate, document, related_ids, idempotent = await service.approve(
            candidate_id, payload, admin=admin
        )
    except SPEC005_ERRORS as exc:
        raise spec005_http_error(exc)
    return ApprovalResultResponse(
        candidate=_summary_out(candidate, document, None),
        document=_approved_out(document, related_ids),
        idempotent=idempotent,
    )


@router.post(
    "/admin/approval-candidates/{candidate_id}/reject",
    response_model=ApprovalCandidateSummary,
)
async def reject_candidate(
    candidate_id: int,
    payload: RejectPayload | None = None,
    admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> ApprovalCandidateSummary:
    service = ApprovalService(session)
    try:
        candidate = await service.reject(
            candidate_id,
            admin=admin,
            reason=payload.reason if payload else None,
        )
        _, document, status, _ = await service.get_candidate(candidate.id)
    except SPEC005_ERRORS as exc:
        raise spec005_http_error(exc)
    return _summary_out(candidate, document, status)


@router.post(
    "/admin/approval-candidates/{candidate_id}/reanalyze",
    response_model=ClassificationJobOut,
    status_code=202,
)
async def reanalyze_candidate(
    candidate_id: int,
    admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> ClassificationJobOut:
    try:
        job = await ApprovalService(session).reanalyze(candidate_id, admin=admin)
    except SPEC005_ERRORS as exc:
        raise spec005_http_error(exc)
    return _job_out(job)
