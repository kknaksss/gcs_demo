"""문서 탐색/관계 service — WORK-006 (SPEC-002/003/006).

- 물리 귀속 목록(tree-documents): 선택 조직/트리 노드 기준 — 논리 연결 문서 혼입 금지 (DEC-014).
- 관련 문서: related_departments / related_products / 승인 document_relations 3원천 병합.
- 문서 상세 relation: approved relation만, target 파생 상태(broken)는 저장 없이 계산 (SPEC-006).
- 통합 검색: 물리 귀속 + 관련 문서, source_badge 표시.
- RBAC: 모든 표면에서 WORK-001 `evaluate_read`가 숨김 판정의 단일 원천이다.
  권한 없는 문서는 잠금 표시 없이 응답에서 제거한다 (SPEC-001 Visibility).
- explorer 표면은 승인(approved = organization_path 보유)·active 문서만 노출한다.
  unresolved relation candidate는 어떤 응답에도 나오지 않는다 (DEC-021).
"""

from __future__ import annotations

import dataclasses

from sqlalchemy.ext.asyncio import AsyncSession

from app.dtos.path import ValidatedPath
from app.dtos.rbac import ReadPolicy
from app.dtos.user import UserDTO
from app.models.candidate import RELATION_TYPES, DocumentRelation, MetadataCandidate
from app.models.document import Document
from app.models.organization import OrganizationNode
from app.repos.candidates import MetadataCandidateRepository
from app.repos.document_tree import DocumentTreeRepository
from app.repos.document_types import DocumentTypeRepository
from app.repos.documents import DocumentRepository
from app.repos.organization import OrganizationRepository
from app.repos.relation_candidates import DocumentRelationRepository
from app.services.document_tree import TreeNodeNotFoundError
from app.services.documents import (
    HIDDEN_DRIVE_STATES,
    DocumentHiddenError,
    DocumentNotFoundError,
)
from app.services.organization import OrgNodeNotFoundError
from app.services.rbac import evaluate_read

_DISPLAY_SEPARATOR = " / "

# v1 relation type 4종 — 한국어 label (SPEC-006 U-2/U-4 문구)
RELATION_TYPE_LABELS: dict[str, str] = {
    "related": "관련",
    "references": "참조",
    "supersedes": "대체",
    "duplicate_candidate": "중복 후보",
}


class InvalidRelationTypeError(Exception):
    """INVALID_RELATION_TYPE — v1 enum 4종 외 relation type filter."""


@dataclasses.dataclass(frozen=True)
class TreeDocumentItem:
    document: Document
    path: ValidatedPath
    document_type_name: str | None


@dataclasses.dataclass(frozen=True)
class RelatedDocumentItem:
    document: Document
    path: ValidatedPath
    source: str  # related_department | related_product | document_relation
    relation_type: str | None
    match_reason: str


@dataclasses.dataclass(frozen=True)
class RelationItem:
    relation: DocumentRelation
    source_drive_name: str | None
    target_drive_name: str | None
    target_state: str  # target 문서 drive_state 파생 — 저장하지 않는다


@dataclasses.dataclass(frozen=True)
class SearchItem:
    document: Document
    path: ValidatedPath
    source_badge: str  # physical | related
    relation_type: str | None = None


@dataclasses.dataclass(frozen=True)
class DocumentDetail:
    document: Document
    path: ValidatedPath | None
    document_type_name: str | None
    related_departments: list[OrganizationNode]
    # admin 전용 — pending metadata 후보 (`승인 대기` badge, SPEC-003 U-2)
    pending_candidate: MetadataCandidate | None


class _PathResolver:
    """org/tree 노드 map을 요청당 1회 로드해 표시 path를 계산한다.

    path array는 노드 id 저장이므로 rename 후에도 최신 이름이 나온다 (SPEC-002).
    """

    def __init__(self, org_map: dict[int, object], tree_map: dict[int, object]) -> None:
        self._org_map = org_map
        self._tree_map = tree_map

    def resolve(self, document: Document) -> ValidatedPath:
        org_nodes = [
            self._org_map[i]
            for i in (document.organization_path or [])
            if i in self._org_map
        ]
        tree_nodes = [
            self._tree_map[i]
            for i in (document.tree_path or [])
            if i in self._tree_map
        ]
        owning = next(
            (n for n in reversed(org_nodes) if n.type == "department"), None
        )
        return ValidatedPath(
            organization_path=list(document.organization_path or []),
            tree_path=list(document.tree_path or []),
            display_path=_DISPLAY_SEPARATOR.join(
                [n.name for n in org_nodes] + [n.name for n in tree_nodes]
            ),
            owning_department_node_id=owning.id if owning else None,
            owning_department=owning.name if owning else None,
        )


