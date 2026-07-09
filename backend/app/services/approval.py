"""승인 게이트 service — WORK-005 (SPEC-005).

- 후보 큐/상세: 원장 state 5개 + 표시용 reanalysis_status 파생 계산 (DEC-022 —
  stale 후보 + ai_queue_jobs 상태에서 계산, 원장 저장 금지).
- approve: 승인 시점 재검사(state/fingerprint/document state/path/policy) →
  documents approved 필드 반영(노드 id) + related_departments 교체 + 확정
  relation 반영 + 후보 approved 종결. 부분 반영 금지(단일 트랜잭션),
  같은 요청 재시도는 멱등 성공 (Implementation Rules).
- preset: policy_preset을 read policy 필드로 풀어 저장 (DEC-018,
  core/policy_presets — 전역 policy.md가 SoT, DEC-017).
- 문서종류 추가: 정규화 이름 unique (DEC-007). 기존 문서 자동 변경 없음.
"""

from __future__ import annotations

import unicodedata

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.policy_presets import POLICY_PRESETS
from app.dtos.user import UserDTO
from app.models.ai_queue import AiQueueJob
from app.models.candidate import MetadataCandidate, RelationCandidate
from app.models.document import Document
from app.models.organization import DocumentType, OrganizationNode
from app.repos.ai_jobs import AiQueueJobRepository
from app.repos.candidates import MetadataCandidateRepository
from app.repos.document_types import DocumentTypeRepository
from app.repos.documents import DocumentRepository
from app.repos.organization import OrganizationRepository
from app.repos.relation_candidates import (
    DocumentRelationRepository,
    RelationCandidateRepository,
)
from app.schemas.approval import ApprovalPayload
from app.services.ai_jobs import (
    AiJobsService,
    ClassificationRetryExhaustedError,
    DocumentUnavailableError,
    fingerprint_key,
)
from app.services.document_tree import DocumentTreeService, TreeNodeNotFoundError
from app.services.organization import (
    InvalidTreeDepthError,
    OrgNodeInactiveError,
    OrgNodeNotFoundError,
)

# job 상태 → 표시용 재분석 상태 (DEC-022 Admin UI States)
_REANALYZING_JOB_STATUSES = ("queued", "running", "succeeded")
_REANALYSIS_FAILED_JOB_STATUSES = ("failed", "timeout", "validation_failed")


# ── SPEC-005 Case Matrix 예외 ────────────────────────────────────────────────


class CandidateNotFoundError(Exception):
    """CANDIDATE_NOT_FOUND — 후보 없음."""


class CandidateNotPendingError(Exception):
    """CANDIDATE_NOT_PENDING — 승인/거절 불가 상태."""


class CandidateStaleError(Exception):
    """CANDIDATE_STALE — fingerprint mismatch 또는 stale 후보 승인 시도."""


class ApprovalDocumentUnavailableError(Exception):
    """DOCUMENT_UNAVAILABLE — active 아닌 문서는 승인 불가."""


class DocumentTypeDuplicateError(Exception):
    """DOCUMENT_TYPE_DUPLICATE — 정규화 이름 중복."""


class DocumentTypeNotFoundError(Exception):
    """DOCUMENT_TYPE_NOT_FOUND — 카탈로그에 없는 stable id.

    SPEC-005 Case Matrix에 전용 코드가 없어 신설 — spec 환류 후보.
    """


class InvalidTreePathError(Exception):
    """INVALID_TREE_PATH — active path 검증 실패 / 귀속-위치 불일치."""


class InvalidAccessPolicyError(Exception):
    """INVALID_ACCESS_POLICY — access_logic/preset/read policy 조합 오류."""


class RelationTargetRequiredError(Exception):
    """RELATION_TARGET_REQUIRED — target 없는 relation은 확정 불가 (DEC-021)."""


class ReanalysisFailedError(Exception):
    """REANALYSIS_FAILED — 수동 재분석 enqueue 실패."""


def normalize_document_type_name(name: str) -> str:
    """문서종류 정규화 — NFKC + 공백 축약 + casefold (SPEC-005 U-4 unique 기준)."""
    collapsed = " ".join(unicodedata.normalize("NFKC", name).split())
    return collapsed.casefold()


