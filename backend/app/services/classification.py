"""AI classification input 조립 + 결과 검증/resolve/저장 — WORK-004 (SPEC-007).

- Classification Input: Drive mirror + analysis_text(최소 범위) + WORK-002
  조직/트리/카탈로그 context + policy/relation_type context. 본문을 못 읽으면
  read_capability=metadata_only로 제한 입력만 전달한다.
- Classification Output: 아래 pydantic 모델이 backend validator의 단일 원천이다.
  worker workspace 가이드(context/classification-guide.md)의 schema 문서는 이
  모델과 동기 유지한다 (드리프트 금지 — Internal Interface Contract).
- 결과 저장: schema 검증 → fingerprint freshness → 부서/트리 노드 id resolve
  (실패 값은 admin 보정 대상 표시) → metadata_candidates pending 저장(문서당
  pending 1개, candidate_fingerprint 멱등) + relation_candidates 저장
  (target 없으면 unresolved, 자동 문서 생성 금지 — DEC-021).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.integrations.google_drive import DriveApiError, GoogleDriveClient
from app.models.ai_queue import AiQueueJob
from app.models.candidate import RELATION_TYPES, MetadataCandidate
from app.models.document import Document
from app.repos.candidates import MetadataCandidateRepository
from app.repos.document_tree import DocumentTreeRepository
from app.repos.document_types import DocumentTypeRepository
from app.repos.documents import DocumentRepository
from app.repos.organization import OrganizationRepository
from app.repos.relation_candidates import RelationCandidateRepository
from app.services.ai_jobs import AiJobsService, fingerprint_key

logger = logging.getLogger(__name__)

# 민감 문서 preset 목록 (context/policy.md 기준 — DEC-008/017/018).
# 정의 SoT 상수는 core/policy_presets — WORK-005 승인 게이트와 공유한다.
from app.core.policy_presets import POLICY_PRESET_NAMES as KNOWN_POLICY_PRESETS  # noqa: E402

# v1 analysis_text 추출 지원 MIME (WORK-004 Open Issue 확정 — 텍스트 계열 최소).
GOOGLE_DOC_EXPORT_MIMES = ("application/vnd.google-apps.document",)
PLAIN_TEXT_MIMES = ("text/plain", "text/markdown", "text/csv")


# ----------------------------------------------------------------------
# Classification Output schema (SPEC-007 Output 계약 — validator 단일 원천)
# ----------------------------------------------------------------------


class PhysicalTreePathOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_path: list[str] = Field(default_factory=list)
    tree_path: list[str] = Field(default_factory=list)


class ReadPolicyOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_roles: list[str] = Field(default_factory=list)
    read_departments: list[str] = Field(default_factory=list)
    read_positions: list[str] = Field(default_factory=list)
    access_logic: Literal["ANY", "ALL", "PRESET"] = "ANY"


class RelationCandidateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_label: str
    relation_type: Literal[
        "related", "references", "supersedes", "duplicate_candidate"
    ]
    target_hint: str | None = None


class ClassificationOutput(BaseModel):
    """AI task result JSON — 이 schema를 통과한 결과만 candidate로 저장한다."""

    model_config = ConfigDict(extra="forbid")

    document_type: str
    created_department: str | None = None
    owning_department: str
    physical_tree_path: PhysicalTreePathOut
    related_departments: list[str] = Field(default_factory=list)
    related_products: list[str] = Field(default_factory=list)
    summary: str | None = None
    sensitivity: Literal["normal", "sensitive"]
    policy_preset: (
        Literal[
            "HR_RESTRICTED",
            "CONTRACT_RESTRICTED",
            "FINANCE_RESTRICTED",
            "SECURITY_RESTRICTED",
            "LEGAL_RESTRICTED",
        ]
        | None
    ) = None
    read_policy: ReadPolicyOut
    relation_candidates: list[RelationCandidateOut] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class ClassificationResultInvalidError(Exception):
    """CLASSIFICATION_RESULT_INVALID — valid JSON object/schema 아님."""


def parse_classification_output(raw_text: str | None) -> ClassificationOutput:
    """task result 텍스트 → ClassificationOutput. 코드펜스 허용, 그 외 불허."""
    if not raw_text or not raw_text.strip():
        raise ClassificationResultInvalidError("empty task result")
    text = raw_text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        # JSON object 본문만 추출 (앞뒤 잡음 최소 허용).
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ClassificationResultInvalidError("no JSON object in result")
        text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClassificationResultInvalidError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ClassificationResultInvalidError("result is not a JSON object")
    try:
        return ClassificationOutput.model_validate(payload)
    except ValidationError as exc:
        raise ClassificationResultInvalidError(
            f"schema validation failed: {exc.error_count()} errors"
        ) from exc


# ----------------------------------------------------------------------
# 결과 처리 outcome
# ----------------------------------------------------------------------


@dataclass
class ProcessOutcome:
    status: Literal["candidate_saved", "validation_failed", "stale"]
    candidate: MetadataCandidate | None = None
    new_job: AiQueueJob | None = None
    message: str | None = None


class ClassificationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        drive_client: GoogleDriveClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._drive = drive_client
        self._docs = DocumentRepository(session)
        self._org = OrganizationRepository(session)
        self._tree = DocumentTreeRepository(session)
        self._doc_types = DocumentTypeRepository(session)
        self._candidates = MetadataCandidateRepository(session)
        self._relations = RelationCandidateRepository(session)
        self._jobs = AiJobsService(session, settings=settings)

    # ------------------------------------------------------------------
    # Classification Input (SPEC-007 계약)
    # ------------------------------------------------------------------

    async def extract_analysis_text(self, document: Document) -> str | None:
        """v1 텍스트 추출 — Google Docs export + text/* download만. 실패/미지원
        MIME은 None(→ metadata_only). 원문은 DB에 저장하지 않는다."""
        if self._drive is None or not self._drive.configured:
            return None
        mime = document.drive_mime_type
        try:
            if mime in GOOGLE_DOC_EXPORT_MIMES:
                text = await self._drive.export_file_text(document.drive_file_id)
            elif mime in PLAIN_TEXT_MIMES:
                text = await self._drive.download_file_text(document.drive_file_id)
            else:
                return None
        except DriveApiError:
            logger.warning(
                "analysis text extraction failed: document=%s", document.id
            )
            return None
        text = text.strip()
        if not text:
            return None
        return text[: self._settings.analysis_text_max_chars]

    async def build_input(
        self, document: Document, *, analysis_text: str | None
    ) -> dict:
        """SPEC-007 Classification Input 조립. secret/OAuth token 미포함."""
        org_nodes = await self._org.list_nodes()
        tree_nodes = await self._tree.list_nodes()
        doc_types = await self._doc_types.list_all()

        read_capability = "content_read" if analysis_text else "metadata_only"
        drive_mirror = {
            "drive_name": document.drive_name,
            "mime_type": document.drive_mime_type,
            "drive_modified_time": (
                document.drive_modified_time.isoformat()
                if document.drive_modified_time
                else None
            ),
            # web url은 제외 가능 필드 — 분류에 불필요해 전달하지 않는다.
        }

        return {
            "document_id": document.id,
            "drive_file_id": document.drive_file_id,
            "drive_fingerprint": document.drive_fingerprint,
            "drive_mirror": drive_mirror,
            "read_capability": read_capability,
            "analysis_text": analysis_text,
            "organization_context": {
                "nodes": [
                    {
                        "id": n.id,
                        "type": n.type,
                        "name": n.name,
                        "parent_id": n.parent_id,
                    }
                    for n in org_nodes
                    if n.status == "active"
                ]
            },
            "document_tree_context": {
                "nodes": [
                    {
                        "id": n.id,
                        "type": n.type,
                        "name": n.name,
                        "parent_id": n.parent_id,
                        "organization_node_id": n.organization_node_id,
                        "document_type_id": n.document_type_id,
                    }
                    for n in tree_nodes
                    if n.status == "active"
                ]
            },
            "document_type_catalog": [
                {"id": t.id, "name": t.name} for t in doc_types
            ],
            "policy_context": {
                "source": "context/classification-guide.md",
                "presets": list(KNOWN_POLICY_PRESETS),
                "rules": [
                    "정책 판단은 추천이다. 최종 확정은 관리자 승인 게이트에서 한다.",
                    "HR/계약/재무/보안/법무 문서는 기본 sensitive + 해당 preset 후보.",
                    "민감 문서는 더 좁은 read policy를 추천한다.",
                    "Drive 폴더 위치만 보고 접근권한을 확정하지 않는다.",
                    "본문에 secret이 있으면 값을 복사하지 말고 위험만 표시한다.",
                ],
            },
            "relation_type_catalog": list(RELATION_TYPES),
        }

    def build_prompt(self, input_payload: dict) -> str:
        """classification prompt + JSON output instruction (SPEC-007 Submit)."""
        return (
            "다음은 cloud-file-organizer 문서 분류 task다.\n"
            "workspace의 `agent.md`와 `context/classification-guide.md` 규칙에 따라 "
            "아래 Classification Input을 분석하고, guide의 Output Schema를 따르는 "
            "**JSON object 하나만** 출력한다. JSON 외의 설명 문장, 코드펜스 밖 "
            "텍스트를 출력하지 않는다.\n\n"
            "## Classification Input\n"
            "```json\n"
            + json.dumps(input_payload, ensure_ascii=False, indent=2)
            + "\n```\n"
        )

    # ------------------------------------------------------------------
    # 결과 검증 → resolve → candidate 저장 (SPEC-007 Validation)
    # ------------------------------------------------------------------

    async def process_result(
        self,
        job: AiQueueJob,
        raw_text: str | None,
        *,
        read_capability: str = "content_read",
    ) -> ProcessOutcome:
        """succeeded job의 결과 처리. job 상태 전이까지 수행한다.

        read_capability는 input 조립 시 판정값 (S-2 metadata_only 표시용).
        """
        # 1) schema validation
        try:
            output = parse_classification_output(raw_text)
        except ClassificationResultInvalidError as exc:
            await self._jobs.mark_validation_failed(job, message=str(exc))
            return ProcessOutcome(status="validation_failed", message=str(exc))

        # 2) fingerprint freshness — 저장 직전 재확인 (ARCH-002 AC)
        document = await self._docs.get(job.document_id)
        current_fp = (
            fingerprint_key(document.drive_fingerprint) if document else None
        )
        if (
            document is None
            or document.drive_state != "active"
            or current_fp != job.fingerprint
        ):
            reenqueue = (
                document.drive_fingerprint
                if document is not None and document.drive_state == "active"
                else None
            )
            _, new_job = await self._jobs.mark_stale(
                job,
                reason="fingerprint changed before candidate save",
                reenqueue_fingerprint=reenqueue,
            )
            return ProcessOutcome(status="stale", new_job=new_job)

        # 3) 노드 id resolve + candidate_metadata 조립
        candidate_metadata = await self._resolve_output(
            document, output, read_capability=read_capability
        )

        # 4) pending candidate 저장 — 문서당 pending 1개
        candidate = await self._save_pending_candidate(
            document, candidate_metadata, read_capability
        )

        # 5) relation candidates 저장 (unresolved 포함, 문서 자동 생성 금지)
        await self._save_relation_candidates(document, output, candidate)

        # 6) job 전이: succeeded → candidate_saved
        await self._jobs.mark_candidate_saved(job, candidate_id=candidate.id)
        return ProcessOutcome(status="candidate_saved", candidate=candidate)

    async def _resolve_output(
        self,
        document: Document,
        output: ClassificationOutput,
        *,
        read_capability: str,
    ) -> dict:
        """부서/트리 명칭 후보 → 노드 id resolve (SPEC-007 Validation).

        resolve 실패 값은 삭제하지 않고 unresolved_fields로 표시해 승인 게이트의
        admin 보정 대상으로 넘긴다.
        """
        org_nodes = await self._org.list_nodes()
        tree_nodes = await self._tree.list_nodes()
        doc_types = await self._doc_types.list_all()

        active_org = [n for n in org_nodes if n.status == "active"]
        active_tree = [n for n in tree_nodes if n.status == "active"]
        org_by_name: dict[str, int] = {}
        for n in active_org:
            org_by_name.setdefault(n.name, n.id)
        tree_by_name: dict[str, int] = {}
        for n in active_tree:
            tree_by_name.setdefault(n.name, n.id)
        type_by_name = {t.name: t.id for t in doc_types}

        unresolved: list[str] = []

        owning_id = org_by_name.get(output.owning_department)
        if owning_id is None:
            unresolved.append("owning_department")

        created_id = None
        if output.created_department:
            created_id = org_by_name.get(output.created_department)
            if created_id is None:
                unresolved.append("created_department")

        org_path_ids: list[int | None] = []
        for name in output.physical_tree_path.organization_path:
            node_id = org_by_name.get(name)
            org_path_ids.append(node_id)
            if node_id is None and "physical_tree_path.organization_path" not in unresolved:
                unresolved.append("physical_tree_path.organization_path")

        tree_path_ids: list[int | None] = []
        for name in output.physical_tree_path.tree_path:
            node_id = tree_by_name.get(name)
            tree_path_ids.append(node_id)
            if node_id is None and "physical_tree_path.tree_path" not in unresolved:
                unresolved.append("physical_tree_path.tree_path")

        read_department_ids: list[int | None] = []
        for name in output.read_policy.read_departments:
            node_id = org_by_name.get(name)
            read_department_ids.append(node_id)
            if node_id is None and "read_policy.read_departments" not in unresolved:
                unresolved.append("read_policy.read_departments")

        related_department_ids: list[int | None] = []
        for name in output.related_departments:
            node_id = org_by_name.get(name)
            related_department_ids.append(node_id)
            if node_id is None and "related_departments" not in unresolved:
                unresolved.append("related_departments")

        # document_type: catalog 값이 아니면 승인 게이트에서 '추가 필요 후보' 표시
        # (SPEC-007 Validation, WORK-005 form 계약 — candidate_metadata 플래그).
        document_type_id = type_by_name.get(output.document_type)

        return {
            **output.model_dump(),
            "read_capability": read_capability,
            "resolution": {
                "document_type_id": document_type_id,
                "document_type_is_new": document_type_id is None,
                "owning_department_node_id": owning_id,
                "created_department_node_id": created_id,
                "organization_path_node_ids": org_path_ids,
                "tree_path_node_ids": tree_path_ids,
                "read_department_node_ids": read_department_ids,
                "related_department_node_ids": related_department_ids,
                "unresolved_fields": unresolved,
                "needs_admin_fix": bool(unresolved),
            },
        }

    async def _save_pending_candidate(
        self,
        document: Document,
        candidate_metadata: dict,
        read_capability: str,
    ) -> MetadataCandidate:
        """문서당 pending 1개 규칙.

        - 기존 pending의 candidate_fingerprint가 같으면(중복 결과) payload만
          갱신한다 — (document_id, candidate_fingerprint) 멱등.
        - 다르면 기존 pending을 stale(superseded)로 밀고 새 pending을 만든다.
        """
        existing = await self._candidates.get_pending_by_document(document.id)
        if existing is not None:
            if fingerprint_key(existing.candidate_fingerprint) == fingerprint_key(
                document.drive_fingerprint
            ):
                updated = await self._candidates.replace_pending_payload(
                    existing.id,
                    read_capability=read_capability,
                    candidate_metadata=candidate_metadata,
                    reason="idempotent update: same candidate fingerprint",
                )
                if updated is not None:
                    return updated
            else:
                await self._candidates.mark_stale(
                    existing.id, reason="superseded by newer analysis result"
                )
        return await self._candidates.create_pending(
            document_id=document.id,
            read_capability=read_capability,
            candidate_metadata=candidate_metadata,
            candidate_fingerprint=document.drive_fingerprint,
        )

    async def _save_relation_candidates(
        self,
        document: Document,
        output: ClassificationOutput,
        candidate: MetadataCandidate,
    ) -> None:
        for rel in output.relation_candidates:
            raw_label = rel.raw_label.strip()
            if not raw_label:
                continue
            existing = await self._relations.find_open(
                source_document_id=document.id,
                raw_label=raw_label,
                suggested_relation_type=rel.relation_type,
            )
            if existing is not None:
                continue
            target = await self._resolve_relation_target(
                document, raw_label, rel.target_hint
            )
            await self._relations.create(
                source_document_id=document.id,
                raw_label=raw_label,
                suggested_relation_type=rel.relation_type,
                target_document_id=target.id if target else None,
                state="pending" if target else "unresolved",
            )

    async def _resolve_relation_target(
        self, document: Document, raw_label: str, target_hint: str | None
    ) -> Document | None:
        """wikilink 라벨 → 기존 document. 못 찾으면 None (row 생성 금지)."""
        name = raw_label
        match = re.match(r"^\[\[(.+?)\]\]$", raw_label)
        if match:
            name = match.group(1).strip()
        for candidate_name in filter(None, (target_hint, name)):
            target = await self._docs.find_by_drive_name(
                candidate_name, exclude_document_id=document.id
            )
            if target is not None:
                return target
        return None
