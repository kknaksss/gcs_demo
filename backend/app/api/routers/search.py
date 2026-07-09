"""통합 문서 검색 라우트 — WORK-006 Phase 2 (SPEC-006 U-3).

GET /search/documents?q=&source=&org_node_id= — 물리 귀속 + 관련 문서를 함께 찾되
출처(source_badge)를 표시한다. RBAC/state 필터는 BE에서 적용 — 권한 없는 문서는
payload에 포함되지 않는다. 빈 결과는 200 + 빈 배열이며 FE가 `검색 결과가
없습니다.`(SEARCH_EMPTY)를 표시한다.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.dtos.user import UserDTO
from app.schemas.document_tree import PhysicalPathOut
from app.schemas.explorer import SearchDocumentsResponse, SearchResultOut
from app.services.explorer import ExplorerService

router = APIRouter(tags=["search"])


@router.get("/search/documents", response_model=SearchDocumentsResponse)
async def search_documents(
    q: str = Query(default="", description="drive_name/승인 summary 검색어"),
    source: Literal["all", "physical", "related"] = Query(default="all"),
    org_node_id: int | None = Query(
        default=None, description="badge 판정 컨텍스트 (기본: 사용자 부서 노드)"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    user: UserDTO = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> SearchDocumentsResponse:
    items = await ExplorerService(session).search(
        user, q, source=source, org_node_id=org_node_id, limit=limit
    )
    return SearchDocumentsResponse(
        query=q.strip(),
        results=[
            SearchResultOut(
                document_id=i.document.id,
                drive_name=i.document.drive_name,
                physical_tree_path=PhysicalPathOut(
                    organization_path=i.path.organization_path,
                    tree_path=i.path.tree_path,
                    display_path=i.path.display_path,
                    owning_department=i.path.owning_department,
                ),
                source_badge=i.source_badge,  # type: ignore[arg-type]
                relation_type=i.relation_type,  # type: ignore[arg-type]
            )
            for i in items
        ],
        total=len(items),
    )