class ApprovalService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._candidates = MetadataCandidateRepository(session)
        self._relations = RelationCandidateRepository(session)
        self._doc_relations = DocumentRelationRepository(session)
        self._docs = DocumentRepository(session)
        self._doc_types = DocumentTypeRepository(session)
        self._orgs = OrganizationRepository(session)
        self._jobs = AiQueueJobRepository(session)
        self._tree_service = DocumentTreeService(session)
        self._ai_jobs = AiJobsService(session)

    # ------------------------------------------------------------------
    # 조회 — 후보 큐/상세 (U-1/U-2)
    # ------------------------------------------------------------------

    async def list_candidates(
        self,
        *,
        state: str | None,
        read_capability: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[MetadataCandidate, Document, str | None]], int]:
        rows, total = await self._candidates.list_for_approval(
            state=state, read_capability=read_capability, limit=limit, offset=offset
        )
        result: list[tuple[MetadataCandidate, Document, str | None]] = []
        for candidate in rows:
            document = await self._docs.get(candidate.document_id)
            if document is None:  # FK상 불가 — 방어
                continue
            status = await self._derive_reanalysis_status(candidate)
            result.append((candidate, document, status))
        return result, total

    async def get_candidate(
        self, candidate_id: int
    ) -> tuple[
        MetadataCandidate, Document, str | None, list[tuple[RelationCandidate, str | None]]
    ]:
        candidate = await self._candidates.get(candidate_id)
        if candidate is None:
            raise CandidateNotFoundError
        document = await self._docs.get(candidate.document_id)
        if document is None:
            raise CandidateNotFoundError
        status = await self._derive_reanalysis_status(candidate)
        relations: list[tuple[RelationCandidate, str | None]] = []
        for rel in await self._relations.list_by_source(candidate.document_id):
            target_name = None
            if rel.target_document_id is not None:
                target = await self._docs.get(rel.target_document_id)
                target_name = target.drive_name if target else None
            relations.append((rel, target_name))
        return candidate, document, status, relations

    async def _derive_reanalysis_status(
        self, candidate: MetadataCandidate
    ) -> str | None:
        """표시용 파생 상태 — stale 후보에만 존재, 원장 저장 금지 (DEC-022).

        우선순위: 새 pending 후보 존재(new_candidate_ready) > 진행 중 job
        (reanalyzing) > 최신 job 실패(reanalysis_failed) > None.
        """
        if candidate.state != "stale":
            return None
        newer_pending = await self._candidates.get_pending_by_document(
            candidate.document_id
        )
        if newer_pending is not None and newer_pending.id != candidate.id:
            return "new_candidate_ready"
        latest: AiQueueJob | None = await self._jobs.get_latest_by_document(
            candidate.document_id
        )
        if latest is None:
            return None
        if latest.status in _REANALYZING_JOB_STATUSES:
            return "reanalyzing"
        if latest.status in _REANALYSIS_FAILED_JOB_STATUSES:
            return "reanalysis_failed"
        return None

    # ------------------------------------------------------------------
    # 승인 (S-1) — 재검사 → 반영 → 종결, 멱등
    # ------------------------------------------------------------------

    async def approve(
        self, candidate_id: int, payload: ApprovalPayload, *, admin: UserDTO
    ) -> tuple[MetadataCandidate, Document, list[int], bool]:
        """(candidate, document, related_department_ids, idempotent)."""
        candidate = await self._candidates.get(candidate_id)
        if candidate is None:
            raise CandidateNotFoundError
        document = await self._docs.get(candidate.document_id)
        if document is None:
            raise CandidateNotFoundError

        # 멱등 재시도: 이미 같은 결과로 승인됐다면 성공 (Implementation Rules)
        if candidate.state == "approved":
            applied = await self._validate_payload(document, payload)
            related = await self._docs.list_related_department_ids(document.id)
            if self._already_applied(document, applied) and sorted(
                set(payload.related_department_node_ids)
            ) == sorted(related):
                return candidate, document, related, True
            raise CandidateNotPendingError

        if candidate.state == "stale":
            raise CandidateStaleError
        if candidate.state == "blocked":
            raise ApprovalDocumentUnavailableError
        if candidate.state != "pending":
            raise CandidateNotPendingError

        # 승인 시점 재검사 — document state (SPEC-005 Validation)
        if document.drive_state != "active":
            await self._candidates.mark_blocked(
                candidate.id, reason=f"document {document.drive_state}"
            )
            await self._session.commit()
            raise ApprovalDocumentUnavailableError

        # 승인 시점 재검사 — fingerprint (candidate == current mirror)
        if fingerprint_key(candidate.candidate_fingerprint) != fingerprint_key(
            document.drive_fingerprint
        ):
            await self._candidates.mark_stale(
                candidate.id, reason="Drive fingerprint changed at approval"
            )
            await self._session.commit()
            raise CandidateStaleError

        applied = await self._validate_payload(document, payload)

        # 반영 — 단일 트랜잭션 (documents + related + relations + candidate)
        await self._docs.apply_approved_metadata(document.id, **applied)
        await self._docs.replace_related_departments(
            document.id, payload.related_department_node_ids
        )
        await self._apply_resolved_relations(document, admin)
        await self._candidates.mark_approved(candidate.id, approved_by=admin.id)
        await self._session.commit()
        # onupdate 서버 생성 컬럼(updated_at) 재조회 — 응답 직렬화용
        await self._session.refresh(candidate)
        await self._session.refresh(document)

        related = await self._docs.list_related_department_ids(document.id)
        return candidate, document, related, False

    async def _apply_resolved_relations(
        self, document: Document, admin: UserDTO
    ) -> None:
        """target 지정된(pending) relation 후보만 확정 graph에 반영 (DEC-021).

        unresolved는 반영하지 않고 보류 상태로 남긴다 — 승인 차단 사유가 아니다
        (U-6 `보류`). (source,target,type) 중복은 멱등 skip.
        """
        for rel in await self._relations.list_pending_with_target(document.id):
            assert rel.target_document_id is not None
            existing = await self._doc_relations.get_by_key(
                source_document_id=document.id,
                target_document_id=rel.target_document_id,
                relation_type=rel.suggested_relation_type,
            )
            if existing is None:
                await self._doc_relations.create(
                    source_document_id=document.id,
                    target_document_id=rel.target_document_id,
                    relation_type=rel.suggested_relation_type,
                    source_label=rel.raw_label,
                    approved_by=admin.id,
                )
            await self._relations.set_state(
                rel.id, state="approved", resolved_by=admin.id
            )

    # ------------------------------------------------------------------
    # payload validation (SPEC-005 Validation 표)
    # ------------------------------------------------------------------

    async def _validate_payload(
        self, document: Document, payload: ApprovalPayload
    ) -> dict:
        """검증 통과 시 documents에 반영할 approved 필드 dict를 돌려준다."""
        # document_type — 카탈로그 stable id 존재 (DEC-007)
        doc_type = await self._doc_types.get(payload.document_type_id)
        if doc_type is None:
            raise DocumentTypeNotFoundError

        # physical_tree_path — WORK-002 active path 검증 재사용
        try:
            validated = await self._tree_service.validate_active_path(
                payload.physical_tree_path.organization_path,
                payload.physical_tree_path.tree_path,
            )
        except (
            OrgNodeNotFoundError,
            OrgNodeInactiveError,
            InvalidTreeDepthError,
            TreeNodeNotFoundError,
        ) as exc:
            raise InvalidTreePathError from exc

        # owning_department — 단일값이며 path의 귀속 부서와 일치해야 한다
        if (
            validated.owning_department_node_id is None
            or payload.owning_department_node_id
            != validated.owning_department_node_id
        ):
            raise InvalidTreePathError

        # created/related 부서 — active 조직 노드 (SPEC-002 Case Matrix 재사용)
        if payload.created_department_node_id is not None:
            await self._require_active_org_node(payload.created_department_node_id)
        for node_id in payload.related_department_node_ids:
            await self._require_active_org_node(node_id)

        # access policy — logic/preset 조합 + preset 풀어 저장 (DEC-018)
        read_roles = [r.strip() for r in payload.read_roles if r.strip()]
        read_departments = list(dict.fromkeys(payload.read_departments))
        read_positions = [p.strip() for p in payload.read_positions if p.strip()]

        if payload.access_logic == "PRESET":
            if payload.policy_preset is None:
                raise InvalidAccessPolicyError
            preset = POLICY_PRESETS.get(payload.policy_preset)
            if preset is None:
                raise InvalidAccessPolicyError
            # preset을 read policy 필드로 풀어 저장 — 판정은 풀어 저장된 필드
            # 기준 (SPEC-001). 부서 힌트는 active 조직 노드 이름 match만 반영.
            read_roles = list(preset.read_roles)
            read_departments = await self._resolve_preset_departments(preset)
            read_positions = list(preset.read_positions)
        else:
            if payload.policy_preset is not None:
                # preset 이름은 access_logic=PRESET에서만 유효
                raise InvalidAccessPolicyError
            for node_id in read_departments:
                node = await self._orgs.get_node(node_id)
                if node is None or node.status != "active":
                    raise InvalidAccessPolicyError

        return {
            "document_type_id": doc_type.id,
            "created_department_node_id": payload.created_department_node_id,
            "owning_department_node_id": validated.owning_department_node_id,
            "organization_path": validated.organization_path,
            "tree_path": validated.tree_path,
            "related_products": list(payload.related_products),
            "read_roles": read_roles,
            "read_departments": read_departments,
            "read_positions": read_positions,
            "access_logic": payload.access_logic,
            "sensitivity": payload.sensitivity,
            "policy_preset": payload.policy_preset,
            "summary": payload.summary,
        }

    async def _require_active_org_node(self, node_id: int) -> OrganizationNode:
        node = await self._orgs.get_node(node_id)
        if node is None:
            raise OrgNodeNotFoundError
        if node.status != "active":
            raise OrgNodeInactiveError
        return node

    async def _resolve_preset_departments(self, preset) -> list[int]:
        """preset 부서 힌트 → active 조직 노드 id (대소문자 무시 이름 일치만)."""
        nodes = await self._orgs.list_nodes()
        hints = {h.casefold() for h in preset.department_name_hints}
        return [
            n.id
            for n in nodes
            if n.status == "active"
            and n.type == "department"
            and n.name.casefold() in hints
        ]

    def _already_applied(self, document: Document, applied: dict) -> bool:
        """멱등 판정 — 같은 payload가 이미 documents에 반영된 상태인지."""
        current = {
            "document_type_id": document.document_type_id,
            "created_department_node_id": document.created_department_node_id,
            "owning_department_node_id": document.owning_department_node_id,
            "organization_path": list(document.organization_path or []),
            "tree_path": list(document.tree_path or []),
            "related_products": list(document.related_products or []),
            "read_roles": list(document.read_roles or []),
            "read_departments": list(document.read_departments or []),
            "read_positions": list(document.read_positions or []),
            "access_logic": document.access_logic,
            "sensitivity": document.sensitivity,
            "policy_preset": document.policy_preset,
            "summary": document.summary,
        }
        normalized = {
            **applied,
            "organization_path": list(applied["organization_path"]),
            "tree_path": list(applied["tree_path"]),
        }
        return current == normalized

    # ------------------------------------------------------------------
    # 거절 / 수동 재분석 (U-2)
    # ------------------------------------------------------------------

    async def reject(
        self, candidate_id: int, *, admin: UserDTO, reason: str | None = None
    ) -> MetadataCandidate:
        candidate = await self._candidates.get(candidate_id)
        if candidate is None:
            raise CandidateNotFoundError
        if candidate.state == "rejected":
            return candidate  # 재시도 멱등
        if candidate.state not in ("pending", "stale"):
            raise CandidateNotPendingError
        row = await self._candidates.mark_rejected(
            candidate_id, reason=reason or f"rejected by admin #{admin.id}"
        )
        if row is None:  # 동시성 방어
            raise CandidateNotPendingError
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def reanalyze(self, candidate_id: int, *, admin: UserDTO) -> AiQueueJob:
        """수동 재분석 — WORK-004 enqueue_reanalysis 위임 (SPEC-005 U-2)."""
        candidate = await self._candidates.get(candidate_id)
        if candidate is None:
            raise CandidateNotFoundError
        try:
            job = await self._ai_jobs.enqueue_reanalysis(
                candidate.document_id, requested_by=admin.id
            )
        except DocumentUnavailableError as exc:
            raise ApprovalDocumentUnavailableError from exc
        except ClassificationRetryExhaustedError as exc:
            raise ReanalysisFailedError from exc
        await self._session.commit()
        return job

    # ------------------------------------------------------------------
    # 문서종류 카탈로그 (U-4/S-3)
    # ------------------------------------------------------------------

    async def list_document_types(self) -> list[DocumentType]:
        return await self._doc_types.list_all()

    async def create_document_type(
        self, *, name: str, admin: UserDTO
    ) -> DocumentType:
        """정규화 이름 unique. 기존 문서의 문서종류는 자동 변경하지 않는다 (U-4)."""
        display_name = " ".join(name.split())
        normalized = normalize_document_type_name(name)
        if not normalized:
            raise DocumentTypeDuplicateError  # 빈 이름은 저장 불가 (schema가 1차 방어)
        existing = await self._doc_types.get_by_normalized(normalized)
        if existing is not None:
            raise DocumentTypeDuplicateError
        row = await self._doc_types.create(
            name=display_name, normalized_name=normalized, created_by=admin.id
        )
        await self._session.commit()
        return row
