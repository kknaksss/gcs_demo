"""Mediness user seed 테스트 — WORK-001 Phase 3 (SPEC-001 멱등 upsert / 매핑).

전용 test DB에 seed_users를 직접 실행해 멱등성·조직 매핑·데모 password를 검증한다.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.core.security import verify_password
from app.models.organization import OrganizationNode
from app.models.user import User
from app.repos.users import UserRepository
from app.seeds.users import seed_users
from app.tests.conftest import DEMO_PASSWORD, _Session


async def _count(session, model) -> int:
    return await session.scalar(sa.select(sa.func.count()).select_from(model))


async def test_seed_creates_users_and_org_nodes() -> None:
    async with _Session() as session:
        result = await seed_users(session, DEMO_PASSWORD)
        await session.commit()

        assert result.total == 26
        assert result.created == 26
        assert result.updated == 0
        # 8명은 department null → 미매핑, 18명 매핑.
        assert result.mapped == 18
        assert result.unmapped == 8

        assert await _count(session, User) == 26
        # 회사 root 1 + 부서 노드 8.
        assert await _count(session, OrganizationNode) == 9
        company = await session.scalar(
            sa.select(OrganizationNode).where(OrganizationNode.type == "company")
        )
        assert company is not None and company.parent_id is None


async def test_seed_is_idempotent_on_rerun() -> None:
    async with _Session() as session:
        await seed_users(session, DEMO_PASSWORD)
        await session.commit()

    async with _Session() as session:
        result = await seed_users(session, DEMO_PASSWORD)
        await session.commit()
        assert result.created == 0
        assert result.updated == 26
        assert await _count(session, User) == 26
        assert await _count(session, OrganizationNode) == 9


async def test_demo_password_applied_and_original_credential_absent() -> None:
    async with _Session() as session:
        await seed_users(session, DEMO_PASSWORD)
        await session.commit()

        user = await session.scalar(sa.select(User).limit(1))
        assert user is not None
        # 공통 데모 password로 로그인 가능, 원본 Mediness credential은 반영 안 됨.
        assert verify_password(DEMO_PASSWORD, user.password_hash) is True
        assert verify_password("wrong-password", user.password_hash) is False


async def test_unmapped_users_listed_for_admin_correction() -> None:
    async with _Session() as session:
        await seed_users(session, DEMO_PASSWORD)
        await session.commit()

        unmapped = await UserRepository(session).list_unmapped()
        assert len(unmapped) == 8
        assert all(u.department_node_id is None for u in unmapped)


async def test_mapped_user_points_to_matching_department_node() -> None:
    async with _Session() as session:
        await seed_users(session, DEMO_PASSWORD)
        await session.commit()

        # department='be' user는 'be' 부서 노드로 매핑돼야 한다.
        be_node = await session.scalar(
            sa.select(OrganizationNode).where(OrganizationNode.name == "be")
        )
        assert be_node is not None
        be_user = await session.scalar(
            sa.select(User).where(User.department == "be").limit(1)
        )
        assert be_user is not None
        assert be_user.department_node_id == be_node.id