def _read_policy(document: Document) -> ReadPolicy:
    return ReadPolicy.from_mapping(
        {
            "read_roles": document.read_roles,
            "read_departments": document.read_departments,
            "read_positions": document.read_positions,
            "access_logic": document.access_logic,
            "sensitivity": document.sensitivity,
            "policy_preset": document.policy_preset,
        }
    )


def validate_relation_type(relation_type: str | None) -> str | None:
    if relation_type is not None and relation_type not in RELATION_TYPES:
        raise InvalidRelationTypeError
    return relation_type


class ExplorerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._docs = DocumentRepository(session)
        self._orgs = OrganizationRepository(session)
        self._tree = DocumentTreeRepository(session)
        self._types = DocumentTypeRepository(session)
        self._relations = DocumentRelationRepository(session)
        self._candidates = MetadataCandidateRepository(session)

    # ------------------------------------------------------------------
    # 공통 helper
    # ------------------------------------------------------------------

    def _readable(self, user: UserDTO, document: Document) -> bool:
        """숨김 판정 단일 원천 — WORK-001 evaluate_read 재사용 (재구현 금지)."""
        return evaluate_read(user, _read_policy(document)).final_readable

    def _visible_in_explorer(self, user: UserDTO, document: Document) -> bool:
        """explorer 표면 공통 노출 규칙: active + 승인(approved) + readable."""
        if document.drive_state != "active":
            return False
        if not document.organization_path:
            return False
        return self._readable(user, document)

    async def _path_resolver(self) -> _PathResolver:
        org_nodes = await self._orgs.list_nodes()
        tree_nodes = await self._tree.list_nodes()
        return _PathResolver(
            {n.id: n for n in org_nodes}, {n.id: n for n in tree_nodes}
        )

    async def _document_type_names(self) -> dict[int, str]:
        return {t.id: t.name for t in await self._types.list_all()}

    async def _get_visible_document(
        self, user: UserDTO, document_id: int
    ) -> Document:
        """상세/관련/relation 진입 guard — 직접 접근은 404 톤 (DOCUMENT_NOT_READABLE)."""
        document = await self._docs.get(document_id)
        if document is None:
            raise DocumentNotFoundError
        if not user.is_admin:
            if document.drive_state in HIDDEN_DRIVE_STATES:
                raise DocumentHiddenError
            if not self._readable(user, document):
                raise DocumentHiddenError
        return document

    # ------------------------------------------------------------------
    # 물리 귀속 목록 — GET /tree-documents (SPEC-002/006 U-1)
    # ------------------------------------------------------------------

    async def tree_documents(
        self,
        user: UserDTO,
        *,
        org_node_id: int,
        tree_node_id: int | None = None,
    ) -> tuple[OrganizationNode, list[TreeDocumentItem]]:
        """선택 노드에 물리 귀속된 문서만 — 논리 연결 문서 혼입 금지 (DEC-014)."""
        org = await self._orgs.get_node(org_node_id)
        if org is None:
            raise OrgNodeNotFoundError
        if tree_node_id is not None:
            tree_node = await self._tree.get_node(tree_node_id)
            if tree_node is None:
                raise TreeNodeNotFoundError
            documents = await self._docs.list_active_by_tree_node(
                tree_node_id, org_node_id=org_node_id
            )
        else:
            documents = await self._docs.list_active_by_org_node(org_node_id)

        resolver = await self._path_resolver()
        type_names = await self._document_type_names()
        items = [
            TreeDocumentItem(
                document=doc,
                path=resolver.resolve(doc),
                document_type_name=type_names.get(doc.document_type_id or -1),
            )
            for doc in documents
            if self._visible_in_explorer(user, doc)
        ]
        return org, items

    # ------------------------------------------------------------------
    # 관련 문서 — 3원천 병합 (SPEC-006 U-2, DEC-014)
    # ------------------------------------------------------------------

    async def related_documents_for_department(
        self,
        user: UserDTO,
        department_node_id: int,
        *,
        relation_type: str | None = None,
    ) -> list[RelatedDocumentItem]:
        validate_relation_type(relation_type)
        dept = await self._orgs.get_node(department_node_id)
        if dept is None:
            raise OrgNodeNotFoundError

        physical = await self._docs.list_active_by_org_node(dept.id)
        physical_ids = {d.id for d in physical}

        merged: dict[int, tuple[Document, str, str | None, str]] = {}

        # 1) 승인 document_relations — 부서 물리 문서와 연결된 반대편 문서
        relations = await self._relations.list_touching(sorted(physical_ids))
        other_ids = [
            r.target_document_id
            if r.source_document_id in physical_ids
            else r.source_document_id
            for r in relations
        ]
        others = await self._docs.get_many(other_ids)
        for rel in relations:
            other_id = (
                rel.target_document_id
                if rel.source_document_id in physical_ids
                else rel.source_document_id
            )
            other = others.get(other_id)
            if other is None or other.id in physical_ids or other.id in merged:
                continue
            label = RELATION_TYPE_LABELS.get(rel.relation_type, rel.relation_type)
            reason = rel.source_label or f"문서 관계: {label}"
            merged[other.id] = (other, "document_relation", rel.relation_type, reason)

        # 2) related_departments 역방향 — 이 부서를 관련 부서로 지정한 문서
        for doc in await self._docs.list_active_related_to_department(dept.id):
            if doc.id in physical_ids or doc.id in merged:
                continue
            merged[doc.id] = (
                doc,
                "related_department",
                None,
                f"관련 부서: {dept.name}",
            )

        # 3) related_products — 부서 물리 문서의 제품 태그와 겹치는 문서
        products = sorted(
            {p for doc in physical for p in (doc.related_products or [])}
        )
        for doc in await self._docs.list_active_by_products(
            products, exclude_ids=sorted(physical_ids)
        ):
            if doc.id in merged:
                continue
            shared = sorted(set(doc.related_products or []).intersection(products))
            merged[doc.id] = (
                doc,
                "related_product",
                None,
                f"관련 제품: {', '.join(shared)}",
            )

        return await self._build_related_items(
            user, list(merged.values()), relation_type=relation_type
        )

    async def related_documents_for_document(
        self,
        user: UserDTO,
        document_id: int,
        *,
        relation_type: str | None = None,
    ) -> list[RelatedDocumentItem]:
        validate_relation_type(relation_type)
        document = await self._get_visible_document(user, document_id)

        merged: dict[int, tuple[Document, str, str | None, str]] = {}

        # 1) 승인 document_relations
        relations = await self._relations.list_by_document(document.id)
        other_ids = [
            r.target_document_id
            if r.source_document_id == document.id
            else r.source_document_id
            for r in relations
        ]
        others = await self._docs.get_many(other_ids)
        for rel in relations:
            other_id = (
                rel.target_document_id
                if rel.source_document_id == document.id
                else rel.source_document_id
            )
            other = others.get(other_id)
            if other is None or other.id == document.id or other.id in merged:
                continue
            label = RELATION_TYPE_LABELS.get(rel.relation_type, rel.relation_type)
            reason = rel.source_label or f"문서 관계: {label}"
            merged[other.id] = (other, "document_relation", rel.relation_type, reason)

        # 2) 승인 related_departments — 관련 부서에 물리 귀속된 문서
        dept_ids = await self._docs.list_related_department_ids(document.id)
        dept_map = await self._orgs.get_nodes_by_ids(dept_ids)
        for dept_id in dept_ids:
            dept = dept_map.get(dept_id)
            if dept is None:
                continue
            for doc in await self._docs.list_active_by_org_node(dept_id):
                if doc.id == document.id or doc.id in merged:
                    continue
                merged[doc.id] = (
                    doc,
                    "related_department",
                    None,
                    f"관련 부서: {dept.name}",
                )

        # 3) related_products 공유 문서
        products = sorted(set(document.related_products or []))
        for doc in await self._docs.list_active_by_products(
            products, exclude_ids=[document.id]
        ):
            if doc.id in merged:
                continue
            shared = sorted(set(doc.related_products or []).intersection(products))
            merged[doc.id] = (
                doc,
                "related_product",
                None,
                f"관련 제품: {', '.join(shared)}",
            )

        return await self._build_related_items(
            user, list(merged.values()), relation_type=relation_type
        )

    async def _build_related_items(
        self,
        user: UserDTO,
        entries: list[tuple[Document, str, str | None, str]],
        *,
        relation_type: str | None,
    ) -> list[RelatedDocumentItem]:
        resolver = await self._path_resolver()
        items: list[RelatedDocumentItem] = []
        for doc, source, rel_type, reason in entries:
            # 권한 없음/unavailable/미승인 문서는 관련 영역에서 제거 (잠금 표시 없음)
            if not self._visible_in_explorer(user, doc):
                continue
            if relation_type is not None and rel_type != relation_type:
                continue
            items.append(
                RelatedDocumentItem(
                    document=doc,
                    path=resolver.resolve(doc),
                    source=source,
                    relation_type=rel_type,
                    match_reason=reason,
                )
            )
        return items

    # ------------------------------------------------------------------
    # 문서 상세 relation — GET /documents/{id}/relations (SPEC-006 U-4)
    # ------------------------------------------------------------------

    async def document_relations(
        self, user: UserDTO, document_id: int
    ) -> list[RelationItem]:
        """approved relation만 — unresolved candidate는 여기 원천에 없다 (DEC-021).

        member에게는 반대편 문서가 권한 없음/unavailable/미승인이면 relation 자체를
        숨긴다. admin은 파생 target_state와 함께 전체를 본다.
        """
        document = await self._get_visible_document(user, document_id)
        relations = await self._relations.list_by_document(document.id)
        endpoint_ids = {r.source_document_id for r in relations} | {
            r.target_document_id for r in relations
        }
        docs = await self._docs.get_many(sorted(endpoint_ids))

        items: list[RelationItem] = []
        for rel in relations:
            source = docs.get(rel.source_document_id)
            target = docs.get(rel.target_document_id)
            other = target if rel.source_document_id == document.id else source
            if not user.is_admin:
                if other is None or not self._visible_in_explorer(user, other):
                    continue
            items.append(
                RelationItem(
                    relation=rel,
                    source_drive_name=source.drive_name if source else None,
                    target_drive_name=target.drive_name if target else None,
                    # broken은 저장하지 않고 target drive_state에서 파생한다
                    target_state=target.drive_state if target else "removed",
                )
            )
        return items

    @staticmethod
    def relation_types() -> list[tuple[str, str]]:
        """v1 enum 4종 (value, 한국어 label)."""
        return [(value, RELATION_TYPE_LABELS[value]) for value in RELATION_TYPES]

    # ------------------------------------------------------------------
    # 통합 검색 — GET /search/documents (SPEC-006 U-3)
    # ------------------------------------------------------------------

    async def search(
        self,
        user: UserDTO,
        query: str,
        *,
        source: str = "all",
        org_node_id: int | None = None,
        limit: int = 50,
    ) -> list[SearchItem]:
        """drive_name/승인 summary 검색 + source_badge.

        badge 기준 context는 선택 조직 노드(org_node_id), 없으면 사용자 부서 노드다.
        context path에 물리 귀속된 문서는 `physical`, 나머지는 `related`.
        """
        text = query.strip()
        if not text:
            return []
        context = org_node_id if org_node_id is not None else user.department_node_id
        documents = await self._docs.search_active(text, limit=limit)
        resolver = await self._path_resolver()

        items: list[SearchItem] = []
        for doc in documents:
            if not self._visible_in_explorer(user, doc):
                continue
            badge = (
                "physical"
                if context is None or context in (doc.organization_path or [])
                else "related"
            )
            if source == "physical" and badge != "physical":
                continue
            if source == "related" and badge != "related":
                continue
            items.append(
                SearchItem(
                    document=doc, path=resolver.resolve(doc), source_badge=badge
                )
            )
        return items

    # ------------------------------------------------------------------
    # 문서 상세 확장 — GET /documents/{id} (SPEC-003 U-1/U-2)
    # ------------------------------------------------------------------

    async def document_detail(
        self, user: UserDTO, document_id: int
    ) -> DocumentDetail:
        """승인 metadata(명칭 join) + admin 전용 pending 후보 badge.

        member는 read policy 불만족/숨김 상태 문서에 접근 불가 —
        DOCUMENT_NOT_READABLE(404 톤)로 존재를 숨긴다.
        """
        document = await self._get_visible_document(user, document_id)

        path: ValidatedPath | None = None
        if document.organization_path:
            resolver = await self._path_resolver()
            path = resolver.resolve(document)

        document_type_name: str | None = None
        if document.document_type_id is not None:
            doc_type = await self._types.get(document.document_type_id)
            document_type_name = doc_type.name if doc_type else None

        dept_ids = await self._docs.list_related_department_ids(document.id)
        dept_map = await self._orgs.get_nodes_by_ids(dept_ids)
        related_departments = [dept_map[i] for i in dept_ids if i in dept_map]

        # 승인 대기 badge는 admin 전용 — member에게 후보 존재를 노출하지 않는다
        pending_candidate: MetadataCandidate | None = None
        if user.is_admin:
            pending = await self._candidates.list_pending_by_document(document.id)
            pending_candidate = pending[0] if pending else None

        return DocumentDetail(
            document=document,
            path=path,
            document_type_name=document_type_name,
            related_departments=related_departments,
            pending_candidate=pending_candidate,
        )
