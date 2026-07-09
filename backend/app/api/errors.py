"""SPEC-002/003/004/007 Case Matrix — service 예외 → HTTP 에러봉투 {error_code, message} 변환.

router 레이어 전용 helper. 백엔드 출력 메시지는 각 SPEC Case Matrix의
'백엔드 출력' 문구를 그대로 쓰고, 한국어 카피는 FE가 error_code로 매핑한다.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.services.document_tree import (
    DocumentNotReadableError,
    ReassignReasonRequiredError,
    TreeNodeNotFoundError,
)
from app.services.documents import (
    DocumentHiddenError,
    DocumentNotFoundError,
)
from app.services.drive_sync import (
    DriveChangesFailedError,
    DriveConnectorNotConfiguredError,
    DriveFolderNotConfiguredError,
    DriveWatchFailedError,
)
from app.services.organization import (
    InvalidTreeDepthError,
    OrgNodeInactiveError,
    OrgNodeNotFoundError,
)

_SPEC002_CASE_MATRIX: dict[type[Exception], tuple[int, str, str]] = {
    OrgNodeNotFoundError: (
        status.HTTP_404_NOT_FOUND,
        "ORG_NODE_NOT_FOUND",
        "organization node not found",
    ),
    OrgNodeInactiveError: (
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "ORG_NODE_INACTIVE",
        "inactive organization cannot be selected",
    ),
    TreeNodeNotFoundError: (
        status.HTTP_404_NOT_FOUND,
        "TREE_NODE_NOT_FOUND",
        "document tree node not found",
    ),
    InvalidTreeDepthError: (
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "INVALID_TREE_DEPTH",
        "invalid organization/tree hierarchy",
    ),
    ReassignReasonRequiredError: (
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "REASSIGN_REASON_REQUIRED",
        "changed_reason is required",
    ),
    DocumentNotReadableError: (
        status.HTTP_404_NOT_FOUND,
        "DOCUMENT_NOT_READABLE",
        "document hidden by read policy",
    ),
}

SPEC002_ERRORS = tuple(_SPEC002_CASE_MATRIX)


def spec002_http_error(exc: Exception) -> HTTPException:
    status_code, error_code, message = _SPEC002_CASE_MATRIX[type(exc)]
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code, "message": message},
    )


# ── SPEC-003 (Document Metadata Record) + SPEC-004 (Drive Connector & Sync) ──

_SPEC003_004_CASE_MATRIX: dict[type[Exception], tuple[int, str, str]] = {
    DocumentNotFoundError: (
        status.HTTP_404_NOT_FOUND,
        "DOCUMENT_NOT_FOUND",
        "document not found",
    ),
    # member에게 soft deleted 문서는 존재 자체를 숨긴다 (SPEC-003 U-1).
    DocumentHiddenError: (
        status.HTTP_404_NOT_FOUND,
        "DOCUMENT_NOT_READABLE",
        "hidden by read policy",
    ),
    DriveConnectorNotConfiguredError: (
        status.HTTP_409_CONFLICT,
        "DRIVE_CONNECTOR_NOT_CONFIGURED",
        "missing drive connector env",
    ),
    DriveFolderNotConfiguredError: (
        status.HTTP_409_CONFLICT,
        "DRIVE_FOLDER_NOT_CONFIGURED",
        "missing selected folder id",
    ),
    DriveWatchFailedError: (
        status.HTTP_409_CONFLICT,
        "DRIVE_WATCH_EXPIRED",
        "watch channel expired",
    ),
    DriveChangesFailedError: (
        status.HTTP_502_BAD_GATEWAY,
        "DRIVE_CHANGES_FAILED",
        "changes.list failed",
    ),
}

SPEC003_004_ERRORS = tuple(_SPEC003_004_CASE_MATRIX)


def spec003_004_http_error(exc: Exception) -> HTTPException:
    status_code, error_code, message = _SPEC003_004_CASE_MATRIX[type(exc)]
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code, "message": message},
    )


# ── SPEC-007 (AI Classification Pipeline) ────────────────────────────────────

from app.services.ai_jobs import (  # noqa: E402
    ClassificationJobNotFoundError,
    ClassificationRetryExhaustedError,
    DocumentUnavailableError,
)

_SPEC007_CASE_MATRIX: dict[type[Exception], tuple[int, str, str]] = {
    ClassificationJobNotFoundError: (
        status.HTTP_404_NOT_FOUND,
        "CLASSIFICATION_JOB_NOT_FOUND",
        "classification job not found",
    ),
    DocumentNotFoundError: (
        status.HTTP_404_NOT_FOUND,
        "DOCUMENT_NOT_FOUND",
        "document not found",
    ),
    # unavailable(trashed/removed/out_of_scope) 문서는 분석 대상이 아니다.
    DocumentUnavailableError: (
        status.HTTP_409_CONFLICT,
        "DOCUMENT_UNAVAILABLE",
        "document is not analyzable in current drive_state",
    ),
    # attempt_count >= max_attempts — 수동 재시도 게이트 (ARCH-002 §5).
    ClassificationRetryExhaustedError: (
        status.HTTP_409_CONFLICT,
        "CLASSIFICATION_RETRY_EXHAUSTED",
        "max attempts exceeded",
    ),
}

SPEC007_ERRORS = tuple(_SPEC007_CASE_MATRIX)


def spec007_http_error(exc: Exception) -> HTTPException:
    status_code, error_code, message = _SPEC007_CASE_MATRIX[type(exc)]
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code, "message": message},
    )


# ── SPEC-006 (Document Relations & Explorer) ─────────────────────────────────

from app.services.explorer import InvalidRelationTypeError  # noqa: E402

_SPEC006_CASE_MATRIX: dict[type[Exception], tuple[int, str, str]] = {
    DocumentNotFoundError: (
        status.HTTP_404_NOT_FOUND,
        "DOCUMENT_NOT_FOUND",
        "document not found",
    ),
    # 권한 없음/숨김 상태 문서 직접 접근 — 존재를 숨긴다 (404 톤).
    DocumentHiddenError: (
        status.HTTP_404_NOT_FOUND,
        "DOCUMENT_NOT_READABLE",
        "hidden by read policy",
    ),
    InvalidRelationTypeError: (
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "INVALID_RELATION_TYPE",
        "invalid relation type",
    ),
    # 노드 컨텍스트 조회 실패는 SPEC-002 코드 재사용.
    OrgNodeNotFoundError: _SPEC002_CASE_MATRIX[OrgNodeNotFoundError],
    TreeNodeNotFoundError: _SPEC002_CASE_MATRIX[TreeNodeNotFoundError],
}

SPEC006_ERRORS = tuple(_SPEC006_CASE_MATRIX)


def spec006_http_error(exc: Exception) -> HTTPException:
    status_code, error_code, message = _SPEC006_CASE_MATRIX[type(exc)]
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code, "message": message},
    )


# ── SPEC-005 (Approval Gate) ─────────────────────────────────────────────────

from app.services.approval import (  # noqa: E402
    ApprovalDocumentUnavailableError,
    CandidateNotFoundError,
    CandidateNotPendingError,
    CandidateStaleError,
    DocumentTypeDuplicateError,
    DocumentTypeNotFoundError,
    InvalidAccessPolicyError,
    InvalidTreePathError,
    ReanalysisFailedError,
    RelationTargetRequiredError,
)

_SPEC005_CASE_MATRIX: dict[type[Exception], tuple[int, str, str]] = {
    CandidateNotFoundError: (
        status.HTTP_404_NOT_FOUND,
        "CANDIDATE_NOT_FOUND",
        "candidate not found",
    ),
    CandidateNotPendingError: (
        status.HTTP_409_CONFLICT,
        "CANDIDATE_NOT_PENDING",
        "candidate is not pending",
    ),
    CandidateStaleError: (
        status.HTTP_409_CONFLICT,
        "CANDIDATE_STALE",
        "candidate fingerprint mismatch",
    ),
    # SPEC-005 배너 문구용 — SPEC-007 DOCUMENT_UNAVAILABLE와 코드는 같고
    # 메시지는 SPEC-005 '백엔드 출력'을 따른다.
    ApprovalDocumentUnavailableError: (
        status.HTTP_409_CONFLICT,
        "DOCUMENT_UNAVAILABLE",
        "document is unavailable",
    ),
    DocumentTypeDuplicateError: (
        status.HTTP_409_CONFLICT,
        "DOCUMENT_TYPE_DUPLICATE",
        "duplicate document type",
    ),
    # 카탈로그에 없는 stable id — SPEC-005 Case Matrix 누락 코드 (spec 환류 후보).
    DocumentTypeNotFoundError: (
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "DOCUMENT_TYPE_NOT_FOUND",
        "document type not in catalog",
    ),
    InvalidTreePathError: (
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "INVALID_TREE_PATH",
        "invalid physical_tree_path",
    ),
    InvalidAccessPolicyError: (
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "INVALID_ACCESS_POLICY",
        "invalid access policy",
    ),
    RelationTargetRequiredError: (
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "RELATION_TARGET_REQUIRED",
        "unresolved relation target required",
    ),
    ReanalysisFailedError: (
        status.HTTP_409_CONFLICT,
        "REANALYSIS_FAILED",
        "reanalysis enqueue failed",
    ),
    # 귀속/관련 부서 노드 검증 실패는 SPEC-002 코드 재사용 (ORG_NODE_*).
    OrgNodeNotFoundError: _SPEC002_CASE_MATRIX[OrgNodeNotFoundError],
    OrgNodeInactiveError: _SPEC002_CASE_MATRIX[OrgNodeInactiveError],
}

SPEC005_ERRORS = tuple(_SPEC005_CASE_MATRIX)


def spec005_http_error(exc: Exception) -> HTTPException:
    status_code, error_code, message = _SPEC005_CASE_MATRIX[type(exc)]
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code, "message": message},
    )
