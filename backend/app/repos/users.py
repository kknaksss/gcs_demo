"""users repository — 유일하게 SQLAlchemy stmt를 쓰는 계층 (ARCH-001 §4 stmt rule).

인증(email 조회), 세션 복원(id 조회), seed 멱등 upsert(source_user_id 기준),
조직 매핑 보정(미매핑 목록 조회 / node 지정)을 담당한다.
service/router/worker는 이 repo 메서드만 호출한다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.dtos.user import UserDTO
from app.models.user import User

# seed upsert 시 원본 값으로 갱신할 컬럼 (id/source_user_id/password/seeded_at 제외).
_SEED_UPDATABLE = (
    "email",
    "name",
    "role",
    "position",
    "department",
    "active",
    "employment_type",
    "resigned_at",
)


def _to_dto(row: User) -> UserDTO:
    return UserDTO(
        id=row.id,
        source_user_id=row.source_user_id,
        email=row.email,
        name=row.name,
        role=row.role,
        position=row.position,
        department=row.department,
        department_node_id=row.department_node_id,
        team_node_id=row.team_node_id,
        active=row.active,
        employment_type=row.employment_type,
        resigned_at=row.resigned_at,
        password_hash=row.password_hash,
    )


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: int) -> UserDTO | None:
        row = await self._session.get(User, user_id)
        return _to_dto(row) if row is not None else None

    async def get_by_email(self, email: str) -> UserDTO | None:
        row = await self._session.scalar(sa.select(User).where(User.email == email))
        return _to_dto(row) if row is not None else None

    # ------------------------------------------------------------------
    # seed (Phase 3)
    # ------------------------------------------------------------------

    async def upsert_by_source_user_id(
        self,
        *,
        source_user_id: uuid.UUID,
        fields: dict[str, Any],
        password_hash: str | None,
    ) -> tuple[UserDTO, bool]:
        """source_user_id 기준 멱등 upsert. (dto, created) 반환.

        기존 row가 있으면 seed 대상 필드만 갱신하고 password/조직 매핑은 보존한다.
        """
        row = await self._session.scalar(
            sa.select(User).where(User.source_user_id == source_user_id)
        )
        created = row is None
        if row is None:
            row = User(source_user_id=source_user_id, password_hash=password_hash)
            for key in _SEED_UPDATABLE:
                setattr(row, key, fields.get(key))
            self._session.add(row)
        else:
            for key in _SEED_UPDATABLE:
                if key in fields:
                    setattr(row, key, fields[key])
            # 기존 계정에 password가 비어 있으면 데모 password를 채운다(멱등 보정).
            if password_hash is not None and not row.password_hash:
                row.password_hash = password_hash
        await self._session.flush()
        return _to_dto(row), created

    async def set_department_node(
        self,
        user_id: int,
        *,
        department_node_id: int | None,
        team_node_id: int | None = None,
    ) -> UserDTO | None:
        """조직 매핑 보정. user 없으면 None."""
        row = await self._session.get(User, user_id)
        if row is None:
            return None
        row.department_node_id = department_node_id
        row.team_node_id = team_node_id
        await self._session.flush()
        return _to_dto(row)

    async def list_unmapped(self) -> list[UserDTO]:
        """조직 노드 매핑 실패(department_node_id null) user 목록 (admin 보정 대상)."""
        rows = (
            await self._session.scalars(
                sa.select(User)
                .where(User.department_node_id.is_(None))
                .order_by(User.id.asc())
            )
        ).all()
        return [_to_dto(r) for r in rows]

    async def touch_seeded_at(self, user_id: int, when: dt.datetime) -> None:
        row = await self._session.get(User, user_id)
        if row is not None:
            row.seeded_at = when
            await self._session.flush()
