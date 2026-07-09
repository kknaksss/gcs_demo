"""문서 트리/카탈로그/이관 service — WORK-002 Phase 1/2 (SPEC-002).

- 문서 트리 노드(work/document_type) CRUD validation.
- validate_active_path(): 승인(WORK-005)과 이관이 공유하는 단일 검증 함수
  (WORK-002 Internal Interface Contract — ORG_NODE_INACTIVE/INVALID_TREE_DEPTH 규칙 단일화).
- reassign(): active path 검증 + 사유 필수 + path 갱신 + history append를
  단일 transaction으로 처리 (DEC-015, append-only).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.dtos.path import ValidatedPath
from app.models.document import Document, DocumentPathHistory
from app.models.organization import DocumentTreeNode, DocumentType
from app.repos.document_tree import DocumentTreeRepository
from app.repos.document_types import DocumentTypeRepository
from app.repos.documents import DocumentRepository
from app.repos.organization import OrganizationRepository
from app.repos.path_history import PathHistoryRepository
from app.services.organization import (
    InvalidTreeDepthError,
    OrgNodeInactiveError,
    OrgNodeNotFoundError,
)

_DISPLAY_SEPARATOR = " / "


class TreeNodeNotFoundError(Exception):
    """TREE_NODE_NOT_FOUND — 문서 트리 노드(또는 참조 카탈로그 항목) 없음."""


class ReassignReasonRequiredError(Exception):
    """REASSIGN_REASON_REQUIRED — 문서 이관 시 변경 사유 필수."""


class DocumentNotReadableError(Exception):
    """DOCUMENT_NOT_READABLE — 문서 없음/비노출 (read policy 표면과 동일 응답)."""


class DocumentTreeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._orgs = OrganizationRepository(session)
        self._tree = DocumentTreeRepository(session)
        self._types = DocumentTypeRepository(session)
        self._docs = DocumentRepository(session)
        self._history = PathHistoryRepository(session)

    # ------------------------------------------------------------------
    # Phase 1 — 트리 설정/카탈로그
    # ------------------------------------------------------------------

    async def get_config(self) -> list[DocumentTreeNode]:
        return await self._tree.list_nodes()

    async def list_document_types(self) -> list[DocumentType]:
        return await self._types.list_all()

    async def create_node(
        self,
        *,
        organization_node_id: int,
        parent_id: int | None,
        type: str,
        document_type_id: int | None,
        name: str,
    ) -> DocumentTreeNode:
        org = await self._orgs.get_node(organization_node_id)
        if org is None:
            raise OrgNodeNotFoundError
        # work/document_type 모두 active 조직 노드 아래에만 (SPEC-002 Validation).
        if org.status != "active":
            raise OrgNodeInactiveError

        if type == "work":
            # v1 트리는 업무 > 문서종류 2단 — work는 조직 노드 바로 아래에만.
            if parent_id is not None:
                raise InvalidTreeDepthError
        else:  # document_type
            catalog = await self._types.get(document_type_id)  # type: ignore[arg-type]
            if catalog is None:
                # 카탈로그 stable id 참조 실패 — Case Matrix에 전용 코드가 없어
                # TREE_NODE_NOT_FOUND로 응답한다 (spec 환류 후보).
                raise TreeNodeNotFoundError
            if parent_id is not None:
                parent = await self._tree.get_node(parent_id)
                if parent is None:
                    raise TreeNodeNotFoundError
                if (
                    parent.type != "work"
                    or parent.organization_node_id != organization_node_id
                ):
                    raise InvalidTreeDepthError
                if parent.status != "active":
                    raise OrgNodeInactiveError

        node = await self._tree.create_node(
            organization_node_id=organization_node_id,
            parent_id=parent_id,
            type=type,
            document_type_id=document_type_id,
            name=name,
        )
        await self._session.commit()
        return node

    async def update_node(
        self,
        node_id: int,
        *,
        name: str | None = None,
        status: str | None = None,
    ) -> DocumentTreeNode:
        node = await self._tree.update_node(node_id, name=name, status=status)
        if node is None:
            raise TreeNodeNotFoundError
        await self._session.commit()
        return node

    # ------------------------------------------------------------------
    # Phase 2 — active path 검증 / 이관 / history
    # ------------------------------------------------------------------

    async def validate_active_path(
        self, organization_path: list[int], tree_path: list[int]
    ) -> ValidatedPath:
        """이관/승인 공용 active path 검증 (Internal Interface Contract).

        - 조직 path: company root부터 parent 사슬로 이어져야 하고 전부 active.
        - 트리 path: 마지막 조직 노드에 부착된 노드들의 parent 사슬, 전부 active.
        """
        if not organization_path:
            raise InvalidTreeDepthError

        org_nodes = []
        for index, node_id in enumerate(organization_path):
            node = await self._orgs.get_node(node_id)
            if node is None:
                raise OrgNodeNotFoundError
            if node.status != "active":
                raise OrgNodeInactiveError
            if index == 0 and node.type != "company":
                raise InvalidTreeDepthError
            expected_parent = None if index == 0 else organization_path[index - 1]
            if node.parent_id != expected_parent:
                raise InvalidTreeDepthError
            org_nodes.append(node)

        tree_nodes = []
        for index, node_id in enumerate(tree_path):
            node = await self._tree.get_node(node_id)
            if node is None:
                raise TreeNodeNotFoundError
            # inactive 트리 노드도 새 귀속 대상 선택 불가 — 규칙 단일화.
            if node.status != "active":
                raise OrgNodeInactiveError
            if node.organization_node_id != organization_path[-1]:
                raise InvalidTreeDepthError
            expected_parent = None if index == 0 else tree_path[index - 1]
            if node.parent_id != expected_parent:
                raise InvalidTreeDepthError
            tree_nodes.append(node)

        owning = next(
            (n for n in reversed(org_nodes) if n.type == "department"), None
        )
        display_path = _DISPLAY_SEPARATOR.join(
            [n.name for n in org_nodes] + [n.name for n in tree_nodes]
        )
        return ValidatedPath(
            organization_path=list(organization_path),
            tree_path=list(tree_path),
            display_path=display_path,
            owning_department_node_id=owning.id if owning else None,
            owning_department=owning.name if owning else None,
        )

    async def describe_path(
        self, organization_path: list[int], tree_path: list[int]
    ) -> ValidatedPath:
        """현재 path 표시용 — active 검증 없이 최신 표시명만 join한다.

        path array는 노드 id 저장이므로 rename 후에도 여기서 항상 최신 이름이
        계산된다 (SPEC-002 Implementation Rules).
        """
        org_map = await self._orgs.get_nodes_by_ids(organization_path)
        tree_map = await self._tree.get_nodes_by_ids(tree_path)
        org_nodes = [org_map[i] for i in organization_path if i in org_map]
        tree_nodes = [tree_map[i] for i in tree_path if i in tree_map]
        owning = next(
            (n for n in reversed(org_nodes) if n.type == "department"), None
        )
        return ValidatedPath(
            organization_path=list(organization_path),
            tree_path=list(tree_path),
            display_path=_DISPLAY_SEPARATOR.join(
                [n.name for n in org_nodes] + [n.name for n in tree_nodes]
            ),
            owning_department_node_id=owning.id if owning else None,
            owning_department=owning.name if owning else None,
        )

    async def reassign(
        self,
        document_id: int,
        *,
        organization_path: list[int],
        tree_path: list[int],
        reason: str,
        changed_by: int,
    ) -> ValidatedPath:
        """physical path 이관 — 갱신 + history append를 단일 transaction으로."""
        document = await self._docs.get(document_id)
        if document is None:
            raise DocumentNotReadableError
        if not reason.strip():
            raise ReassignReasonRequiredError

        validated = await self.validate_active_path(organization_path, tree_path)

        previous = {
            "organization_path": list(document.organization_path or []),
            "tree_path": list(document.tree_path or []),
        }
        new = {
            "organization_path": validated.organization_path,
            "tree_path": validated.tree_path,
        }
        await self._docs.update_physical_path(
            document_id,
            organization_path=validated.organization_path,
            tree_path=validated.tree_path,
            owning_department_node_id=validated.owning_department_node_id,
        )
        await self._history.append(
            document_id=document_id,
            previous_path=previous,
            new_path=new,
            changed_by=changed_by,
            reason=reason.strip(),
        )
        await self._session.commit()
        return validated

    async def path_history(
        self, document_id: int
    ) -> tuple[Document, ValidatedPath | None, list[DocumentPathHistory]]:
        """(document, 현재 path 표시, 이력 최신순). 문서 없으면 DOCUMENT_NOT_READABLE."""
        document = await self._docs.get(document_id)
        if document is None:
            raise DocumentNotReadableError
        current = None
        if document.organization_path:
            current = await self.describe_path(
                list(document.organization_path), list(document.tree_path or [])
            )
        entries = await self._history.list_by_document(document_id)
        return document, current, entries
