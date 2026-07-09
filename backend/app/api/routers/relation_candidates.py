"""relation 후보 처리 admin 라우트 — WORK-005 (SPEC-005 U-6, DEC-021).

- POST /admin/relation-candidates/{id}/resolve : target 지정 → pending
- POST /admin/relation-candidates/{id}/hold    : unresolved 보류 유지
- POST /admin/relation-candidates/{id}/remove  : 후보 제거
- POST /admin/relation-candidates/{id}/rematch : title/drive_name 재검색 제안

확정 graph 반영(document_relations)은 metadata candidate approve 흐름 소관.
어떤 경로로도 새 document row를 자동 생성하지 않는다 (DEC-021).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_admin_only
from app.api.errors import SPEC005_ERRORS, spec005_http_error
from app.api.routers.approvals import _relation_out
from app.dtos.user import UserDTO
from app.schemas.approval import (
    RelationCandidateOut,
    RelationRematchResponse,
    RelationResolvePayload,
)
from app.services.relation_candidates import RelationCandidateService

router = APIRouter(tags=["relation-candidates"])


@router.post(
    "/admin/relation-candidates/{candidate_id}/resolve",
    response_model=RelationCandidateOut,
)
async def resolve_relation_candidate(
    candidate_id: int,
    payload: RelationResolvePayload,
    admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> RelationCandidateOut:
    service = RelationCandidateService(session)
    try:
        row = await service.resolve(
            candidate_id,
            target_document_id=payload.target_document_id,
            admin_id=admin.id,
        )
    except SPEC005_ERRORS as exc:
        raise spec005_http_error(exc)
    return _relation_out(row, await service.target_name(row))


@router.post(
    "/admin/relation-candidates/{candidate_id}/hold",
    response_model=RelationCandidateOut,
)
async def hold_relation_candidate(
    candidate_id: int,
    admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> RelationCandidateOut:
    service = RelationCandidateService(session)
    try:
        row = await service.hold(candidate_id, admin_id=admin.id)
    except SPEC005_ERRORS as exc:
        raise spec005_http_error(exc)
    return _relation_out(row, await service.target_name(row))


@router.post(
    "/admin/relation-candidates/{candidate_id}/remove",
    response_model=RelationCandidateOut,
)
async def remove_relation_candidate(
    candidate_id: int,
    admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> RelationCandidateOut:
    service = RelationCandidateService(session)
    try:
        row = await service.remove(candidate_id, admin_id=admin.id)
    except SPEC005_ERRORS as exc:
        raise spec005_http_error(exc)
    return _relation_out(row, await service.target_name(row))


@router.post(
    "/admin/relation-candidates/{candidate_id}/rematch",
    response_model=RelationRematchResponse,
)
async def rematch_relation_candidate(
    candidate_id: int,
    _admin: UserDTO = Depends(require_admin_only),
    session: AsyncSession = Depends(get_db),
) -> RelationRematchResponse:
    service = RelationCandidateService(session)
    try:
        row, suggestion = await service.rematch(candidate_id)
    except SPEC005_ERRORS as exc:
        raise spec005_http_error(exc)
    return RelationRematchResponse(
        candidate=_relation_out(row, await service.target_name(row)),
        suggested_target_document_id=suggestion.id if suggestion else None,
        suggested_target_drive_name=suggestion.drive_name if suggestion else None,
    )
