"""documents repository — WORK-002(이관) + WORK-003(Drive mirror 멱등 upsert).

- upsert_mirror: (source_provider, drive_file_id) 기준 멱등. mirror 필드만
  갱신하고 approved metadata 필드는 절대 만지지 않는다 (SPEC-004 Implementation
  Rules — 승인 metadata 불가침).
- 삭제는 drive_state soft delete뿐 (DEC-011). hard delete API를 두지 않는다.
- RBAC 필터 완성판 목록은 WORK-006. 여기는 admin 감사 목록까지만.
stmt는 이 repo 안에서만 (ARCH-001 §4).
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentRelatedDepartment


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, document_id: int) -> Document | None:
        return await self._session.get(Document, document_id)

    async def get_by_drive_file_id(
        self, drive_file_id: str, *, source_provider: str = "google_drive"
    ) -> Document | None:
        return await self._session.scalar(
            sa.select(Document).where(
                Document.source_provider == source_provider,
                Document.drive_file_id == drive_file_id,
            )
        )

    async def upsert_mirror(
        self,
        *,
        drive_file_id: str,
        drive_name: str,
        drive_mime_type: str,
        drive_fingerprint: dict,
        drive_web_url: str | None = None,
        drive_modified_time: dt.datetime | None = None,
        drive_state: str = "active",
        source_provider: str = "google_drive",
    ) -> tuple[Document, bool]:
        """(source_provider, drive_file_id) 멱등 upsert — (document, created).

        기존 row가 있으면 mirror 필드만 덮어쓴다. approved 필드 불가침.
        """
        row = await self.get_by_drive_file_id(
            drive_file_id, source_provider=source_provider
        )
        if row is None:
            row = Document(
                source_provider=source_provider,
                drive_file_id=drive_file_id,
                drive_name=drive_name,
                drive_web_url=drive_web_url,
                drive_mime_type=drive_mime_type,
                drive_state=drive_state,
                drive_modified_time=drive_modified_time,
                drive_fingerprint=drive_fingerprint,
            )
            self._session.add(row)
            await self._session.flush()
            return row, True
        row.drive_name = drive_name
        row.drive_web_url = drive_web_url
        row.drive_mime_type = drive_mime_type
        row.drive_state = drive_state
        row.drive_modified_time = drive_modified_time
        row.drive_fingerprint = drive_fingerprint
        await self._session.flush()
        return row, False

    async def set_drive_state(self, document_id: int, state: str) -> Document | None:
        """soft delete/복구 — drive_state 전환만 수행한다 (hard delete 금지)."""
        row = await self._session.get(Document, document_id)
        if row is None:
            return None
        row.drive_state = state
        await self._session.flush()
        return row

    async def list_admin(
        self,
        *,
        drive_state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Document], int]:
        """admin 감사 목록 — soft deleted 포함, 상태 필터 (SPEC-003 U-1 admin audit)."""
        where = []
        if drive_state is not None:
            where.append(Document.drive_state == drive_state)
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(Document).where(*where)
        )
        rows = await self._session.scalars(
            sa.select(Document)
            .where(*where)
            .order_by(Document.updated_at.desc(), Document.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total or 0)

    async def find_by_drive_name(
        self, drive_name: str, *, exclude_document_id: int | None = None
    ) -> Document | None:
        """wikilink target resolve (WORK-004) — active 문서를 이름으로 조회.

        동명 문서가 여러 개면 최신 갱신 문서를 고른다. 없으면 None —
        새 document row를 만들지 않는다 (DEC-021).
        """
        where = [
            Document.drive_name == drive_name,
            Document.drive_state == "active",
        ]
        if exclude_document_id is not None:
            where.append(Document.id != exclude_document_id)
        return await self._session.scalar(
            sa.select(Document)
            .where(*where)
            .order_by(Document.updated_at.desc(), Document.id.desc())
            .limit(1)
        )

    async def apply_approved_metadata(
        self,
        document_id: int,
        *,
        document_type_id: int,
        created_department_node_id: int | None,
        owning_department_node_id: int,
        organization_path: list[int],
        tree_path: list[int],
        related_products: list[str],
        read_roles: list[str],
        read_departments: list[int],
        read_positions: list[str],
        access_logic: str,
        sensitivity: str,
        policy_preset: str | None,
        summary: str | None,
    ) -> Document | None:
        """승인 확정 metadata 반영 — WORK-005 (SPEC-005 S-1).

        approved 필드만 갱신한다. Drive mirror 필드는 불가침 (SPEC-004
        Implementation Rules).
        """
        row = await self._session.get(Document, document_id)
        if row is None:
            return None
        row.document_type_id = document_type_id
        row.created_department_node_id = created_department_node_id
        row.owning_department_node_id = owning_department_node_id
        row.organization_path = organization_path
        row.tree_path = tree_path
        row.related_products = related_products
        row.read_roles = read_roles
        row.read_departments = read_departments
        row.read_positions = read_positions
        row.access_logic = access_logic
        row.sensitivity = sensitivity
        row.policy_preset = policy_preset
        row.summary = summary
        await self._session.flush()
        return row

    async def replace_related_departments(
        self, document_id: int, node_ids: list[int]
    ) -> None:
        """관련 부서 역방향 index 교체 — 읽기 권한 부여 아님 (DEC-005)."""
        await self._session.execute(
            sa.delete(DocumentRelatedDepartment).where(
                DocumentRelatedDepartment.document_id == document_id
            )
        )
        for node_id in dict.fromkeys(node_ids):  # 중복 제거, 순서 유지
            self._session.add(
                DocumentRelatedDepartment(
                    document_id=document_id, organization_node_id=node_id
                )
            )
        await self._session.flush()

    async def list_related_department_ids(self, document_id: int) -> list[int]:
        rows = await self._session.scalars(
            sa.select(DocumentRelatedDepartment.organization_node_id)
            .where(DocumentRelatedDepartment.document_id == document_id)
            .order_by(DocumentRelatedDepartment.organization_node_id.asc())
        )
        return list(rows)

    # ------------------------------------------------------------------
    # WORK-006 — explorer 조회 (SPEC-002/006). 승인(approved) = organization_path
    # 보유 문서만 explorer 표면에 노출한다. RBAC 필터는 service(explorer) 소관.
    # ------------------------------------------------------------------

    async def get_many(self, document_ids: list[int]) -> dict[int, Document]:
        if not document_ids:
            return {}
        rows = await self._session.scalars(
            sa.select(Document).where(Document.id.in_(document_ids))
        )
        return {row.id: row for row in rows}

    async def list_active_by_org_node(self, org_node_id: int) -> list[Document]:
        """조직 노드에 물리 귀속된 active 문서 (organization_path GIN contains)."""
        rows = await self._session.scalars(
            sa.select(Document)
            .where(
                Document.drive_state == "active",
                Document.organization_path.is_not(None),
                Document.organization_path.contains([org_node_id]),
            )
            .order_by(Document.updated_at.desc(), Document.id.desc())
        )
        return list(rows)

    async def list_active_by_tree_node(
        self, tree_node_id: int, *, org_node_id: int | None = None
    ) -> list[Document]:
        """문서 트리 노드에 물리 귀속된 active 문서 (tree_path GIN contains)."""
        where = [
            Document.drive_state == "active",
            Document.organization_path.is_not(None),
            Document.tree_path.contains([tree_node_id]),
        ]
        if org_node_id is not None:
            where.append(Document.organization_path.contains([org_node_id]))
        rows = await self._session.scalars(
            sa.select(Document)
            .where(*where)
            .order_by(Document.updated_at.desc(), Document.id.desc())
        )
        return list(rows)

    async def list_active_related_to_department(
        self, org_node_id: int
    ) -> list[Document]:
        """해당 부서를 관련 부서로 지정한 active 문서 (역방향 index join)."""
        rows = await self._session.scalars(
            sa.select(Document)
            .join(
                DocumentRelatedDepartment,
                DocumentRelatedDepartment.document_id == Document.id,
            )
            .where(
                DocumentRelatedDepartment.organization_node_id == org_node_id,
                Document.drive_state == "active",
                Document.organization_path.is_not(None),
            )
            .order_by(Document.updated_at.desc(), Document.id.desc())
        )
        return list(rows)

    async def list_active_by_products(
        self, products: list[str], *, exclude_ids: list[int] | None = None
    ) -> list[Document]:
        """related_products가 겹치는 active 문서 (ARRAY overlap)."""
        if not products:
            return []
        where = [
            Document.drive_state == "active",
            Document.organization_path.is_not(None),
            Document.related_products.overlap(products),
        ]
        if exclude_ids:
            where.append(Document.id.not_in(exclude_ids))
        rows = await self._session.scalars(
            sa.select(Document)
            .where(*where)
            .order_by(Document.updated_at.desc(), Document.id.desc())
        )
        return list(rows)

    async def search_active(self, query: str, *, limit: int = 50) -> list[Document]:
        """drive_name/승인 summary ILIKE 검색 — 승인(approved)·active 문서만.

        v1 데모 규모는 단순 ILIKE로 충분하다 (work-006 Open Issues — tsvector 도입
        시 architecture 환류).
        """
        pattern = f"%{query}%"
        rows = await self._session.scalars(
            sa.select(Document)
            .where(
                Document.drive_state == "active",
                Document.organization_path.is_not(None),
                sa.or_(
                    Document.drive_name.ilike(pattern),
                    Document.summary.ilike(pattern),
                ),
            )
            .order_by(Document.updated_at.desc(), Document.id.desc())
            .limit(limit)
        )
        return list(rows)

    async def update_physical_path(
        self,
        document_id: int,
        *,
        organization_path: list[int],
        tree_path: list[int],
        owning_department_node_id: int | None,
    ) -> Document | None:
        row = await self._session.get(Document, document_id)
        if row is None:
            return None
        row.organization_path = organization_path
        row.tree_path = tree_path
        row.owning_department_node_id = owning_department_node_id
        await self._session.flush()
        return row
