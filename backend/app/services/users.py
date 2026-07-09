"""user 관리 service — WORK-001 Phase 3 (조직 매핑 보정, v1 admin tool 범위).

seed 이름 규칙으로 매핑되지 못한 user(department_node_id null)를 admin이 조회하고
조직도 노드를 지정해 보정한다 (SPEC-001 Admin Behavior). 실제 문서 트리 CRUD는 WORK-002.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.dtos.user import UserDTO
from app.repos.organization import OrganizationRepository
from app.repos.users import UserRepository


class UserNotFoundError(Exception):
    """대상 user 없음."""


class OrganizationNodeNotFoundError(Exception):
    """지정한 조직 노드 없음."""


class InvalidOrganizationNodeError(Exception):
    """department/team 이 아닌 노드를 매핑에 지정."""


class UserAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._users = UserRepository(session)
        self._orgs = OrganizationRepository(session)

    async def list_unmapped(self) -> list[UserDTO]:
        return await self._users.list_unmapped()

    async def assign_department(
        self,
        user_id: int,
        *,
        department_node_id: int,
        team_node_id: int | None = None,
    ) -> UserDTO:
        if await self._users.get_by_id(user_id) is None:
            raise UserNotFoundError
        await self._require_node(department_node_id, expected={"department", "team"})
        if team_node_id is not None:
            await self._require_node(team_node_id, expected={"team"})
        dto = await self._users.set_department_node(
            user_id,
            department_node_id=department_node_id,
            team_node_id=team_node_id,
        )
        assert dto is not None  # user 존재는 위에서 확인됨
        return dto

    async def _require_node(self, node_id: int, *, expected: set[str]) -> None:
        node = await self._orgs.get_node(node_id)
        if node is None:
            raise OrganizationNodeNotFoundError
        if node.type not in expected:
            raise InvalidOrganizationNodeError
